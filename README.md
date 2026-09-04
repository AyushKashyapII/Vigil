# Vigil

Vigil is an observability and auto-remediation toolkit for applications and
their databases. A Go collector gathers runtime and database metrics (e.g.
via `pg_stat_statements`), a Python brain parses that data and uses an LLM
to reason about anomalies and propose fixes, and a demo app is included so
the whole pipeline can be exercised end to end.

## Getting started

> These are placeholder steps — real setup instructions will land as each
> component is implemented.

1. Clone the repo.
2. From `infra/`, run:
   ```
   docker compose up
   ```
3. Once running, check the demo app health endpoint:
   ```
   curl http://localhost:8000/health
   ```
4. (Collector and brain services will be added to `infra/docker-compose.yml`
   once they have real code.)
