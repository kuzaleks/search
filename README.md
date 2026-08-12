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

## Prerequisites

To be documented during implementation.

## Local Setup

To be documented during implementation.

## Running the API

To be documented during implementation.

## API Usage

Example requests and responses will be added as endpoints are implemented.

## Running Tests

To be documented during implementation.

## AWS Deployment

Deployment instructions and the demonstration endpoint will be added after the
local implementation is complete.

## Contributing

Keep changes focused, add tests for behavioural changes, and document material
architecture decisions as ADRs.
