"""Minimal HTTP server endpoint for Slack interactivity.

Usage: python scripts/slack_server.py [--port 3000]

POST /slack/actions endpoint:
- Reads raw body
- Verifies X-Slack-Signature with SURAKSHA_SLACK_SIGNING_SECRET
- Parses payload= form field JSON
- Calls slack.handle_action()
- Returns 200 JSON with new case status

In-process Suraksha pipeline over synth data (state lost on restart).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from suraksha.integrations.slack import handle_action, verify_signature  # noqa: E402
from suraksha.pipeline import Suraksha  # noqa: E402
from suraksha.store.memory import MemoryStore  # noqa: E402
from suraksha.synth import generate, load_into  # noqa: E402


# Global app state
_store = None
_app = None
_audit = None


def init_app() -> tuple[MemoryStore, Suraksha]:
    """Initialize store and pipeline with synth data."""
    store = MemoryStore()
    ds = generate(seed=42)
    load_into(store, ds)
    app = Suraksha(store)
    # Pre-process requests to create some cases
    for req in ds.requests[:30]:
        app.process(req)
    return store, app


def make_handler(app: Suraksha):
    """Factory to create a request handler with access to the Suraksha app."""

    class SlackActionHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            """Suppress default logging."""
            pass

        def do_POST(self):
            if self.path != "/slack/actions":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(json.dumps({"error": "not found"}).encode())
                return

            # Read raw body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            # Get signature header
            sig = self.headers.get("X-Slack-Signature", "")
            ts = self.headers.get("X-Slack-Request-Timestamp", "")

            # Get signing secret
            secret = os.getenv("SURAKSHA_SLACK_SIGNING_SECRET", "")
            if not secret:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(json.dumps({"error": "signing secret not configured"}).encode())
                return

            # Verify signature
            if not verify_signature(secret, ts, body, sig):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(json.dumps({"error": "invalid signature"}).encode())
                return

            # Parse payload
            try:
                decoded = urllib.parse.parse_qs(body.decode("utf-8"))
                payload_json = decoded.get("payload", [""])[0]
                payload = json.loads(payload_json)
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"invalid payload: {e}"}).encode())
                return

            # Handle action
            try:
                case = handle_action(payload, app.store, app.audit)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                out = {
                    "ok": True,
                    "case_id": case.case_id,
                    "status": case.status.value,
                    "decided_by": case.decided_by,
                }
                self.wfile.write(json.dumps(out).encode())
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())

    return SlackActionHandler


def main(args: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Slack interactivity server")
    ap.add_argument("--port", type=int, default=3000, help="HTTP port")
    a = ap.parse_args(args)

    print(f"Initializing Suraksha pipeline...", file=sys.stderr)
    store, app = init_app()

    print(f"Starting HTTP server on port {a.port}...", file=sys.stderr)
    print(f"POST /slack/actions for approvals", file=sys.stderr)
    print(f"SURAKSHA_SLACK_SIGNING_SECRET={os.getenv('SURAKSHA_SLACK_SIGNING_SECRET', 'NOT SET')}", file=sys.stderr)

    handler = make_handler(app)
    server = HTTPServer(("127.0.0.1", a.port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
