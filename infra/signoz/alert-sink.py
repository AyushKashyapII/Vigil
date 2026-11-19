"""Tiny webhook receiver for SigNoz alert notifications (demo/dev only).

Logs every POST body to stdout so `docker logs vigil-alert-sink` shows the
exact payload SigNoz's Alertmanager sent -- proof an alert didn't just turn
red in the UI but actually went out over a notification channel. Stdlib
only; see infra/signoz/README.md for how it's run.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Sink(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            payload = json.loads(body)
            for a in payload.get("alerts", []):
                print(f"[{a.get('status')}] {a.get('labels', {}).get('alertname')} "
                      f"value={a.get('annotations', {}).get('summary')} startsAt={a.get('startsAt')}")
            print(json.dumps(payload, indent=2), flush=True)
        except ValueError:
            print(body.decode(errors="replace"), flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # silence per-request access log noise
        pass


HTTPServer(("0.0.0.0", 9000), Sink).serve_forever()
