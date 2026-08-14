# Search Performance Baseline

Date: 2026-08-14

## Scope

The benchmark used the local `baseline10k` dataset:

- 1,000 clients.
- 10 documents per client, for 10,000 documents total.
- Exactly 10,000 bytes of content per document.
- Five overlapping chunks per document, for 50,000 chunks total.
- 512-dimensional synthetic topic vectors.
- 575 MB PostgreSQL database and 1.67 GB Docker volume.

The API ran as one Uvicorn process in Docker. End-to-end measurements include
HTTP handling, OpenAI query embedding, client search, semantic document search,
lexical document search, ranking fusion, snippets, and serialization. The
database benchmark bypasses HTTP and OpenAI and calls the application search
functions with a precomputed vector.

Synthetic vectors exercise pgvector and HNSW performance but cannot measure
semantic relevance against OpenAI query embeddings.

## Use Cases

- Exact client email.
- Typo-tolerant client name.
- Exact document title.
- Typo-tolerant document title.
- Document-content full-text search with stemming.
- Quoted web-style query syntax.
- Hybrid lexical and semantic document search.
- Query with no lexical match.

A response counted as reliable only when it returned HTTP 200 with the expected
client or document for cases with a known result.

## Sequential API Baseline

Configuration: one warm-up, 10 measured requests per case, concurrency 1,
`limit=10`.

| Case | Valid | Mean | p50 | p95/max |
|---|---:|---:|---:|---:|
| Exact client email | 10/10 | 623.9 ms | 614.5 ms | 657.3 ms |
| Typo client | 10/10 | 526.5 ms | 522.2 ms | 576.1 ms |
| Exact document title | 10/10 | 763.4 ms | 767.8 ms | 781.3 ms |
| Typo document title | 10/10 | 755.1 ms | 753.0 ms | 792.9 ms |
| Document content FTS | 10/10 | 572.9 ms | 572.4 ms | 600.7 ms |
| Web query syntax | 10/10 | 553.6 ms | 554.1 ms | 574.1 ms |
| Hybrid document | 10/10 | 616.5 ms | 567.6 ms | 1,099.3 ms |
| No lexical match | 10/10 | 476.4 ms | 467.3 ms | 517.2 ms |

Overall reliability was 80/80, or 100%.

## Concurrent API Baseline

Configuration: one warm-up, 20 measured requests per case, concurrency 5,
`limit=10`.

| Case | Valid | Mean | p50 | p95 | Max | Throughput |
|---|---:|---:|---:|---:|---:|---:|
| Exact client email | 20/20 | 769.4 ms | 728.0 ms | 1,000.0 ms | 1,000.3 ms | 6.26 req/s |
| Typo client | 20/20 | 659.9 ms | 659.2 ms | 678.6 ms | 679.7 ms | 7.55 req/s |
| Exact document title | 20/20 | 906.4 ms | 907.4 ms | 925.7 ms | 926.5 ms | 5.51 req/s |
| Typo document title | 20/20 | 905.1 ms | 878.2 ms | 963.2 ms | 963.7 ms | 5.51 req/s |
| Document content FTS | 20/20 | 704.7 ms | 702.9 ms | 726.8 ms | 729.5 ms | 7.08 req/s |
| Web query syntax | 19/20 | 2,157.2 ms | 675.6 ms | 732.1 ms | 30,018.0 ms | 0.62 req/s |
| Hybrid document | 20/20 | 713.2 ms | 710.3 ms | 744.2 ms | 745.0 ms | 7.00 req/s |
| No lexical match | 20/20 | 595.1 ms | 592.1 ms | 608.4 ms | 609.9 ms | 8.37 req/s |

Overall reliability was 159/160, or 99.4%. One web-query request hit the
benchmark's 30-second client timeout. The other 19 requests in that case stayed
below 733 ms, the API logged no database or application exception, and
PostgreSQL connections were idle afterward. This is consistent with an
isolated external embedding or connection stall, but phase-level timing is
required to attribute it conclusively.

## PostgreSQL Baseline

Configuration: two warm-ups, 20 measured calls per case, concurrency 1. Times
include SQLAlchemy mapping and snippet construction but exclude HTTP and
OpenAI.

| Case | Valid | Mean | p50 | p95 | Max |
|---|---:|---:|---:|---:|---:|
| Exact client email | 20/20 | 63.8 ms | 62.2 ms | 69.5 ms | 79.0 ms |
| Typo client | 20/20 | 43.2 ms | 43.1 ms | 43.9 ms | 44.0 ms |
| Semantic chunks | 20/20 | 9.1 ms | 9.1 ms | 12.1 ms | 13.8 ms |
| Exact document title | 20/20 | 630.2 ms | 600.1 ms | 659.2 ms | 1,077.9 ms |
| Typo document title | 20/20 | 622.6 ms | 595.5 ms | 627.8 ms | 1,058.7 ms |
| Document content FTS | 20/20 | 418.4 ms | 392.9 ms | 615.4 ms | 633.1 ms |
| Web query syntax | 20/20 | 391.4 ms | 389.7 ms | 406.4 ms | 407.3 ms |
| Hybrid document | 20/20 | 407.6 ms | 404.7 ms | 421.2 ms | 427.6 ms |
| No lexical match | 20/20 | 310.7 ms | 306.1 ms | 326.7 ms | 363.3 ms |

Overall database reliability was 180/180, or 100%.

## Findings

### Semantic index performs well

The semantic service path was 9.1 ms p50. `EXPLAIN (ANALYZE, BUFFERS)` confirmed
an HNSW index scan that selected 100 candidates in 2.2 ms.

### Lexical query bypasses its indexes

The current lexical statement combines title FTS, chunk FTS, title substring,
and title trigram conditions in one `OR` across `documents` and
`document_chunks`. PostgreSQL consequently used sequential scans over all
10,005 documents and 50,008 chunks. For the exact-title case it removed 45,008
joined rows in the filter and took 647 ms.

This explains why a lexical no-match still costs approximately 306 ms and title
search costs approximately 600 ms despite the GIN indexes.

### Client search scans every client

Client search also used a sequential scan. The exact-email plan took 32.8 ms at
the SQL level before scoring and result mapping. The common load-test email
domain caused the low trigram threshold to admit all 1,000 generated clients as
candidates. This behavior can also occur with real clients sharing a company
email domain.

### Reliability testing found invalid fixture data

The first correctness gate returned four HTTP 500 responses because bulk-loaded
`.invalid` addresses bypassed request validation but failed `EmailStr` response
validation. Generated addresses now use `performance-test.nevis.dev`, and a
regression test validates them against the API schema.

## Recommended Next Steps

1. Split lexical title and chunk retrieval into independently indexable queries,
   then merge and deduplicate their candidate rankings.
2. Split exact client lookup from fuzzy lookup and add appropriate trigram
   indexes for the fuzzy candidate fields.
3. Add phase timings for embedding, client SQL, semantic SQL, lexical SQL, and
   serialization to make external-provider stalls observable.
4. Repeat the same benchmark after query changes, then test higher concurrency
   and the 100,000-document dataset.

## Reproducing

End-to-end sequential baseline:

```bash
.venv/bin/python -m scripts.benchmark_search \
  --iterations 10 \
  --warmup-iterations 1 \
  --concurrency 1
```

Concurrency-5 baseline:

```bash
.venv/bin/python -m scripts.benchmark_search \
  --iterations 20 \
  --warmup-iterations 1 \
  --concurrency 5
```

Database-only baseline:

```bash
.venv/bin/python -m scripts.benchmark_database \
  --iterations 20 \
  --warmup-iterations 2
```
