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

## Running Tests

To be documented during implementation.

## AWS Deployment

Deployment instructions and the demonstration endpoint will be added after the
local implementation is complete.

## Contributing

Keep changes focused, add tests for behavioural changes, and document material
architecture decisions as ADRs.
