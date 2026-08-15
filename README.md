# Nevis Search API

A Python API for searching Nevis clients and their documents using lexical and
semantic search.

> Status: implementation in progress.

## Live Demo

The temporary interview deployment is available at:

- API documentation: <http://18.133.142.221/docs>
- Readiness check: <http://18.133.142.221/ready>

This unauthenticated HTTP deployment is intentionally short-lived and may be
removed after the interview review window.

## Overview

The service will support:

- Creating clients.
- Adding documents to clients.
- Searching client names, emails, and descriptions.
- Finding documents through exact and semantically similar terms.
- Returning results ordered by relevance.

## Architecture

The planned implementation uses FastAPI, PostgreSQL full-text search,
`pg_trgm`, `pgvector`, and an external embedding provider.

Architecture decisions:

- [ADR-001: Search Service Architecture](./ADR-001-search-service-architecture.md)
- [ADR-002: Use PostgreSQL for Search](./ADR-002-use-postgresql-for-search.md)
- [ADR-003: Design for AWS Deployment](./ADR-003-design-for-aws-deployment.md)
- [ADR-004: Use an Asynchronous Application Runtime](./ADR-004-use-an-asynchronous-application-runtime.md)
- [ADR-005: Optimize Search Candidate Retrieval](./ADR-005-optimize-search-candidate-retrieval.md)

Manual verification:

- [Search Features and Manual Test Guide](./SEARCH_FEATURES.md)
- [Search Performance Baseline](./PERFORMANCE_RESULTS.md)

Deployment:

- [Temporary EC2 Demo Deployment](./EC2_DEPLOYMENT.md)

## Prerequisites

- Python 3.13 or newer.
- Docker with Docker Compose for the complete local stack.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env`. Document ingestion uses
`text-embedding-3-small`; the key is passed to the API container at runtime and
is not included in the image. `EMBEDDING_TIMEOUT_SECONDS` bounds the complete
provider operation, including retries, and `EMBEDDING_MAX_RETRIES` controls the
OpenAI SDK retry count.

## Running the API

Start PostgreSQL, apply migrations, and run the API:

```bash
docker compose up --build
```

The service is available at `http://localhost:8000`, with interactive API
documentation at `http://localhost:8000/docs`.

Health endpoints:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

To run the API process directly while keeping PostgreSQL in Docker:

```bash
docker compose up -d database
alembic upgrade head
uvicorn app.main:app --reload
```

Create a new migration after changing SQLAlchemy models:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

## API Usage

Create a client:

```bash
curl -i -X POST http://localhost:8000/clients \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "John",
    "last_name": "Doe",
    "email": "john.doe@neviswealth.com",
    "description": "Wealth management client",
    "social_links": ["https://www.linkedin.com/in/john-doe"]
  }'
```

The endpoint returns `201 Created`:

```json
{
  "id": "e936cab6-800f-45f9-994a-2c0d7da522b3",
  "first_name": "John",
  "last_name": "Doe",
  "email": "john.doe@neviswealth.com",
  "description": "Wealth management client",
  "social_links": ["https://www.linkedin.com/in/john-doe"]
}
```

Emails are normalized to lowercase and must be unique regardless of case. A
duplicate returns `409 Conflict`.

Add a document to a client:

```bash
curl -i -X POST \
  http://localhost:8000/clients/CLIENT_ID/documents \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Electricity statement",
    "content": "Account holder John Doe. Service address: 10 High Street."
  }'
```

The endpoint validates that the client exists, splits the content into
overlapping chunks, generates their embeddings in one request, and stores the
document with chunk offsets and embeddings in one database transaction. Chunk
text is reconstructed from the original document when needed rather than being
stored twice. The endpoint returns `404` for an unknown client, `503` when the
embedding provider is not configured, and `502` when embedding generation
fails.

Search clients and documents:

```bash
curl "http://localhost:8000/search?q=address%20proof&limit=10"
```

The response keeps lexical client scores and semantic document scores in
separate ranked collections because the two scores are not directly
comparable:

```json
{
  "query": "address proof",
  "clients": [],
  "documents": [
    {
      "score": 0.41,
      "document": {
        "id": "...",
        "client_id": "...",
        "title": "Electricity statement",
        "created_at": "2026-08-13T19:00:00Z"
      },
      "snippet": "Electricity utility bill for John Doe..."
    }
  ]
}
```

Client search supports case-insensitive exact, substring, and typo-tolerant
trigram matches across names, email, and description. Document search combines
English full-text matches over titles and chunk content with cosine similarity
over chunk embeddings. Title matches receive additional lexical weight, and
reciprocal-rank fusion combines lexical and semantic rankings without treating
their raw scores as comparable. Snippets are reconstructed from chunk offsets,
centered on matching lexical terms when possible, and limited to approximately
320 characters. `limit` defaults to 10 and accepts values from 1 to 50 for each
result collection.

### Error Responses

All errors use one JSON envelope with a stable machine-readable code and a
human-readable message:

```json
{
  "error": {
    "code": "client_not_found",
    "message": "Client not found"
  }
}
```

Validation failures also include field-level details. Unexpected failures are
logged by the API but return only the generic `internal_server_error` response;
internal exception details are not exposed to clients. The complete operation,
parameter, model, and error-response documentation is available in Swagger UI
at `http://localhost:8000/docs` or as JSON at
`http://localhost:8000/openapi.json`.

## Running Tests

The unit tests use fake embeddings and do not require an OpenAI API key:

```bash
python -m unittest discover -s tests
```

PostgreSQL integration tests are opt-in, use the configured local database, and
do not require an OpenAI API key. Start PostgreSQL, then enable them explicitly:

```bash
docker compose up -d database
RUN_POSTGRES_TESTS=1 python -m unittest discover \
  -s tests/integration \
  -v
```

Each integration test runs in its own outer transaction and rolls back all
fixtures. The suite refuses to run when `ENVIRONMENT=production`. It verifies
the required extensions and indexes, client exact and typo matching, title and
content FTS, stemming, web-query syntax, semantic nearest-chunk retrieval,
document deduplication, hybrid ranking, snippets, and the complete FastAPI
search response using a fake embedding provider.

## Performance Test Data

The performance-data utility bulk-loads tagged synthetic records directly into
PostgreSQL. It creates local 512-dimensional topic vectors, so loading does not
call OpenAI. These vectors exercise vector storage and HNSW search performance,
but they are not suitable for evaluating semantic relevance against OpenAI
query embeddings.

Keep the API stopped during large loads and start PostgreSQL:

```bash
docker compose stop api
docker compose up -d database
```

Create the baseline run of 1,000 clients, 10 documents per client, and 10,000
bytes per document:

```bash
.venv/bin/python -m scripts.performance_data load \
  --run-id baseline10k \
  --clients 1000 \
  --documents-per-client 10 \
  --document-size-bytes 10000
```

The run ID is encoded in generated addresses under the dedicated
`performance-test.nevis.dev` domain. Inspect runs, row counts, and relation
sizes:

```bash
.venv/bin/python -m scripts.performance_data status
```

Delete one run through the existing client-document cascade:

```bash
.venv/bin/python -m scripts.performance_data delete --run-id baseline10k
```

Normal deletion makes the space reusable by PostgreSQL. Add `--compact` to
rewrite and lock the affected tables with `VACUUM FULL`, returning unused table
files to Docker:

```bash
.venv/bin/python -m scripts.performance_data delete \
  --run-id baseline10k \
  --compact
```

Delete and compact every tagged performance run with:

```bash
.venv/bin/python -m scripts.performance_data delete --all --compact
```

Run the end-to-end and database-only benchmarks with:

```bash
.venv/bin/python -m scripts.benchmark_search
.venv/bin/python -m scripts.benchmark_database
```

## AWS Deployment

Deployment instructions and the demonstration endpoint will be added after the
local implementation is complete.

## Contributing

Keep changes focused, add tests for behavioural changes, and document material
architecture decisions as ADRs.
