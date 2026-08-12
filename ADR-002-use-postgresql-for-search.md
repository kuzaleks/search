# ADR-002: Use PostgreSQL for Search

- Status: Accepted
- Date: 2026-08-12

## Context

The service needs durable storage for clients and documents, lexical matching,
typo tolerance, semantic document search, and relevance ranking. Apache Lucene
offers more sophisticated lexical search, but from Python it would normally be
used through an additional service such as OpenSearch.

## Decision

Use PostgreSQL as both the system of record and the search backend:

- PostgreSQL full-text search for lexical document matching.
- `pg_trgm` for partial and typo-tolerant client and title matching.
- `pgvector` for semantic document retrieval.

Do not introduce Lucene or OpenSearch for the take-home implementation.

## Rationale

PostgreSQL provides atomic uniqueness constraints, foreign keys, transactional
document ingestion, and immediately searchable writes. Adding OpenSearch would
require a second datastore, indexing synchronization, retry handling, and
eventual-consistency behaviour.

Lucene provides stronger analyzers, BM25 ranking, fuzzy queries, and synonym
handling. However, the task's main semantic requirement is addressed by vector
embeddings, while PostgreSQL's lexical features are sufficient for the expected
client and document searches.

## Consequences

The service has fewer components and a simpler reproducible setup, but less
advanced lexical-search functionality. Reconsider OpenSearch if the product
requires sophisticated multilingual analysis, document-wide typo correction,
faceting, extensive relevance tuning, very high query throughput, or independent
horizontal scaling of search.
