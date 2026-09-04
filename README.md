# Vigil

**An autonomous PostgreSQL operations agent that proves its optimizations before suggesting them.**

Vigil watches your database's live statistics, identifies slow queries, inefficient indexes, connection leaks, and deadlocks, then — instead of just alerting you — spins up an isolated sandbox to mathematically verify that its proposed fix actually helps before opening a GitHub PR or paging you on Slack.

No guessing. No "the AI thinks this might work." Every suggestion is benchmarked against a disposable clone of your schema first, and discarded quietly if it doesn't hold up.

---

## What it does

Vigil is scoped to one problem: **optimizing database operations, safely and provably.**

### Query efficiency
- Detects N+1 query patterns from ORMs (Prisma, SQLAlchemy, Django ORM, etc.)
- Flags unnecessarily nested subqueries that can be rewritten as joins
- Catches queries missing `LIMIT` or proper filtering that cause full-table scans
- Flags query plan regressions caused by stale planner statistics

### Index management
- Detects sequential scans and proposes the correct index type (B-Tree, Hash, GiST)
- Flags redundant or duplicate indexes
- Finds indexes unused for 30+ days and proposes dropping them
- Supports pgvector index health (HNSW / IVFFlat) where applicable

### Connections
- Detects connection leaks (`idle in transaction`)
- Detects deadlocks between competing transactions
- Flags approaching `max_connections` limits
- Recommends PgBouncer pool mode based on observed connection churn

### Storage & maintenance
- Detects table and index bloat from dead tuples (MVCC)
- Schedules `VACUUM ANALYZE` during low-traffic windows when bloat crosses a threshold

---

## How it works

```
Postgres (pg_stat_statements, pg_stat_activity, pg_stat_user_indexes)
        │
        ▼
Go collector — polls metrics, runs rule-based detection
        │
        ▼
Python brain — parses query AST, asks an LLM to propose a fix
        │
        ▼
Docker sandbox — clones schema, seeds synthetic data,
                  benchmarks the fix before/after
        │
   ┌────┴────┐
   ▼         ▼
verified   discarded
   │        (logged, no action taken)
   ▼
Go action executor
   │
   ├── GitHub PR (query rewrite / index change, with benchmark numbers)
   └── Slack alert (live issues: leaks, deadlocks — human-gated actions only)
```

Live issues (connection leaks, deadlocks) bypass the sandbox entirely and go straight to an alert, since there's nothing to benchmark in an active emergency — the agent proposes the fix (e.g. killing a PID) but never executes it without explicit human approval.

---

## Project structure

```
vigil/
├── collector/       # Go — metrics polling, rule engine, GitHub/Slack actions
├── brain/           # Python — LLM orchestration, AST parsing, sandbox benchmarking
├── demo-app/         # FastAPI + SQLAlchemy app with deliberately bad queries, used to test Vigil
├── infra/           # docker-compose setup for Postgres + demo-app + Vigil services
└── docs/            # architecture notes and design decisions
```

---

## Prerequisites

- Docker & Docker Compose
- Go 1.22+
- Python 3.11+
- A GitHub personal access token (for PR creation)
- A Slack webhook URL (for alerts)

---

## Getting started

```bash
# clone the repo
git clone https://github.com/<your-username>/vigil.git
cd vigil

# bring up Postgres (with pg_stat_statements enabled) and the demo app
cd infra
docker compose up -d

# seed the demo app with test data
cd ../demo-app
python seed.py

# generate some bad query traffic so pg_stat_statements has data to work with
python load.py
```

Verify Postgres is collecting stats:

```bash
docker exec -it vigil-postgres psql -U postgres -d dev_db \
  -c "SELECT query, calls, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 5;"
```

Once this returns rows, the collector and brain services can be built against real data.

---

## Design principles

- **Prove it, don't trust it.** Every optimization suggestion is benchmarked in an isolated sandbox before it's ever surfaced to a human.
- **Least privilege by default.** The agent connects with a read-only monitoring role (`pg_monitor`) for detection; any destructive or write action requires a separately scoped, explicitly approved credential.
- **Human-gated destructive actions.** The agent never runs `DROP INDEX`, kills a PID, or executes a migration on its own — it proposes, and a human approves.
- **Read-only fixes can run autonomously.** Non-destructive actions like `ANALYZE` (which can only improve planner statistics, never lose data) are safe to automate without a human in the loop.

---

## Demo app

`demo-app/` is a "victim" application that exists purely to generate realistic bad
query patterns for Vigil to detect and fix. Every endpoint below is deliberately
inefficient or broken — that's the point, not a bug.

### What each endpoint demonstrates

- `GET /orders-bad` — N+1 queries: fetches orders, then lazy-loads `order.user` per row.
- `GET /orders-good` — the same result via a single `JOIN`, for comparison.
- `GET /order-summary-nested/{user_id}` — per-order correlated subqueries instead of a `JOIN` + `GROUP BY`.
- `GET /orders-by-user/{user_id}` — filters on `orders.user_id`, which has no index.
- `GET /all-inventory-logs` — returns the entire table, no `LIMIT`, no pagination.
- `POST /bulk-import-products` — bulk-inserts fake products in one commit, producing stale planner statistics.
- `GET /reviews-by-product/{product_id}` — filters on `reviews.product_id`, which has no index.
- `GET /leaky` — opens a raw `Session` outside the request lifecycle and never closes it, leaking a connection.
- `POST /reserve-stock/{product_id}` — locks product, then order, sleeping in between to widen the race window.
- `POST /log-order/{order_id}` — locks order, then product (opposite order from above) — pairs with it to produce a deadlock.
- `POST /slow-transaction` — holds a connection open for 5s on `pg_sleep(5)`; hammer it concurrently to exhaust the pool.
- `POST /simulate-churn` — rapidly inserts and deletes `inventory_logs` rows to generate dead tuples/bloat, without ever vacuuming.
- `GET /search-similar-products?q=...` — pgvector similarity search, useful for demonstrating the empty-table HNSW index bug (see below).

Schema-level flaws (applied by `app/db_init.py`, not the endpoints above):

- Duplicate indexes on `products.category` (`idx_products_category` / `idx_products_cat_dup`).
- An index on `users.last_login_at` that nothing ever queries.
- An HNSW index on `product_embeddings` built *before* any embeddings exist, reproducing pgvector's empty-table garbage-centroids bug.

### How to run

> **Note:** the run order here differs from a natural "seed first" instinct —
> `db_init.py` must run *before* `seed.py`, because the HNSW empty-table bug
> only reproduces if the index is built while `product_embeddings` is still
> empty.

```bash
cd infra
docker compose up -d

cd ../demo-app
python -m app.db_init      # applies schema + deliberate flaws (must run first)
python seed.py             # seeds users/products/orders/etc.
python load.py              # in a separate terminal: generates continuous traffic
```

To reproduce the deadlock, call `/reserve-stock/{product_id}` and `/log-order/{order_id}`
**concurrently** (e.g. from two terminals at once) using a product and order that share
an `order_items` row — look one up first:

```bash
psql ... -c "SELECT order_id, product_id FROM order_items LIMIT 1;"

# terminal 1
curl -X POST http://localhost:8000/reserve-stock/<product_id>
# terminal 2, started immediately after
curl -X POST http://localhost:8000/log-order/<order_id>
```

---
