# ADR-005: Optimize Search Candidate Retrieval

- Status: Proposed
- Date: 2026-08-14

## Context

Performance testing used 1,000 clients, 10,000 documents averaging 10 KB, and
50,000 document chunks. The complete measurements and reproduction commands are
recorded in [Search Performance Baseline](./PERFORMANCE_RESULTS.md).

The database-only benchmark measured:

- Semantic chunk search at 9.1 ms p50.
- Lexical no-match search at 306.1 ms p50.
- Exact document-title search at 600.1 ms p50.
- Exact client-email search at 62.2 ms p50.

`EXPLAIN (ANALYZE, BUFFERS)` confirmed that HNSW semantic retrieval uses its
index and selects 100 candidates in 2.2 ms. The lexical statement, however,
combines title FTS, chunk FTS, title substring, and title trigram predicates in
one cross-table `OR`. PostgreSQL consequently scans all 10,005 documents and
50,008 chunks, then removes 45,008 joined rows for the exact-title case.

Client search also scans all clients and evaluates several similarity
functions per row. A common email domain caused its low trigram threshold to
admit all 1,000 generated clients as candidates.

The end-to-end API benchmark was 100% reliable sequentially and 99.4% reliable
at concurrency five. One request reached the 30-second client timeout; no
database or application exception identified the phase responsible.

## Proposed Decision

Generate a small indexed candidate set before calculating expensive scores.

For document lexical retrieval:

1. Query title candidates only from `documents`, using title FTS and trigram
   indexes.
2. Replace the unindexed `strpos(lower(title), query)` predicate with an escaped
   `ILIKE '%query%'` predicate supported by `gin_trgm_ops`.
3. Query content candidates only from `document_chunks`, using the chunk FTS
   GIN index.
4. Merge title and chunk candidates by document ID, retain the highest lexical
   score, prefer a matching content snippet, and apply the existing candidate
   limit.
5. Keep semantic retrieval and reciprocal-rank fusion unchanged.

Start with two explicit database queries and merge candidates in Python. This
is easier to inspect and test than one complex statement. A `UNION ALL` query
can replace the two round trips later if measurements show that round-trip
latency is material.

For client retrieval:

1. Separate exact email lookup from fuzzy candidate generation so the existing
   `lower(email)` B-tree index can be used directly.
2. Generate fuzzy candidates independently for full name, email, and
   description rather than applying `word_similarity` to one combined value.
3. Add expression trigram indexes matching those candidate expressions.
4. Merge and deduplicate candidates before applying the existing relevance
   scoring and result limit.

Add phase-level timings for query embedding, client retrieval, semantic
retrieval, lexical retrieval, fusion, and serialization. This will distinguish
database regressions from external embedding-provider stalls.

## Alternatives Considered

### Add more indexes without changing the query

Rejected. GIN and trigram indexes already exist for document search. The
cross-table `OR` and unindexed `strpos` branch prevent PostgreSQL from using
them effectively.

### Tune PostgreSQL to favor index scans

Rejected as the primary solution. Disabling sequential scans can demonstrate
index availability but does not correct the inefficient candidate-query shape
and may damage unrelated plans.

### Move lexical search to OpenSearch

Deferred. OpenSearch would provide stronger lexical retrieval but introduces a
second datastore and synchronization concerns. The measured semantic path is
already fast, and PostgreSQL should handle the expected scale once candidate
queries are indexable.

## Consequences

- No-match and selective title searches should avoid work proportional to all
  chunks.
- Document retrieval adds a second database round trip in exchange for much
  smaller indexed scans.
- Merging candidates in Python may change ordering at candidate-limit
  boundaries and therefore requires ranking regression tests.
- Client expression indexes increase write cost and storage but should remain
  small for the expected 1,000-10,000 clients.
- The external embedding provider remains part of end-to-end latency and
  availability.

## Implementation Progress

Document lexical retrieval now uses separate bounded title and chunk candidate
queries. Title FTS, substring, and typo candidates are combined with
`UNION ALL`; chunk FTS filters candidates before joining documents. The final
merge deduplicates documents, retains the highest lexical score, and prefers a
matching content snippet.

A short database-only benchmark on the unchanged `baseline10k` dataset measured:

| Case | Baseline p50 | Current p50 |
|---|---:|---:|
| Exact document title | 600.1 ms | 64.3 ms |
| Typo document title | 595.5 ms | 64.3 ms |
| Document content FTS | 392.9 ms | 65.1 ms |
| Lexical no-match | 306.1 ms | 30.9 ms |

Client retrieval now short-circuits a case-insensitive exact email match using
`uq_clients_email_lower`. Other searches generate bounded full-name, email, and
description substring and fuzzy candidates, deduplicate them, and then apply
the existing exact, substring, and trigram scoring expression. Migration
`20260814_0007` adds matching GIN trigram expression indexes for those fields.
The application sets the similarity and word-similarity thresholds to `0.20`
for the current transaction so indexed candidate filtering preserves the
existing score cutoff.

A 20-iteration database-only benchmark measured:

| Case | Baseline p50 | Current p50 |
|---|---:|---:|
| Exact client email | 62.2 ms | 0.5 ms |
| Typo client name | 43.1 ms | 43.7 ms |

`EXPLAIN (ANALYZE, BUFFERS)` confirms that exact email retrieval uses the
B-tree expression index. The broad typo fixture matches many near-identical
generated client names, so PostgreSQL still considers a sequential scan cheaper
for that candidate branch at 1,000 clients; its latency and expected result are
preserved rather than materially improved.

All 44 unit and PostgreSQL integration tests pass, including exact email,
exact and typo name, description substring and typo, title and content search,
hybrid ranking, and snippet selection. `alembic check` reports no schema drift.

The chunk query uses `ix_document_chunks_search_vector`. PostgreSQL still
chooses a sequential scan for broad fuzzy-title scoring at this dataset size,
and exact-title p50 remains above the 50 ms acceptance target. These results are
intermediate; the complete sequential and concurrency-five API benchmarks have
not yet been rerun.

## Acceptance Criteria

Before changing this ADR to `Accepted`:

- `EXPLAIN (ANALYZE, BUFFERS)` must show GIN/trigram index use for title and
  chunk lexical candidate queries.
- Exact title and lexical no-match database p50 should be below 50 ms on the
  existing `baseline10k` dataset.
- Client exact-email lookup should use the B-tree index, while typo search must
  retain its expected result.
- Existing relevance, snippet, stemming, web-query, typo, and deduplication
  tests must continue to pass.
- Sequential and concurrency-five benchmarks must be rerun and appended to the
  performance report.
