# ADR-003: Design for AWS Deployment

- Status: Accepted
- Date: 2026-08-12

## Context

The take-home service will be deployed to the candidate's AWS account for an
external demonstration. The exact AWS services will be selected after the local
implementation is working.

## Decision

Keep the application portable and AWS-ready from the start:

- Package the FastAPI service as a stateless Docker container.
- Store all persistent data in PostgreSQL with `pgvector`.
- Supply configuration and credentials through environment variables.
- Expose a health endpoint for container health checks.
- Run explicit database migrations during deployment.
- Write structured application logs to stdout.
- Avoid dependencies on local files or in-memory application state.

Secrets will not be committed or included in the container image.

## Consequences

The same image can run locally and on AWS. AWS service selection, networking,
TLS, secret storage, database backups, and infrastructure automation remain
deployment decisions to be made later, with cost and demo simplicity as key
constraints.
