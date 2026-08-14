from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import close_database, session_factory
from app.search import (
    hybrid_search_documents,
    search_clients,
    search_lexical_documents,
    search_semantic_documents,
)
from scripts.benchmark_search import percentile, positive_integer
from scripts.performance_data import make_embedding


Operation = Literal["clients", "lexical", "semantic", "hybrid"]


@dataclass(frozen=True, slots=True)
class DatabaseCase:
    name: str
    operation: Operation
    query: str
    expected_client_email: str | None = None
    expected_document_title: str | None = None
    expected_document_prefix: str | None = None
    expect_empty: bool = False


CASES = (
    DatabaseCase(
        name="client_exact_email",
        operation="clients",
        query="perf+baseline10k.00420@performance-test.nevis.dev",
        expected_client_email=(
            "perf+baseline10k.00420@performance-test.nevis.dev"
        ),
    ),
    DatabaseCase(
        name="client_typo",
        operation="clients",
        query="Performnce00420 Client",
        expected_client_email=(
            "perf+baseline10k.00420@performance-test.nevis.dev"
        ),
    ),
    DatabaseCase(
        name="semantic_chunks",
        operation="semantic",
        query="retirement",
        expected_document_prefix="Retirement Portfolio",
    ),
    DatabaseCase(
        name="document_title_exact",
        operation="lexical",
        query="Retirement Portfolio 00420-00",
        expected_document_title="Retirement Portfolio 00420-00",
    ),
    DatabaseCase(
        name="document_title_typo",
        operation="lexical",
        query="Retiremnt Portfolio 00420-00",
        expected_document_title="Retirement Portfolio 00420-00",
    ),
    DatabaseCase(
        name="document_content_fts",
        operation="lexical",
        query="pensions inflation withdrawals",
        expected_document_prefix="Retirement Portfolio",
    ),
    DatabaseCase(
        name="web_query_syntax",
        operation="lexical",
        query='"government bonds" retirement',
        expected_document_prefix="Retirement Portfolio",
    ),
    DatabaseCase(
        name="hybrid_document",
        operation="hybrid",
        query="retirement pension inflation",
        expected_document_prefix="Retirement Portfolio",
    ),
    DatabaseCase(
        name="lexical_no_match",
        operation="lexical",
        query="zxqvnotfound",
        expect_empty=True,
    ),
)


async def execute_case(
    session: AsyncSession,
    case: DatabaseCase,
    limit: int,
) -> tuple[float, str | None]:
    started = time.perf_counter()
    if case.operation == "clients":
        results = await search_clients(session, case.query, limit)
    elif case.operation == "lexical":
        results = await search_lexical_documents(session, case.query, 100)
    elif case.operation == "semantic":
        results = await search_semantic_documents(
            session,
            make_embedding(0, 0, 0, 0),
            100,
        )
    else:
        results = await hybrid_search_documents(
            session,
            case.query,
            make_embedding(0, 0, 0, 0),
            limit,
        )
    latency_ms = (time.perf_counter() - started) * 1_000

    if case.expect_empty and results:
        return latency_ms, "expected no results"
    if case.expected_client_email and not any(
        match.client.email == case.expected_client_email for match in results
    ):
        return latency_ms, "expected client not found"
    if case.expected_document_title and not any(
        match.document.title == case.expected_document_title
        for match in results
    ):
        return latency_ms, "expected document not found"
    if case.expected_document_prefix and not any(
        match.document.title.startswith(case.expected_document_prefix)
        for match in results
    ):
        return latency_ms, "expected document prefix not found"
    return latency_ms, None


async def run_benchmark(args: argparse.Namespace) -> None:
    print(
        f"Database iterations: {args.iterations}; "
        f"warmups: {args.warmup_iterations}; limit: {args.limit}"
    )
    print(
        f"{'case':<24} {'n':>4} {'valid':>8} {'mean_ms':>9} "
        f"{'p50_ms':>8} {'p95_ms':>8} {'max_ms':>8}"
    )

    total_valid = 0
    total_requests = 0
    async with session_factory() as session:
        for case in CASES:
            for _ in range(args.warmup_iterations):
                await execute_case(session, case, args.limit)

            latencies = []
            errors = []
            for _ in range(args.iterations):
                latency_ms, error = await execute_case(
                    session,
                    case,
                    args.limit,
                )
                latencies.append(latency_ms)
                if error:
                    errors.append(error)

            valid_count = len(latencies) - len(errors)
            total_valid += valid_count
            total_requests += len(latencies)
            print(
                f"{case.name:<24} {len(latencies):>4} "
                f"{100 * valid_count / len(latencies):>7.1f}% "
                f"{statistics.mean(latencies):>9.1f} "
                f"{percentile(latencies, 0.50):>8.1f} "
                f"{percentile(latencies, 0.95):>8.1f} "
                f"{max(latencies):>8.1f}"
            )
            for error in sorted(set(errors)):
                print(f"  error: {error}")

    print(
        f"Overall reliability: {total_valid}/{total_requests} "
        f"({100 * total_valid / total_requests:.1f}%)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark PostgreSQL search without HTTP or OpenAI."
    )
    parser.add_argument("--iterations", type=positive_integer, default=30)
    parser.add_argument("--warmup-iterations", type=int, default=2)
    parser.add_argument("--limit", type=positive_integer, default=10)
    return parser


async def main() -> None:
    try:
        await run_benchmark(build_parser().parse_args())
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
