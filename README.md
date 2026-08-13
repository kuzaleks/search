# Nevis Search API

A Python API for searching Nevis clients and their documents using lexical and
semantic search.

> Status: implementation in progress.

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
is not included in the image.

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

## Running Tests

The unit tests use fake embeddings and do not require an OpenAI API key:

```bash
python -m unittest discover -s tests
```

## AWS Deployment

Deployment instructions and the demonstration endpoint will be added after the
local implementation is complete.

## Contributing

Keep changes focused, add tests for behavioural changes, and document material
architecture decisions as ADRs.
