from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import time
from dataclasses import dataclass
from typing import Any

from httpx import AsyncClient, HTTPError, Response, Timeout


@dataclass(frozen=True, slots=True)
class SearchCase:
    name: str
    query: str
    expected_client_email: str | None = None
    expected_document_title: str | None = None
    expected_document_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    latency_ms: float
    valid: bool
    error: str | None = None


CASES = (
    SearchCase(
        name="client_exact_email",
        query="perf+baseline10k.00420@performance-test.nevis.dev",
        expected_client_email=(
            "perf+baseline10k.00420@performance-test.nevis.dev"
        ),
    ),
    SearchCase(
        name="client_typo",
        query="Performnce00420 Client",
        expected_client_email=(
            "perf+baseline10k.00420@performance-test.nevis.dev"
        ),
    ),
    SearchCase(
        name="document_title_exact",
        query="Retirement Portfolio 00420-00",
        expected_document_title="Retirement Portfolio 00420-00",
    ),
    SearchCase(
        name="document_title_typo",
        query="Retiremnt Portfolio 00420-00",
        expected_document_title="Retirement Portfolio 00420-00",
    ),
    SearchCase(
        name="document_content_fts",
        query="pensions inflation withdrawals",
        expected_document_prefix="Retirement Portfolio",
    ),
    SearchCase(
        name="web_query_syntax",
        query='"government bonds" retirement',
        expected_document_prefix="Retirement Portfolio",
    ),
    SearchCase(
        name="hybrid_document",
        query="retirement pension inflation",
        expected_document_prefix="Retirement Portfolio",
    ),
    SearchCase(
        name="no_lexical_match",
        query="zxqvnotfound",
    ),
)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def validate_response(case: SearchCase, response: Response) -> str | None:
    if response.status_code != 200:
        return f"HTTP {response.status_code}: {response.text[:160]}"

    try:
        body: dict[str, Any] = response.json()
        clients = body["clients"]
        documents = body["documents"]
    except (TypeError, ValueError, KeyError) as error:
        return f"invalid response schema: {error}"

    if body.get("query") != case.query:
        return "response query does not match request"

    if case.expected_client_email and not any(
        result.get("client", {}).get("email") == case.expected_client_email
        for result in clients
    ):
        return f"expected client not found: {case.expected_client_email}"

    if case.expected_document_title and not any(
        result.get("document", {}).get("title")
        == case.expected_document_title
        for result in documents
    ):
        return f"expected document not found: {case.expected_document_title}"

    if case.expected_document_prefix and not any(
        result.get("document", {}).get("title", "").startswith(
            case.expected_document_prefix
        )
        for result in documents
    ):
        return (
            "expected document prefix not found: "
            f"{case.expected_document_prefix}"
        )

    return None


async def execute_case(
    client: AsyncClient,
    case: SearchCase,
    limit: int,
    semaphore: asyncio.Semaphore,
) -> Observation:
    async with semaphore:
        started = time.perf_counter()
        try:
            response = await client.get(
                "/search",
                params={"q": case.query, "limit": limit},
            )
            error = validate_response(case, response)
        except HTTPError as request_error:
            detail = str(request_error) or request_error.__class__.__name__
            error = f"request failed: {detail}"
        latency_ms = (time.perf_counter() - started) * 1_000

    return Observation(
        latency_ms=latency_ms,
        valid=error is None,
        error=error,
    )


def print_result(
    case: SearchCase,
    observations: list[Observation],
    wall_seconds: float,
) -> None:
    latencies = [observation.latency_ms for observation in observations]
    valid_count = sum(observation.valid for observation in observations)
    reliability = 100 * valid_count / len(observations)
    throughput = len(observations) / wall_seconds
    print(
        f"{case.name:<24} {len(observations):>4} "
        f"{reliability:>7.1f}% "
        f"{statistics.mean(latencies):>9.1f} "
        f"{percentile(latencies, 0.50):>8.1f} "
        f"{percentile(latencies, 0.95):>8.1f} "
        f"{max(latencies):>8.1f} "
        f"{throughput:>8.2f}"
    )

    errors = sorted(
        {observation.error for observation in observations if observation.error}
    )
    for error in errors[:3]:
        print(f"  error: {error}")


async def run_benchmark(args: argparse.Namespace) -> None:
    timeout = Timeout(args.timeout_seconds)
    semaphore = asyncio.Semaphore(args.concurrency)
    async with AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=timeout,
    ) as client:
        health = await client.get("/ready")
        health.raise_for_status()

        print(
            f"Target: {args.base_url}; iterations: {args.iterations}; "
            f"concurrency: {args.concurrency}; limit: {args.limit}"
        )
        print(
            f"{'case':<24} {'n':>4} {'valid':>8} {'mean_ms':>9} "
            f"{'p50_ms':>8} {'p95_ms':>8} {'max_ms':>8} {'req/s':>8}"
        )

        total_valid = 0
        total_requests = 0
        for case in CASES:
            for _ in range(args.warmup_iterations):
                await execute_case(client, case, args.limit, semaphore)

            started = time.perf_counter()
            observations = await asyncio.gather(
                *(
                    execute_case(client, case, args.limit, semaphore)
                    for _ in range(args.iterations)
                )
            )
            wall_seconds = time.perf_counter() - started
            print_result(case, observations, wall_seconds)
            total_valid += sum(item.valid for item in observations)
            total_requests += len(observations)

        print(
            f"Overall reliability: {total_valid}/{total_requests} "
            f"({100 * total_valid / total_requests:.1f}%)"
        )


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark end-to-end search latency and reliability."
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--iterations", type=positive_integer, default=10)
    parser.add_argument("--warmup-iterations", type=int, default=1)
    parser.add_argument("--concurrency", type=positive_integer, default=1)
    parser.add_argument("--limit", type=positive_integer, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
