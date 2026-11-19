#!/usr/bin/env bash
# Reproduces the "97% orphaned statements" bug on purpose, so the
# vigil.statements.orphaned gauge has something to show.
#
# Does what Vigil's own sandbox does: create a throwaway database, run
# queries in it (each distinct query shape becomes its own
# pg_stat_statements entry), then DROP it. pg_stat_statements does not
# clean up entries for dropped databases -- they stay behind as orphans.
#
# Usage: ./demo-orphans.sh [num_tables]   (default 200 -> ~400 orphans)
# Cleanup: SELECT pg_stat_statements_reset();   (resets ALL stats, incl. real ones)
set -euo pipefail

N="${1:-200}"
DB="vigil_sandbox_orphan_demo_$$"
PSQL=(docker exec -i infra-postgres-1 psql -U postgres -v ON_ERROR_STOP=1 -q)

"${PSQL[@]}" -d demo -c "CREATE DATABASE $DB"

# Distinct table names => distinct query shapes => distinct queryids.
# (Same shape with different constants would normalise to ONE entry.)
{
  for i in $(seq 1 "$N"); do
    echo "CREATE TABLE t$i (id int);"
    echo "SELECT count(*) FROM t$i;"
  done
} | "${PSQL[@]}" -d "$DB" >/dev/null

"${PSQL[@]}" -d demo -c "DROP DATABASE $DB"

"${PSQL[@]}" -d demo -Atc "
  SELECT 'orphaned now: ' || count(*) FILTER (WHERE dbid NOT IN (SELECT oid FROM pg_database))
         || ' of ' || count(*) || ' total entries (max ' || current_setting('pg_stat_statements.max') || ')'
  FROM pg_stat_statements(false)"
echo "Watch vigil.statements.orphaned in SigNoz -- it should step up within ~15s."
