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
- **The unused-index write benchmark has two known scope limits, both
  deliberate for v1, not oversights.** (1) It only benchmarks the first
  column of a multi-column index (parsed from `pg_indexes.indexdef`) --
  fine for the single-column case that's the only one actually built
  today, but a multi-column index's real write cost isn't fully captured.
  (2) The row sample (`ctid IN (SELECT ctid ... LIMIT N)`) is physical
  order, not random -- consistent enough for a fair before/after
  comparison within one sandbox run, but not a statistically
  representative sample of the table. Revisit if/when multi-column
  indexes or a more rigorous sampling need shows up.
- **Duplicate index detection -- deliberately deprioritized, not just
  deferred.** Every rule built so far detects something only visible by
  observing behavior *over time* (call rates, idle duration, scan
  patterns). A duplicate index is visible the instant it's created, from
  the schema alone -- no polling or time-series data needed. That makes it
  a different *kind* of tool (a schema/migration linter, or a one-time
  audit check), not a fit for a runtime-monitoring agent's continuous poll
  loop. Worth reconsidering only if Vigil ever grows a separate one-time
  "schema audit" mode, distinct from the rules that run every poll cycle.

- **Query-rewrite fixes (`nested_subquery`, sandbox-verified) don't get PR
  drafts yet.** `actions/pr.py` generates an Alembic migration -- the
  right shape for a schema change (an index), completely the wrong shape
  for a rewrite, which means editing the literal SQL inside application
  source (`demo-app/app/main.py`). Needs a source-diff generator, not a
  migration generator -- a genuinely different action, not an extension
  of the existing one. Stays console-only for now.

- **`possible_n_plus_one`'s deterministic fix only covers the exact
  shape `SELECT ... FROM table WHERE table.column = $1`** -- a single
  table, a single equality condition, nothing else. Real N+1s with a
  join in the per-row query, a compound WHERE clause, or a composite key
  fall through unhandled rather than being guessed at. Widening this
  would mean either loosening the shape check (risking an incorrect
  batching that isn't actually equivalent to the original per-row
  semantics) or routing non-matching shapes through the LLM path like
  nested_subquery -- not done yet.

- **`possible_unbounded_query` still has a smaller residual false-positive
  source after the SELECT-prefix guard fix:** `pg_dump`'s own schema
  introspection queries (e.g. against `pg_catalog.pg_description`,
  `pg_proc`) *are* real SELECTs, so the guard that eliminated its `COPY`
  false positives doesn't touch these. Deliberately not filtered by
  schema name (`pg_catalog`/`information_schema`) for now -- picked the
  narrower, more consistent fix (matching the exact precedent set by
  `possible_n_plus_one`'s BEGIN/COMMIT guard) over a broader one. Revisit
  if this noise turns out to matter in practice.

## Infrastructure / hardening
- **Findings have no resolution/expiry concept -- the store only ever
  grows, and `latest_by_subject`'s "last recorded wins" dedup means a
  finding can outlive the condition that caused it.** Two distinct ways
  this bites: (1) detection logic changes (e.g. a correlation-bug fix)
  leave old findings recorded under the old, now-wrong logic sitting in
  the store forever, since nothing ever recorded a fresher finding for
  that exact `(rule, subject)` key to supersede them; (2) even with
  perfect logic, a real-world condition can resolve on its own (someone
  manually adds the missing index, an unused index starts getting
  scanned again) and nothing marks the old finding stale -- brain has no
  way to know the world moved on. Discovered when a stale
  `possible_missing_index` finding recorded before a collector bugfix
  crashed the sandbox verification pipeline on a column that no longer
  applied. Needs either an expiry/TTL (ignore findings older than N
  cycles without reconfirmation) or the collector actively re-affirming
  or retracting findings each poll, not just adding new ones. Not fixed
  now -- one-off truncation of the dev store is enough to unblock local
  testing, but this will recur in a real deployment.
- **`brain/main.py`'s per-finding loop has no error isolation -- one bad
  finding (unparseable subject, invalid SQL, a benchmark query that no
  longer matches the schema) throws and kills the entire pipeline run**
  instead of skipping that finding and continuing with the rest. Fixed
  by wrapping the suggest/verify step in try/except per finding.
- **The sandbox's `SANDBOX_DATABASE_URL` reuses the same superuser
  credential as everything else in this dev stack.** This is a real
  capability increase for brain -- it went from "only reads collector's
  SQLite store" to "has `CREATEDB` on the monitored database." Fine for a
  local dev/demo credential; a real deployment should scope this to a
  dedicated sandbox role, kept separate from collector's ideally-read-only
  monitoring role (matches the least-privilege design principle already
  in the root README).
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
