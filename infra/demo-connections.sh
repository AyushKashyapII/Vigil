#!/usr/bin/env bash
# Pushes Postgres past 80% of max_connections for a while, so the SigNoz
# alert on vigil.connections.utilization has something real to fire on.
#
# Each connection is a psql session parked in pg_sleep -- it shows up in
# pg_stat_activity as an active client connection, exactly like a leaked
# or stuck app connection would.
#
# Usage: ./demo-connections.sh [percent_of_max=90] [seconds=300]
set -euo pipefail

PCT="${1:-90}"
SECS="${2:-300}"
PG=infra-postgres-1

MAX=$(docker exec "$PG" psql -U postgres -Atc "SHOW max_connections")
CUR=$(docker exec "$PG" psql -U postgres -Atc "SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL")
# Leave headroom below max so the collector (and you) can still connect.
TARGET=$(( MAX * PCT / 100 ))
[ "$TARGET" -ge "$((MAX - 5))" ] && TARGET=$((MAX - 5))
N=$(( TARGET - CUR ))
[ "$N" -le 0 ] && { echo "already at $CUR/$MAX connections"; exit 0; }

echo "max_connections=$MAX, currently $CUR -> opening $N sessions for ${SECS}s (target ~$TARGET, $((TARGET * 100 / MAX))%)"
for _ in $(seq 1 "$N"); do
  docker exec -d "$PG" psql -U postgres -d demo -qc "SELECT pg_sleep($SECS)"
done
sleep 3
docker exec "$PG" psql -U postgres -Atc \
  "SELECT 'now: ' || count(*) || '/' || current_setting('max_connections') FROM pg_stat_activity WHERE state IS NOT NULL"
echo "They close on their own after ${SECS}s. To end early:"
echo "  docker exec $PG psql -U postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query LIKE 'SELECT pg_sleep%' AND pid <> pg_backend_pid()\""
