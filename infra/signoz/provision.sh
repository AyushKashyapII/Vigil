#!/usr/bin/env bash
# Provisions Vigil's SigNoz dashboard, alert rule and webhook channel from
# the JSON committed next to this script -- the dashboard and alert are
# code, not clicks.
#
#   SIGNOZ_URL      default http://localhost:8080
#   SIGNOZ_API_KEY  or the file ~/.config/vigil/signoz-api-key
#                   (Settings -> Service Accounts -> <account> -> Keys).
#                   Never commit it.
#
# Idempotent: anything that already exists (matched by name) is skipped,
# not duplicated. To change one, edit it in the UI, then re-export it here.
set -euo pipefail

cd "$(dirname "$0")"
URL="${SIGNOZ_URL:-http://localhost:8080}"
KEY="${SIGNOZ_API_KEY:-$(cat ~/.config/vigil/signoz-api-key 2>/dev/null || true)}"
[ -n "$KEY" ] || { echo "no API key: set SIGNOZ_API_KEY or create ~/.config/vigil/signoz-api-key" >&2; exit 1; }

api() { # api METHOD PATH [JSON_FILE]
  curl -sS -X "$1" -H "SIGNOZ-API-KEY: $KEY" -H 'Content-Type: application/json' \
    ${3:+--data @"$3"} "$URL$2"
}
# has_name "<python expr yielding names from JSON d>" NAME  (reads JSON on stdin)
has_name() { python3 -c "import json,sys; d=json.load(sys.stdin)['data']; sys.exit(0 if sys.argv[2] in ($1) else 1)" "$1" "$2"; }
ok() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'], d.get('error',{}).get('message',''))"; }

# 1. Webhook channel -> alert-sink.py, which logs each notification payload.
#    Run the sink first:
#      docker run -d --name vigil-alert-sink --network signoz-network \
#        -v "$PWD/alert-sink.py:/sink.py:ro" python:3.12-slim python -u /sink.py
if api GET /api/v1/channels | has_name "[c['name'] for c in d]" vigil-alert-sink; then
  echo "channel vigil-alert-sink: exists, skipped"
else
  echo -n "channel vigil-alert-sink: "
  echo '{"name":"vigil-alert-sink","webhook_configs":[{"send_resolved":true,"url":"http://vigil-alert-sink:9000/alert"}]}' \
    > /tmp/vigil-channel.json
  api POST /api/v1/channels /tmp/vigil-channel.json | ok
fi

# 2. Dashboard
if api GET /api/v2/dashboards | has_name "[x['spec']['display']['name'] for x in d['dashboards']]" Vigil; then
  echo "dashboard Vigil: exists, skipped"
else
  echo -n "dashboard Vigil: "; api POST /api/v2/dashboards dashboards/vigil.json | ok
fi

# 3. Alert rules
for f in alerts/*.json; do
  name=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['alert'])" "$f")
  if api GET /api/v3/rules | has_name "[r['alert'] for r in d['rules']]" "$name"; then
    echo "alert '$name': exists, skipped"
  else
    echo -n "alert '$name': "; api POST /api/v2/rules "$f" | ok
  fi
done
