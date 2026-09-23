# Roadmap / deferred work

Things we've deliberately scoped out of the current build, so we don't
have to rediscover them later. Not in priority order. When something gets
picked up, move it out of here into the actual work.

## Rule refinements
- **`possible_missing_index` findings don't carry enough information for
  an automated fix.** The finding names the table getting sequentially
  scanned, but not which column the queries are actually filtering on --
  `pg_stat_user_tables` only has table-level scan counts, nothing
  column-level. `CREATE INDEX ON orders(???)` -- we don't know what goes
  in the parentheses. This wasn't caught when the rule was built because
  it was verified against `/orders-by-user`, and we already knew that
  query filters on `user_id` from having written the demo app ourselves --
  the test proved the *pattern* detection worked without ever proving the
  *finding* carries enough data for something else to act on it blind.
  Real fix needs either: collector correlating this against the actual
  query text hitting that table (from `pg_stat_statements`, which it
  already polls but doesn't persist raw, only rule matches) to extract the
  filtered column, or a more direct mechanism (periodic `EXPLAIN` sampling
  of slow queries against the table). Bigger than a one-line addition to
  brain's v1 -- `possible_unused_index` fix suggestions (which only need
  an index name, already have it) are unaffected and proceeding first.
- **Stale planner statistics detection was never built, for the same
  reason bloat detection is disabled.** The idea: `pg_stat_user_tables.
  n_mod_since_analyze` (rows changed since the last `ANALYZE`) as a
  fraction of table size would flag a table the planner's row-count
  estimates can no longer trust -- directly relevant to `/bulk-import-
  products`, which was built specifically to cause this. But autovacuum
  has a separate auto-analyze trigger (`autovacuum_analyze_scale_factor`,
  default ~10% modified) that resets `n_mod_since_analyze` back down when
  it fires -- the same race we already hit with bloat, predictable in
  advance this time. Needs the same realistic (bigger/busier, or
  auto-analyze deliberately disabled for a controlled test) scenario
  before it's worth building, not just adding for its own sake.
- **Bloat detection is built but disabled (commented out, not deleted) --
  needs a realistic test before it can be trusted.** Code lives in
  `tables.go`/`table_poller.go`/`detect.go` (`DetectBloat`,
  `BloatMinDeadRatio`/`BloatMinDeadTuples`), all commented out. Twice in
  testing on the demo app's small `inventory_logs` table, autovacuum
  cleaned up dead tuples before our threshold (20% dead, 1000+ dead
  tuples) was ever crossed -- the rule was never honestly exercised
  catching something real, only forced by manually disabling autovacuum.
  Before re-enabling: test against a bigger table and/or heavier
  concurrent write load, where autovacuum's default 20%-of-table trigger
  genuinely can't keep up (the realistic case this rule is meant for --
  large/busy tables, or autovacuum blocked by a long-running transaction,
  not small lightly-loaded ones).
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
- **Duplicate index detection -- deliberately deprioritized, not just
  deferred.** Every rule built so far detects something only visible by
  observing behavior *over time* (call rates, idle duration, scan
  patterns). A duplicate index is visible the instant it's created, from
  the schema alone -- no polling or time-series data needed. That makes it
  a different *kind* of tool (a schema/migration linter, or a one-time
  audit check), not a fit for a runtime-monitoring agent's continuous poll
  loop. Worth reconsidering only if Vigil ever grows a separate one-time
  "schema audit" mode, distinct from the rules that run every poll cycle.

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
- **Nothing in this stack actually routes through PgBouncer.** `demo-app`
  and the collector both connect straight to `postgres:5432`;
  `docker-compose.yml`'s `pgbouncer` service is running but bypassed by
  every real connection. This limits how meaningful
  `approaching_max_connections` and the not-yet-built PgBouncer pool-mode
  rule really are right now -- there's no pooling layer in the path to
  reason about. Fixing this (pointing app/collector traffic at
  `pgbouncer:6432` instead) would make both rules test against something
  real, and would also make the deliberately-wrong `POOL_MODE: session`
  test case actually exercised rather than just sitting unused in config.

## Entirely unbuilt (beyond collector)
- `brain` (Python/LLM component) -- still an empty stub.
- Docker sandbox that clones the schema and benchmarks proposed fixes.
- GitHub PR / Slack action executor.
