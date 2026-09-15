# Roadmap / deferred work

Things we've deliberately scoped out of the current build, so we don't
have to rediscover them later. Not in priority order. When something gets
picked up, move it out of here into the actual work.

## Rule refinements
- **Unused-index detection should eventually weigh maintenance cost, not
  just usage duration.** An index can get occasional real use and still be
  a net loss, since every INSERT/UPDATE/DELETE on the table pays a write
  cost for every index on it. A fuller version needs table write volume
  (`pg_stat_user_tables.n_tup_ins`/`n_tup_upd`/`n_tup_del` -- not currently
  collected) and index size (`pg_relation_size`, not currently collected),
  weighed against how rarely the index gets scanned. v1 is duration-only.
- **Poller's reset-guard has a small gap:** it only checks whether the
  *count* field (`Calls`/`SeqScan`/`IdxScan`) went backwards to detect a
  stats reset -- it doesn't independently check whether `TotalExecTime`/
  `Rows`/etc. moved backwards on their own. Observed once in the wild: a
  delta with `delta_calls=0` but `delta_total_exec_time=-0.01ms` and
  `delta_rows=-1`. Cosmetic so far, but the guard should probably check
  every counter field, not just the primary one.
- **Duplicate index detection** (via `pg_indexes`, comparing table+columns+
  method across indexes) -- structurally different from every rule so far:
  a definition comparison, not a threshold on a number.

## Infrastructure / hardening
- **Unused-index tracking state is in-memory only for v1** (a map, like
  `Poller`/`TablePoller` already use) -- resets on every collector restart,
  losing days of accumulated "last changed" history. A persisted version
  (SQLite, upsert per index) would survive restarts but needs `Store` to
  gain `UPDATE` capability, which it doesn't have yet (append-only so far).
- **No graceful shutdown.** `Ctrl+C` (or a container stop) kills the
  collector mid-poll -- no signal handling, no clean exit.
- **Poll interval and rule thresholds are hardcoded Go constants**, not
  configurable via env vars the way `COLLECTOR_DATABASE_URL`/
  `COLLECTOR_STORE_PATH` are.
- **Every source polls on the same single 5s ticker.** Fine for
  `pg_stat_statements`/`pg_stat_activity` where recent activity matters,
  wasteful for day-scale signals like unused-index tracking. Current plan:
  skip most ticks with a counter rather than add real per-source
  concurrency (goroutines/independent tickers) -- that's a legitimate
  future refactor if more slow-cadence sources get added.

## Structurally different future sources (not more of the same pattern)
- **Deadlock detection** -- lives in Postgres's log file, not any stats
  view. Needs log tailing/parsing, nothing like the SQL-polling built so
  far.
- **PgBouncer pool-mode recommendation** -- needs PgBouncer's own admin
  console (a separate connection, separate protocol), not Postgres at all.

## Entirely unbuilt (beyond collector)
- `brain` (Python/LLM component) -- still an empty stub.
- Docker sandbox that clones the schema and benchmarks proposed fixes.
- GitHub PR / Slack action executor.
