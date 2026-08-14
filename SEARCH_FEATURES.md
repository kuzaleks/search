# Search Features and Manual Test Guide

This guide lists the service's main features and provides requests for testing
them against a local instance.

## Prerequisites

Start the stack and verify that the API and database are ready:

```bash
docker compose up -d --build
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

The examples use `jq` to create request bodies and format responses. Set
`OPENAI_API_KEY` in `.env` before starting the stack because document ingestion
and search generate embeddings.

## Create Test Data

Create a client with a unique email and retain its ID:

```bash
CLIENT_EMAIL="manual-search-$(date +%s)@example.com"

CLIENT_ID=$(
  curl -sS -X POST http://localhost:8000/clients \
    -H 'Content-Type: application/json' \
    -d "$(jq -n \
      --arg email "$CLIENT_EMAIL" \
      '{
        first_name: "Alexandra",
        last_name: "Morgan",
        email: $email,
        description: "Retirement planning and wealth management client",
        social_links: ["https://www.linkedin.com/in/alexandra-morgan"]
      }')" | jq -r '.id'
)

echo "$CLIENT_ID"
```

Create three deliberately different documents:

```bash
curl -sS -X POST \
  "http://localhost:8000/clients/$CLIENT_ID/documents" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Account Note Alpha",
    "content": "An investment portfolio allocates capital across equities and bonds. Diversification helps manage volatility and financial risk. Dividends can provide recurring income."
  }' | jq '{id, title}'

curl -sS -X POST \
  "http://localhost:8000/clients/$CLIENT_ID/documents" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "The Annual Investment Reports",
    "content": "Quarterly allocation data for client account 4821."
  }' | jq '{id, title}'

curl -sS -X POST \
  "http://localhost:8000/clients/$CLIENT_ID/documents" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Beneficiary Nomination Certificate",
    "content": "Signed form received on 14 July and approved by the adviser."
  }' | jq '{id, title}'
```

Create a document large enough to produce several overlapping chunks:

```bash
LONG_CONTENT="$(printf 'A diversified portfolio balances equities, bonds, property, cash, liquidity reserves, income, and long-term growth. %.0s' {1..50})"

jq -n \
  --arg title 'Long Portfolio Handbook' \
  --arg content "$LONG_CONTENT" \
  '{title: $title, content: $content}' \
  | curl -sS -X POST \
      "http://localhost:8000/clients/$CLIENT_ID/documents" \
      -H 'Content-Type: application/json' \
      --data-binary @- \
  | jq '{id, title}'
```

## Client Search

Client search is case-insensitive and supports exact, substring, and
typo-tolerant matching across client metadata. Client and document results are
ranked separately.

Exact full-name match:

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=alexandra morgan' \
  --data-urlencode 'limit=10' | jq '.clients'
```

Description substring match:

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=retirement' \
  --data-urlencode 'limit=10' | jq '.clients'
```

Typo-tolerant match using `pg_trgm`:

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=Alexandra Morgn' \
  --data-urlencode 'limit=10' | jq '.clients'
```

## Document Title Full-Text Search

Title search uses PostgreSQL English full-text search. It normalizes words,
removes English stop words, and applies stemming. Title matches receive extra
lexical weight.

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=the annual investments' \
  --data-urlencode 'limit=10' | jq '.documents'
```

`The Annual Investment Reports` should rank highly: `the` is a stop word, and
`investments` matches the stem of `Investment`.

## Typo-Tolerant Document Titles

Document titles also use trigram similarity for misspellings and partial
matches:

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=beneficary nomination' \
  --data-urlencode 'limit=10' | jq '.documents'
```

The misspelled query should find `Beneficiary Nomination Certificate`.

## Document Content Full-Text Search

Content is searched at chunk level with English stemming. The title below does
not contain the search words, which makes the content match easy to observe:

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=investments dividends' \
  --data-urlencode 'limit=10' | jq '.documents'
```

`Account Note Alpha` should be returned because `investments` matches
`investment` in its content and `dividends` is also present.

## Web-Style Query Syntax

Lexical document search uses `websearch_to_tsquery`, supporting ordinary AND
terms, quoted phrases, `OR`, and exclusions:

```bash
# Both terms are required by the lexical search.
curl --get http://localhost:8000/search \
  --data-urlencode 'q=investment dividends' | jq '.documents'

# Exact phrase.
curl --get http://localhost:8000/search \
  --data-urlencode 'q="investment portfolio"' | jq '.documents'

# Either expression may match.
curl --get http://localhost:8000/search \
  --data-urlencode 'q="annual investment" OR beneficiary' | jq '.documents'

# Exclude a lexical term.
curl --get http://localhost:8000/search \
  --data-urlencode 'q=investment -dividends' | jq '.documents'
```

These operators govern the lexical ranking. The endpoint also performs
semantic retrieval, so a document excluded from the lexical side can still
appear if it is a strong semantic match.

## Semantic Document Search

Semantic search embeds the query and compares it with document-chunk vectors
using pgvector cosine distance. Use different wording from the stored text:

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=How can I spread my savings to make large losses less likely?' \
  --data-urlencode 'limit=10' | jq '.documents'
```

`Account Note Alpha` should be a strong candidate even though the query does
not repeat its main keywords.

## Hybrid Document Ranking

The service produces independent lexical and semantic candidate rankings and
combines them with reciprocal-rank fusion. Documents found by both retrieval
methods receive contributions from both rankings.

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=investment portfolio risk' \
  --data-urlencode 'limit=10' \
  | jq '.documents | map({title: .document.title, score, snippet})'
```

`Account Note Alpha` should rank highly because the query is both a lexical and
semantic match. The API exposes only the fused score, so the deterministic RRF
test is the definitive verification of this behavior:

```bash
.venv/bin/python -m unittest \
  tests/test_search.py \
  -k test_rrf_rewards_documents_found_by_both_rankings \
  -v
```

## Relevance-Ranked Results

Each result collection is ordered by descending relevance. Results within the
`clients` collection can be compared with each other, as can results within
`documents`; client and document scores are not directly comparable.

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=investment portfolio dividends' \
  --data-urlencode 'limit=10' \
  | jq '.documents | map({title: .document.title, score})'
```

Check that the scores descend. The `limit` applies independently to both
result collections:

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=investment' \
  --data-urlencode 'limit=1' \
  | jq '{clients: (.clients | length), documents: (.documents | length)}'
```

## Chunk-Level Retrieval and Snippets

Documents are searched by chunk but returned once, using their best matching
chunk. The response includes an approximately 320-character snippet,
reconstructed from the original document and centered on a lexical match when
available.

```bash
curl --get http://localhost:8000/search \
  --data-urlencode 'q=liquidity reserves income' \
  --data-urlencode 'limit=10' \
  | jq '.documents | map({
      id: .document.id,
      title: .document.title,
      snippet,
      snippet_length: (.snippet | length)
    })'
```

Verify that each document ID occurs only once and that the matching terms are
visible in the short snippet. `Long Portfolio Handbook` contains multiple
matching chunks but should occur only once.

## Input Validation and Failures

A blank query, invalid limit, and duplicate email are rejected without a
server error:

```bash
curl -i --get http://localhost:8000/search \
  --data-urlencode 'q=   '

curl -i --get http://localhost:8000/search \
  --data-urlencode 'q=investment' \
  --data-urlencode 'limit=51'
```

Both requests should return `422 Unprocessable Content`. Reusing the test
client's email should return `409 Conflict`:

```bash
curl -i -X POST http://localhost:8000/clients \
  -H 'Content-Type: application/json' \
  -d "$(jq -n \
    --arg email "$CLIENT_EMAIL" \
    '{
      first_name: "Duplicate",
      last_name: "Client",
      email: $email
    }')"
```

Document ingestion returns `404` for an unknown client, `503` when the
embedding provider is not configured, and `502` when the provider fails.

## Automated Tests

The automated suite uses fake embeddings and does not require network access
or an OpenAI API key:

```bash
.venv/bin/python -m unittest discover -s tests -v
```
