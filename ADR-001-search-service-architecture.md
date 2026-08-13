# ADR-001: Search Service Architecture

- Status: Accepted
- Date: 2026-08-12

## Context

Nevis advisors need an API that can search across clients and their documents.
The service must support:

- Client matches across names, email addresses, and descriptions.
- Semantic document matches, such as finding a utility bill for the query
  `address proof`.
- Relevance-ranked results.
- Between 1,000 and 10,000 clients, with 10 to 100 documents per client.
- Documents averaging approximately 10 KB.
- Reproducible local setup with Docker Compose.

External embedding providers are allowed, vector search is expected, and
authentication, authorization, document updates, and document deletion are out
of scope. Duplicate clients are not allowed.

## Decision

Build an asynchronous Python API using FastAPI, PostgreSQL, and `pgvector`.
Use an external embedding provider for semantic document search.

PostgreSQL will provide all persistent storage and search capabilities:

- `pg_trgm` and PostgreSQL text search for client fields and exact terms.
- `pgvector` cosine-similarity search for document chunks.
- PostgreSQL full-text search for lexical document matches.
- Reciprocal-rank fusion to combine lexical and vector document rankings.

This keeps the take-home service reproducible without introducing a separate
search cluster or vector database.

## Architecture

```text
HTTP API (FastAPI)
        |
        +-- Client service
        |     +-- PostgreSQL client records
        |     +-- trigram/text search
        |
        +-- Document service
        |     +-- document storage
        |     +-- text chunking
        |     +-- external embeddings
        |
        +-- Search service
              +-- lexical client search
              +-- hybrid document search
              +-- relevance ranking

PostgreSQL
    clients
    documents
    document_chunks + vector embeddings
```

## Data Model

### Clients

- UUID `id`
- `first_name`
- `last_name`
- `email`
- optional `description`
- `social_links`
- case-insensitive unique email constraint

### Documents

- UUID `id`
- `client_id` foreign key
- `title`
- original `content`
- `created_at`

### Document Chunks

- UUID `id`
- `document_id` foreign key
- `chunk_index`
- inclusive `start_offset` and exclusive `end_offset`
- embedding vector
- searchable text vector

Documents will be divided at word boundaries into 400-word chunks with a
50-word overlap, approximately 500 tokens depending on the text. Chunking
improves retrieval precision and prevents large documents from exceeding
embedding-provider limits. OpenAI's `text-embedding-3-small` will generate
512-dimensional vectors, reducing storage relative to its full-size output.

### Document Text Storage

Store the original document text once in PostgreSQL. Chunk rows retain character
offsets, embeddings, and searchable text vectors rather than duplicate chunk
text. Matching snippets are reconstructed from `documents.content` using the
inclusive start and exclusive end offsets. This is safe because document
updates are out of scope, so offsets cannot become stale.

PostgreSQL persists the original `TEXT` on disk and can compress or move larger
values out of the main row through TOAST. Keeping the original, chunk metadata,
and embeddings in one database preserves atomic ingestion while avoiding text
duplication caused by chunk overlap.

## API Design

### `POST /clients`

- Validate required fields and email format.
- Return `409 Conflict` for a duplicate email.
- Return `201 Created` on success.

### `POST /clients/{client_id}/documents`

- Return `404 Not Found` when the client does not exist.
- Split the document into chunks.
- Generate embeddings for the chunks.
- Store the document and chunks atomically.
- Return `201 Created` on success.

### `GET /search?q=...&limit=10`

- Reject a blank query.
- Generate one embedding for the query.
- Search clients using substring and trigram similarity.
- Search document chunks using vector similarity and full-text search.
- Combine document rankings using reciprocal-rank fusion.
- Group matching chunks by document and return the best snippet.

Document full-text search uses PostgreSQL's English text-search configuration
and stores only `tsvector` values for chunks. Titles have their own generated
`tsvector` and receive greater lexical weight than body matches. Semantic and
lexical candidate lists are combined using reciprocal-rank fusion with
`k = 60`, avoiding direct comparison of unrelated raw score scales. Returned
snippets are reconstructed from offsets and capped at approximately 320
characters.

### Implemented Search Capabilities

Client retrieval provides:

- Case-insensitive exact matching across first name, last name, full name, and
  email.
- Substring matching across names, email, and description.
- Typo-tolerant metadata matching with `pg_trgm`.

Document retrieval provides:

- Weighted English full-text search over titles and chunk content, including
  stemming and stop-word handling.
- Typo-tolerant and substring title matching with `pg_trgm`.
- Web-style lexical queries with ordinary AND terms, quoted phrases, `OR`, and
  `-` exclusions through `websearch_to_tsquery`.
- Semantic chunk search using OpenAI embeddings and pgvector cosine distance.
- Chunk-level retrieval with one result per document, selected from its best
  matching candidates.
- Hybrid ranking through reciprocal-rank fusion, which rewards documents found
  by both lexical and semantic retrieval.

The API returns client and document rankings separately because their scores
are not comparable. Document results include bounded snippets reconstructed
from offsets and centered on lexical terms when available. The `limit`
parameter defaults to 10, accepts values from 1 to 50, and blank or overlong
queries are rejected.

PostgreSQL accelerates these paths with GIN indexes for title and chunk FTS, a
GIN trigram index for document titles, and an HNSW cosine index for embeddings.

The response will contain separate client and document collections:

```json
{
  "query": "address proof",
  "clients": [],
  "documents": [
    {
      "score": 0.91,
      "document": {
        "id": "...",
        "client_id": "...",
        "title": "Utility bill"
      },
      "snippet": "Electricity account statement..."
    }
  ]
}
```

Client lexical scores and document vector scores are not directly comparable.
Keeping them in separate ranked collections avoids presenting a misleading
mixed score.

## Project Structure

```text
search/
|-- app/
|   |-- main.py
|   |-- config.py
|   |-- database.py
|   |-- models.py
|   |-- schemas.py
|   |-- api.py
|   |-- embeddings.py
|   |-- chunking.py
|   `-- search.py
|-- migrations/
|-- tests/
|-- Dockerfile
|-- docker-compose.yml
|-- requirements.txt
|-- .env.example
`-- README.md
```

The design separates HTTP, persistence, embedding, chunking, and search logic
without adding repository or domain layers that are unnecessary for this
assignment.

## Alternatives Considered

### OpenSearch

OpenSearch offers strong full-text and vector-search capabilities, but adds a
second persistence system and more operational setup. It is unnecessary for the
initial scale and time budget.

### Dedicated Vector Database

A hosted or standalone vector database could scale semantic retrieval
independently. It would complicate local reproducibility and require keeping
document metadata synchronized with PostgreSQL.

### Local Embedding Model

A local model would remove the external API dependency, but increases image
size, startup time, hardware requirements, and setup complexity. An external
provider is acceptable for this assignment and keeps the service focused.

### Whole-Document Embeddings

Embedding an entire document is simpler, but produces poorer matches for mixed
or long documents and can exceed provider token limits. Chunk embeddings are
therefore preferred.

### Object Storage for Original Documents

Original documents could be stored in Amazon S3 with only an object key,
metadata, chunks, and embeddings in PostgreSQL. This would reduce database
storage cost and is preferable for large binary files, substantially larger
documents, or significantly greater volume. It is not selected now because it
adds another service, consistency and failure handling across two stores, and
extra retrieval latency without a clear benefit at the expected document size.

### Single Mixed Result List

A unified list would look simpler to clients, but client lexical scores and
document semantic scores are not naturally calibrated. Separate ranked lists
are more transparent.

## Consequences

### Positive

- One database handles relational, lexical, and vector data.
- Docker Compose can reproduce the complete local infrastructure.
- Chunk-level search supports precise semantic matches.
- Hybrid search handles both exact terms and related concepts.
- FastAPI provides validation and generated OpenAPI documentation.

### Negative

- Document creation waits for the embedding provider.
- Search availability and latency partly depend on an external provider.
- PostgreSQL vector search may eventually require partitioning, tuning, or a
  dedicated search system at substantially larger scale.
- Separate result collections require consumers to decide how to present client
  and document results together.

## Testing Strategy

Core automated tests will cover:

- Client creation and validation.
- Duplicate clients returning `409 Conflict`.
- Document creation for an unknown client returning `404 Not Found`.
- Document chunk creation.
- Case-insensitive name and email matching.
- Semantic matching, including `address proof` finding `utility bill`.
- Relevance ordering.
- Blank query validation and result limits.
- Embedding-provider failures without partial document persistence.

Automated tests will use deterministic fake embeddings and will not require an
API key or make paid external calls.

## Implementation Plan

1. Scaffold FastAPI, configuration, Docker Compose, PostgreSQL, `pgvector`, and
   database migrations.
2. Implement client persistence and `POST /clients`.
3. Implement documents, chunking, embedding generation, and document ingestion.
4. Implement lexical client search and semantic document search.
5. Add document full-text search, ranking fusion, and snippets.
6. Add consistent API error handling and OpenAPI details.
7. Add unit and PostgreSQL integration tests.
8. Complete the README with setup instructions, architecture decisions, and
   example requests and responses.

## Deferred Work

- Authentication and authorization.
- Client or document updates and deletion.
- Cursor-based pagination.
- Asynchronous ingestion through a job queue.
- LLM-generated document summaries.
- Full production hardening and infrastructure automation.

For a production version, document embedding would likely move to a background
worker with explicit processing states and retry handling.
