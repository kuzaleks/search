# ADR-004: Use an Asynchronous Application Runtime

- Status: Accepted
- Date: 2026-08-12

## Context

The API waits on PostgreSQL and an external embedding provider. Uvicorn already
runs an event loop, but that loop can process another request only when endpoint
code returns control to it. PostgreSQL supports concurrent connections, but the
application must issue work on those connections without blocking the event-loop
thread.

## Decision

Use an asynchronous stack throughout the request path:

- FastAPI `async def` endpoints.
- SQLAlchemy `AsyncSession` with the `asyncpg` driver.
- An asynchronous HTTP client for the embedding provider.
- One database session per request or unit of work.

An endpoint will `await` database and HTTP operations. This pauses that endpoint,
not the server, allowing Uvicorn's existing event loop to run other requests.
SQLAlchemy's connection pool supplies separate PostgreSQL connections for
concurrent database work.

## Alternatives Considered

A synchronous stack is also valid: FastAPI can run normal `def` endpoints with
a synchronous database driver in a worker thread pool. It has a simpler mental
model, but each waiting operation occupies a worker thread.

We will not combine `async def` endpoints with blocking database or HTTP clients,
because those calls would block the event-loop thread and delay unrelated
requests.

## Consequences

The service can handle concurrent database and embedding-provider waits with a
small number of threads. Async does not make an individual query faster or let
an endpoint return before its required data is ready. It adds implementation
discipline: blocking work must be avoided or explicitly moved off the event loop,
and an `AsyncSession` must not be shared between concurrent tasks.
