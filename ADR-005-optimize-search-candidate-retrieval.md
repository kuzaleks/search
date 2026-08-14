# ADR-005: Optimize Search Candidate Retrieval

- Status: Accepted
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

## Decision

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

Overlap independent work in two lanes:

1. Start query-embedding generation as an `asyncio` task.
2. On the request's existing `AsyncSession`, run client retrieval and lexical
   document retrieval sequentially.
3. Await the embedding if it is not ready, then run semantic retrieval and
   reciprocal-rank fusion.

Client and lexical queries deliberately share one sequential database lane.
Running queries concurrently on one `AsyncSession` is unsupported, while
opening phase-specific sessions would increase pool and database pressure. The
expected endpoint duration becomes approximately
`max(embedding, client + lexical) + semantic + fusion`, rather than the sum of
every phase.

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

### Run all database phases concurrently

Rejected for now. It would require separate sessions and connections for
client and lexical retrieval. Their combined work already fits beneath the
embedding-provider latency in the measured workload, so the additional pool
pressure would provide little critical-path benefit.

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
  availability, but independent database work no longer adds to it serially.
- If database retrieval fails, the in-flight embedding task is cancelled and
  awaited so it cannot outlive the request.
- A delayed provider failure may be observed only after client and lexical
  retrieval finish, causing bounded wasted database work. Immediate provider
  configuration failures are detected before database retrieval starts.
- Phase timings now overlap and therefore must not be added together to infer
  total request duration.

## Implementation Progress

Document lexical retrieval now uses separate bounded title and chunk candidate
queries. Title FTS, substring, and typo candidates are combined with
`UNION ALL`; chunk FTS filters candidates before joining documents. The final
merge deduplicates documents, retains the highest lexical score, and prefers a
matching content snippet.

A final 20-iteration database-only benchmark on the unchanged `baseline10k`
dataset measured:

| Case | Baseline p50 | Current p50 |
|---|---:|---:|
| Exact document title | 600.1 ms | 8.5 ms |
| Typo document title | 595.5 ms | 64.5 ms |
| Document content FTS | 392.9 ms | 68.9 ms |
| Lexical no-match | 306.1 ms | 38.1 ms |

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
| Exact client email | 62.2 ms | 0.6 ms |
| Typo client name | 43.1 ms | 37.3 ms |

`EXPLAIN (ANALYZE, BUFFERS)` confirms that exact email retrieval uses the
B-tree expression index. The broad typo fixture matches many near-identical
generated client names, so PostgreSQL still considers a sequential scan cheaper
for that candidate branch at 1,000 clients; its latency and expected result are
preserved rather than materially improved.

All 49 unit and PostgreSQL integration tests pass, including exact email,
exact and typo name, description substring and typo, title and content search,
hybrid ranking, and snippet selection. `alembic check` reports no schema drift.

The title FTS and chunk FTS candidate queries use their GIN indexes. PostgreSQL
still chooses sequential scans for broad fuzzy-title scoring at this dataset
size because many generated titles are similar. Exact-title and no-match p50
are both below 50 ms.

The sequential API rerun was 80/80 valid. The concurrency-five rerun was
160/160 valid with no timeout or application error, compared with 159/160 in
the baseline. Full tables are recorded in `PERFORMANCE_RESULTS.md`.

Search completion logs now include phase-level durations for embedding, client
retrieval, semantic retrieval, lexical retrieval, fusion, response building,
and total endpoint work. Embedding calls have an explicit retry count and an
overall timeout that includes retries.

### Pipeline Overlap Outcome

Query embedding now runs concurrently with the sequential client and lexical
database lane. Semantic retrieval still waits for the embedding, then the
existing reciprocal-rank fusion combines semantic and lexical candidates. A
focused test coordinates both lanes with events to prove the overlap, and a
second test verifies that a database failure cancels the embedding task.

Embedding latency remains the effective lower bound when it is slower than the
independent database lane, but client and lexical latency are no longer added
to it serially. The critical path is approximately
`max(embedding, client + lexical) + semantic + fusion`.

On the same `baseline10k` dataset, sequential API p50 fell from 184-240 ms to
141-150 ms across the eight cases. At concurrency five, p50 fell from
186-252 ms to 141-153 ms. Both runs returned 100% valid responses. One logged
hybrid request demonstrates the overlap: 134.1 ms embedding, 37.8 ms client,
72.9 ms lexical, 4.4 ms semantic, and 138.7 ms total. The phase durations sum
past total because the first three database/provider durations overlap.

## Acceptance Evidence

- `EXPLAIN (ANALYZE, BUFFERS)` shows GIN index retrieval for title and chunk
  FTS candidates and B-tree retrieval for exact client email.
- Exact title is 8.5 ms p50 and lexical no-match is 38.1 ms p50.
- Client typo search retains its expected result and improves from 43.1 ms to
  37.3 ms p50 in the final database run.
- All 49 relevance, snippet, stemming, web-query, typo, deduplication, API,
  timeout, and integration tests pass.
- Final sequential, concurrency-five, and database-only measurements are
  recorded in `PERFORMANCE_RESULTS.md`.
