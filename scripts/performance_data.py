from __future__ import annotations

import argparse
import asyncio
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from pgvector.asyncpg import register_vector

from app.chunking import chunk_text
from app.config import EMBEDDING_DIMENSIONS, get_settings


PERFORMANCE_DOMAIN = "performance-test.nevis.dev"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9]+$")
DEFAULT_CLIENTS = 1_000
DEFAULT_DOCUMENTS_PER_CLIENT = 10
DEFAULT_DOCUMENT_SIZE_BYTES = 10_000
DEFAULT_BATCH_CLIENTS = 20
SYNTHETIC_VOCABULARY = tuple(f"w{index:04d}" for index in range(10_000))


@dataclass(frozen=True, slots=True)
class Topic:
    title: str
    paragraph: str


TOPICS = (
    Topic(
        "Retirement Portfolio",
        "Retirement planning balances pension income, diversified equities, "
        "government bonds, inflation protection, and sustainable withdrawals.",
    ),
    Topic(
        "Property Investment",
        "Property analysis compares rental yield, mortgage rates, maintenance "
        "costs, occupancy, location, taxation, and long-term capital growth.",
    ),
    Topic(
        "Estate Planning",
        "Estate planning records beneficiaries, inheritance wishes, trusts, "
        "executors, gifts, tax allowances, and family protection arrangements.",
    ),
    Topic(
        "Insurance Review",
        "Insurance review covers life assurance, income protection, critical "
        "illness, premiums, exclusions, beneficiaries, and coverage limits.",
    ),
    Topic(
        "Education Savings",
        "Education savings forecasts tuition, living costs, scholarships, "
        "monthly contributions, investment growth, and withdrawal schedules.",
    ),
    Topic(
        "Sustainable Investing",
        "Sustainable investing evaluates emissions, governance, renewable "
        "energy, social impact, exclusions, diversification, and fund charges.",
    ),
    Topic(
        "Tax Planning",
        "Tax planning considers annual allowances, capital gains, dividends, "
        "pensions, charitable gifts, reporting deadlines, and expected income.",
    ),
    Topic(
        "Emergency Reserve",
        "Emergency reserve guidance estimates essential spending, liquidity, "
        "deposit protection, interest rates, accessibility, and target savings.",
    ),
    Topic(
        "Business Succession",
        "Business succession planning addresses ownership transfer, valuation, "
        "shareholder agreements, key staff, continuity, funding, and taxation.",
    ),
    Topic(
        "Travel Budget",
        "Travel budgeting tracks transport, accommodation, meals, insurance, "
        "exchange rates, activities, contingency funds, and payment schedules.",
    ),
)


def database_dsn() -> str:
    return get_settings().database_url.replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )


def validate_run_id(value: str) -> str:
    run_id = value.lower()
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise argparse.ArgumentTypeError(
            "run ID must contain only lowercase letters and numbers"
        )
    return run_id


def default_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dt%H%M%S")


def performance_email(run_id: str, client_index: int) -> str:
    return (
        f"perf+{run_id}.{client_index:05d}@{PERFORMANCE_DOMAIN}"
    )


def make_document_content(
    topic: Topic,
    client_index: int,
    document_index: int,
    target_bytes: int,
) -> str:
    prefix = (
        f"Synthetic performance document for client {client_index}, record "
        f"{document_index}. {topic.paragraph} "
    )
    random_source = random.Random(
        client_index * 10_007 + document_index * 101
    )
    token_count = max(1, ((target_bytes - len(prefix)) // 6) + 2)
    payload = " ".join(
        random_source.choices(SYNTHETIC_VOCABULARY, k=token_count)
    )
    return (prefix + payload)[:target_bytes]


def make_embedding(
    topic_index: int,
    client_index: int,
    document_index: int,
    chunk_index: int,
) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[topic_index] = 1.0
    seed = (
        client_index * 1_009
        + document_index * 101
        + chunk_index * 17
    )
    for offset, weight in enumerate((0.08, 0.06, 0.04, 0.02), start=1):
        dimension = len(TOPICS) + ((seed + offset * 97) % 502)
        vector[dimension] = weight
    return vector


async def connect() -> asyncpg.Connection:
    connection = await asyncpg.connect(database_dsn())
    await register_vector(connection)
    return connection


async def run_exists(connection: asyncpg.Connection, run_id: str) -> bool:
    return bool(
        await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM clients
                WHERE email LIKE $1
            )
            """,
            f"perf+{run_id}.%@{PERFORMANCE_DOMAIN}",
        )
    )


async def load_data(args: argparse.Namespace) -> None:
    connection = await connect()
    started = time.monotonic()
    total_documents = args.clients * args.documents_per_client
    total_chunks = 0

    try:
        if await run_exists(connection, args.run_id):
            raise RuntimeError(
                f"performance run '{args.run_id}' already exists"
            )

        print(
            f"Loading run {args.run_id}: {args.clients:,} clients, "
            f"{total_documents:,} documents, "
            f"{args.document_size_bytes:,} bytes per document"
        )

        for batch_start in range(0, args.clients, args.batch_clients):
            batch_end = min(batch_start + args.batch_clients, args.clients)
            client_rows: list[tuple[Any, ...]] = []
            document_rows: list[tuple[Any, ...]] = []
            chunk_rows: list[tuple[Any, ...]] = []

            for client_index in range(batch_start, batch_end):
                client_id = uuid4()
                client_rows.append(
                    (
                        client_id,
                        f"Performance{client_index:05d}",
                        "Client",
                        performance_email(args.run_id, client_index),
                        f"[performance-test:{args.run_id}] Synthetic client",
                        [],
                    )
                )

                for document_index in range(args.documents_per_client):
                    document_id = uuid4()
                    topic_index = document_index % len(TOPICS)
                    topic = TOPICS[topic_index]
                    content = make_document_content(
                        topic,
                        client_index,
                        document_index,
                        args.document_size_bytes,
                    )
                    document_rows.append(
                        (
                            document_id,
                            client_id,
                            (
                                f"{topic.title} {client_index:05d}-"
                                f"{document_index:02d}"
                            ),
                            content,
                        )
                    )

                    for chunk_index, chunk in enumerate(chunk_text(content)):
                        chunk_rows.append(
                            (
                                uuid4(),
                                document_id,
                                chunk_index,
                                chunk.start_offset,
                                chunk.end_offset,
                                make_embedding(
                                    topic_index,
                                    client_index,
                                    document_index,
                                    chunk_index,
                                ),
                                chunk.text,
                            )
                        )

            async with connection.transaction():
                await connection.copy_records_to_table(
                    "clients",
                    records=client_rows,
                    columns=(
                        "id",
                        "first_name",
                        "last_name",
                        "email",
                        "description",
                        "social_links",
                    ),
                )
                await connection.copy_records_to_table(
                    "documents",
                    records=document_rows,
                    columns=("id", "client_id", "title", "content"),
                )
                await connection.executemany(
                    """
                    INSERT INTO document_chunks (
                        id,
                        document_id,
                        chunk_index,
                        start_offset,
                        end_offset,
                        embedding,
                        search_vector
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, to_tsvector('english', $7))
                    """,
                    chunk_rows,
                )

            total_chunks += len(chunk_rows)
            elapsed = time.monotonic() - started
            print(
                f"  clients {batch_end:,}/{args.clients:,}; "
                f"chunks {total_chunks:,}; elapsed {elapsed:.1f}s"
            )

        await connection.execute("ANALYZE clients")
        await connection.execute("ANALYZE documents")
        await connection.execute("ANALYZE document_chunks")
    except Exception:
        print(
            "Load stopped. Any committed batches remain tagged and can be "
            f"removed with: delete --run-id {args.run_id}"
        )
        raise
    finally:
        await connection.close()

    elapsed = time.monotonic() - started
    print(
        f"Loaded run {args.run_id}: {args.clients:,} clients, "
        f"{total_documents:,} documents, {total_chunks:,} chunks in "
        f"{elapsed:.1f}s"
    )


async def fetch_run_counts(
    connection: asyncpg.Connection,
    where_clause: str,
    parameter: str | None = None,
) -> asyncpg.Record:
    query = f"""
        WITH selected_clients AS (
            SELECT client.id
            FROM clients AS client
            WHERE {where_clause}
        ),
        selected_documents AS (
            SELECT document.id, document.content
            FROM documents AS document
            JOIN selected_clients AS client
              ON client.id = document.client_id
        )
        SELECT
            (SELECT count(*) FROM selected_clients) AS clients,
            (SELECT count(*) FROM selected_documents) AS documents,
            (
                SELECT count(*)
                FROM document_chunks AS chunk
                JOIN selected_documents AS document
                  ON document.id = chunk.document_id
            ) AS chunks,
            coalesce(
                (SELECT sum(octet_length(content)) FROM selected_documents),
                0
            ) AS content_bytes
    """
    if parameter is None:
        return await connection.fetchrow(query)
    return await connection.fetchrow(query, parameter)


def print_counts(label: str, counts: asyncpg.Record) -> None:
    print(
        f"{label}: {counts['clients']:,} clients, "
        f"{counts['documents']:,} documents, {counts['chunks']:,} chunks, "
        f"{counts['content_bytes'] / 1_000_000:.1f} MB raw content"
    )


async def show_status(_: argparse.Namespace) -> None:
    connection = await connect()
    try:
        rows = await connection.fetch(
            f"""
            SELECT
                split_part(split_part(email, '+', 2), '.', 1) AS run_id,
                count(*) AS clients
            FROM clients
            WHERE email LIKE 'perf+%@{PERFORMANCE_DOMAIN}'
            GROUP BY run_id
            ORDER BY run_id
            """
        )
        if not rows:
            print("No performance test runs found.")
        for row in rows:
            counts = await fetch_run_counts(
                connection,
                f"client.email LIKE $1",
                f"perf+{row['run_id']}.%@{PERFORMANCE_DOMAIN}",
            )
            print_counts(f"Run {row['run_id']}", counts)

        sizes = await connection.fetchrow(
            """
            SELECT
                pg_database_size(current_database()) AS database_bytes,
                pg_total_relation_size('clients') AS clients_bytes,
                pg_total_relation_size('documents') AS documents_bytes,
                pg_total_relation_size('document_chunks') AS chunks_bytes
            """
        )
        print(
            "Database storage: "
            f"{sizes['database_bytes'] / 1_000_000:.1f} MB total; "
            f"clients {sizes['clients_bytes'] / 1_000_000:.1f} MB; "
            f"documents {sizes['documents_bytes'] / 1_000_000:.1f} MB; "
            f"chunks {sizes['chunks_bytes'] / 1_000_000:.1f} MB"
        )
    finally:
        await connection.close()


async def delete_data(args: argparse.Namespace) -> None:
    connection = await connect()
    try:
        if args.all_runs:
            where_clause = f"client.email LIKE 'perf+%@{PERFORMANCE_DOMAIN}'"
            delete_query = (
                f"DELETE FROM clients WHERE email LIKE "
                f"'perf+%@{PERFORMANCE_DOMAIN}'"
            )
            parameter = None
            label = "All performance runs"
        else:
            where_clause = "client.email LIKE $1"
            parameter = f"perf+{args.run_id}.%@{PERFORMANCE_DOMAIN}"
            delete_query = "DELETE FROM clients WHERE email LIKE $1"
            label = f"Run {args.run_id}"

        counts = await fetch_run_counts(
            connection,
            where_clause,
            parameter,
        )
        print_counts(label, counts)
        if counts["clients"] == 0:
            print("Nothing to delete.")
            return

        if parameter is None:
            result = await connection.execute(delete_query)
        else:
            result = await connection.execute(delete_query, parameter)
        print(f"Deleted {result.split()[-1]} clients; documents cascaded.")

        if args.compact:
            print("Compacting tables; the API should remain stopped...")
            for table in ("document_chunks", "documents", "clients"):
                await connection.execute(f"VACUUM (FULL, ANALYZE) {table}")
            await connection.execute("CHECKPOINT")
            print("Compaction complete; unused files were returned to Docker.")
        else:
            for table in ("document_chunks", "documents", "clients"):
                await connection.execute(f"VACUUM (ANALYZE) {table}")
            print(
                "Space is reusable by PostgreSQL. Add --compact to return "
                "unused table files to Docker."
            )
    finally:
        await connection.close()


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage locally generated search performance data."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    load_parser = subparsers.add_parser("load", help="Generate tagged data")
    load_parser.add_argument(
        "--run-id",
        type=validate_run_id,
        default=default_run_id(),
    )
    load_parser.add_argument(
        "--clients",
        type=positive_integer,
        default=DEFAULT_CLIENTS,
    )
    load_parser.add_argument(
        "--documents-per-client",
        type=positive_integer,
        default=DEFAULT_DOCUMENTS_PER_CLIENT,
    )
    load_parser.add_argument(
        "--document-size-bytes",
        type=positive_integer,
        default=DEFAULT_DOCUMENT_SIZE_BYTES,
    )
    load_parser.add_argument(
        "--batch-clients",
        type=positive_integer,
        default=DEFAULT_BATCH_CLIENTS,
    )
    load_parser.set_defaults(handler=load_data)

    status_parser = subparsers.add_parser(
        "status",
        help="Show generated runs and relation sizes",
    )
    status_parser.set_defaults(handler=show_status)

    delete_parser = subparsers.add_parser(
        "delete",
        help="Delete generated data",
    )
    target = delete_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-id", type=validate_run_id)
    target.add_argument("--all", dest="all_runs", action="store_true")
    delete_parser.add_argument(
        "--compact",
        action="store_true",
        help="Lock and rewrite tables to return disk space to Docker",
    )
    delete_parser.set_defaults(handler=delete_data, all_runs=False)

    return parser


async def main() -> None:
    args = build_parser().parse_args()
    await args.handler(args)


if __name__ == "__main__":
    asyncio.run(main())
