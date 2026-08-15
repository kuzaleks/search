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

## Optimized Results

The optimized implementation uses indexed candidate retrieval, an exact-email
short circuit, an exact-title short circuit, and bounded fuzzy candidates. The
following measurements use the same `baseline10k` data and benchmark settings
as the baseline tables above.

### PostgreSQL After Optimization

| Case | Valid | Mean | p50 | p95 | Max |
|---|---:|---:|---:|---:|---:|
| Exact client email | 20/20 | 0.6 ms | 0.6 ms | 0.7 ms | 0.7 ms |
| Typo client | 20/20 | 40.4 ms | 37.3 ms | 57.2 ms | 57.4 ms |
| Semantic chunks | 20/20 | 10.1 ms | 10.3 ms | 12.8 ms | 13.5 ms |
| Exact document title | 20/20 | 8.5 ms | 8.5 ms | 9.4 ms | 12.6 ms |
| Typo document title | 20/20 | 68.4 ms | 64.5 ms | 89.3 ms | 101.5 ms |
| Document content FTS | 20/20 | 69.5 ms | 68.9 ms | 72.1 ms | 76.9 ms |
| Web query syntax | 20/20 | 90.5 ms | 90.5 ms | 93.1 ms | 94.2 ms |
| Hybrid document | 20/20 | 98.5 ms | 97.4 ms | 102.7 ms | 103.9 ms |
| No lexical match | 20/20 | 38.3 ms | 38.1 ms | 39.9 ms | 40.8 ms |

Overall database reliability was 180/180, or 100%.

### Sequential API After Optimization

| Case | Valid | Mean | p50 | p95/max |
|---|---:|---:|---:|---:|
| Exact client email | 10/10 | 220.2 ms | 220.3 ms | 237.2 ms |
| Typo client | 10/10 | 231.2 ms | 230.8 ms | 255.4 ms |
| Exact document title | 10/10 | 208.0 ms | 184.3 ms | 383.5 ms |
| Typo document title | 10/10 | 247.7 ms | 237.6 ms | 323.9 ms |
| Document content FTS | 10/10 | 246.8 ms | 232.7 ms | 368.2 ms |
| Web query syntax | 10/10 | 245.7 ms | 239.6 ms | 270.9 ms |
| Hybrid document | 10/10 | 238.6 ms | 234.3 ms | 270.0 ms |
| No lexical match | 10/10 | 218.5 ms | 215.5 ms | 299.9 ms |

Overall sequential reliability was 80/80, or 100%.

### Concurrent API After Optimization

| Case | Valid | Mean | p50 | p95 | Max | Throughput |
|---|---:|---:|---:|---:|---:|---:|
| Exact client email | 20/20 | 224.0 ms | 206.0 ms | 310.0 ms | 312.6 ms | 20.86 req/s |
| Typo client | 20/20 | 250.9 ms | 245.4 ms | 280.2 ms | 281.1 ms | 18.80 req/s |
| Exact document title | 20/20 | 185.6 ms | 185.5 ms | 218.3 ms | 223.4 ms | 26.44 req/s |
| Typo document title | 20/20 | 241.0 ms | 240.4 ms | 282.7 ms | 286.0 ms | 19.79 req/s |
| Document content FTS | 20/20 | 237.1 ms | 237.7 ms | 262.2 ms | 265.3 ms | 20.46 req/s |
| Web query syntax | 20/20 | 250.6 ms | 252.2 ms | 258.5 ms | 316.9 ms | 18.72 req/s |
| Hybrid document | 20/20 | 261.4 ms | 245.3 ms | 317.0 ms | 319.9 ms | 17.44 req/s |
| No lexical match | 20/20 | 201.8 ms | 201.8 ms | 214.2 ms | 223.6 ms | 23.49 req/s |

Overall concurrency-five reliability was 160/160, or 100%. No provider timeout
or application error was logged.

## API After Pipeline Overlap

Query embedding now overlaps the independent database lane. That lane runs
client and lexical retrieval sequentially on one `AsyncSession`; semantic
retrieval follows once the embedding is available. The dataset, result limit,
warm-ups, and request counts are unchanged from the preceding API tables.

### Sequential API

| Case | Valid | Mean | p50 | p95/max |
|---|---:|---:|---:|---:|
| Exact client email | 10/10 | 203.0 ms | 148.3 ms | 719.5 ms |
| Typo client | 10/10 | 142.1 ms | 140.9 ms | 154.2 ms |
| Exact document title | 10/10 | 152.7 ms | 149.6 ms | 167.0 ms |
| Typo document title | 10/10 | 144.8 ms | 141.9 ms | 159.6 ms |
| Document content FTS | 10/10 | 144.0 ms | 144.4 ms | 157.1 ms |
| Web query syntax | 10/10 | 155.1 ms | 147.3 ms | 188.4 ms |
| Hybrid document | 10/10 | 155.5 ms | 148.1 ms | 255.1 ms |
| No lexical match | 10/10 | 171.1 ms | 148.2 ms | 330.2 ms |

Overall reliability was 80/80, or 100%. Compared with indexed candidate
retrieval before overlap, p50 improved by 19-40% depending on the case. The
719.5 ms exact-email outlier occurred in the external embedding phase.

### Concurrent API

Configuration: one warm-up, 20 measured requests per case, concurrency 5.

| Case | Valid | Mean | p50 | p95 | Max | Throughput |
|---|---:|---:|---:|---:|---:|---:|
| Exact client email | 20/20 | 162.9 ms | 150.4 ms | 232.4 ms | 271.3 ms | 28.51 req/s |
| Typo client | 20/20 | 156.4 ms | 149.8 ms | 191.3 ms | 225.2 ms | 29.58 req/s |
| Exact document title | 20/20 | 153.2 ms | 149.3 ms | 171.6 ms | 188.1 ms | 31.21 req/s |
| Typo document title | 20/20 | 157.3 ms | 151.6 ms | 192.2 ms | 200.0 ms | 29.82 req/s |
| Document content FTS | 20/20 | 152.6 ms | 153.0 ms | 162.5 ms | 222.5 ms | 28.72 req/s |
| Web query syntax | 20/20 | 142.4 ms | 141.0 ms | 156.1 ms | 161.6 ms | 34.24 req/s |
| Hybrid document | 20/20 | 145.1 ms | 145.6 ms | 159.7 ms | 160.1 ms | 33.53 req/s |
| No lexical match | 20/20 | 150.4 ms | 145.3 ms | 179.2 ms | 244.3 ms | 30.42 req/s |

Overall reliability was 160/160, or 100%.

The phase logs confirm that durations overlap. For example, one hybrid request
reported 134.1 ms embedding, 37.8 ms client, 72.9 ms lexical, 4.4 ms semantic,
and 138.7 ms total. Total latency is now approximately the slower of embedding
or the client-plus-lexical lane, followed by semantic retrieval and fusion.

### Database Check

The orchestration change does not alter database query plans. A final
30-iteration check remained 270/270 valid: semantic retrieval was 8.7 ms p50,
exact email 0.5 ms, exact title 11.5 ms, content FTS 70.3 ms, and hybrid
retrieval 96.7 ms.

### Throughput Saturation Test

Higher-concurrency runs tested whether the local deployment could sustain 50
requests per second:

- Concurrency 10: 50 measured requests per case.
- Concurrency 15: 45 measured requests per case.
- Concurrency 20: 40 measured requests per case.

All 1,080 responses were valid. The best observed throughput for each
homogeneous workload was:

| Case | Best throughput | Concurrency |
|---|---:|---:|
| Exact client email | 54.2 req/s | 20 |
| Typo client | 44.1 req/s | 15 |
| Exact document title | 59.0 req/s | 15 |
| Typo document title | 38.2 req/s | 20 |
| Document content FTS | 46.5 req/s | 20 |
| Web query syntax | 36.1 req/s | 15 |
| Hybrid document | 38.5 req/s | 10 |
| No lexical match | 61.2 req/s | 20 |

Selective exact and no-match searches exceeded 50 requests per second. Broad
typo, content, web-syntax, and hybrid searches plateaued between 36 and 47
requests per second. Treating the eight homogeneous batches as equally
weighted gives an approximate aggregate plateau of 44 requests per second;
this is not a dedicated mixed-workload measurement.

Increasing concurrency beyond the 15-connection SQLAlchemy pool capacity did
not produce a general throughput improvement. At concurrency 20, p95 latency
ranged from 438 ms to 947 ms. Phase logs showed client, lexical, and semantic
database durations increasing under load, especially for broad queries.

The current deployment therefore demonstrates more than 50 requests per
second for selective searches, but not consistently across all search modes.
Reaching a general 50-request-per-second target requires further optimization
of broad PostgreSQL retrieval or additional database capacity; adding request
concurrency alone is insufficient.

### Query Plans and Attribution

`EXPLAIN (ANALYZE, BUFFERS)` confirms:

- `uq_clients_email_lower` serves exact email lookup.
- `ix_documents_title_search_vector` serves title FTS candidates.
- `ix_document_chunks_search_vector` serves content FTS candidates.
- The HNSW index continues to serve semantic candidates.

PostgreSQL still chooses sequential scans for broad fuzzy title and client
queries on this dataset because many generated records are intentionally
similar. The trigram indexes are available, but the planner estimates a scan as
cheaper at 1,000 clients and 10,000 titles.

Each completed API search now logs total, embedding, client, semantic, lexical,
fusion, and response-construction durations without logging the search text in
the structured record. The embedding operation has a 30-second overall default
deadline and one explicit SDK retry by default. This prevents retries from
multiplying the request deadline and makes provider stalls attributable.

## Follow-up Opportunities

1. Optimize broad typo and lexical candidate retrieval, then repeat the
   saturation test before claiming a general 50-request-per-second capacity.
2. Repeat the benchmark with 100,000 documents to observe the planner's index
   choices at the upper expected data range.
3. Evaluate a GiST trigram index if fuzzy-title latency becomes important; it
   can support nearest-neighbor similarity ordering directly.
4. Export phase timings to a metrics backend if production percentile alerts
   are required.

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

Throughput saturation runs:

```bash
.venv/bin/python -m scripts.benchmark_search \
  --iterations 50 --warmup-iterations 2 --concurrency 10
.venv/bin/python -m scripts.benchmark_search \
  --iterations 45 --warmup-iterations 2 --concurrency 15
.venv/bin/python -m scripts.benchmark_search \
  --iterations 40 --warmup-iterations 2 --concurrency 20
```

Database-only baseline:

```bash
.venv/bin/python -m scripts.benchmark_database \
  --iterations 20 \
  --warmup-iterations 2
```
