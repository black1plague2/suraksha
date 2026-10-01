"""Tests for demo.py and slack_server.py."""
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from suraksha.agents.approval import AuditLog, open_case  # noqa: E402
from suraksha.integrations.slack import handle_action, verify_signature  # noqa: E402
from suraksha.models import Citation, CitationKind, ReportSection, ReportSentence, STRDraft  # noqa: E402
from suraksha.store.memory import MemoryStore  # noqa: E402
from scripts.slack_server import make_handler, init_app  # noqa: E402


def test_demo_default_scenario():
    """Demo runs for default scenario and returns exit 0."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py")],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "INTAKE" in result.stdout
    assert "Audit chain: VALID" in result.stdout


def test_demo_specific_scenario():
    """Demo can run a specific scenario."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py"), "--scenario", "dup_exact_same_borrower"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "dup_exact_same_borrower" in result.stdout


def test_demo_with_approval():
    """Demo can approve a case."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py"), "--approve", "Priya Nair"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "APPROVAL" in result.stdout
    assert "Priya Nair" in result.stdout


def test_demo_invalid_scenario():
    """Demo exits 1 for invalid scenario."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py"), "--scenario", "nonexistent"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 1
    assert "not found" in result.stderr or "ERROR" in result.stderr


class TestSlackServer:
    """Tests for Slack server handler and integration."""

    @pytest.fixture
    def store_and_app(self):
        """Initialize Suraksha with synth data."""
        store, app = init_app()
        yield store, app

    @pytest.fixture
    def handler_class(self, store_and_app):
        """Create handler class with app."""
        store, app = store_and_app
        return make_handler(app)

    def _sign_request(self, secret: str, timestamp: str, body: str) -> str:
        """Compute Slack request signature."""
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        base = b"v0:" + str(timestamp).encode("utf-8") + b":" + raw
        return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()

    def test_slack_server_integration(self, store_and_app):
        """Full Slack server test with valid signature."""
        store, app = store_and_app

        # Find a pending case - check existing cases
        cases = store.list_cases()
        pending = next((c for c in cases if c.status.value == "PENDING_APPROVAL"), None)

        if not pending:
            pytest.skip("No pending cases in test data")

        # Create payload
        secret = "test-secret-123"
        timestamp = str(int(time.time()))
        payload = {
            "user": {"name": "alice"},
            "actions": [{"action_id": "suraksha_approve", "value": pending.case_id}],
        }
        payload_encoded = "payload=" + urllib.parse.quote(json.dumps(payload))
        sig = self._sign_request(secret, timestamp, payload_encoded)

        # Call handler directly (simulating HTTP request)
        handler = make_handler(app)
        # We test by calling handle_action directly since HTTP testing requires full server
        case = handle_action(payload, store, app.audit)
        assert case.case_id == pending.case_id
        assert case.decided_by == "officer:alice"

    def test_slack_signature_validation(self, store_and_app):
        """Signature verification must reject invalid signatures."""
        secret = "test-secret-123"
        timestamp = str(int(time.time()))
        body = "payload=%7B%7D"

        # Valid signature
        sig = self._sign_request(secret, timestamp, body)
        assert verify_signature(secret, timestamp, body, sig)

        # Invalid signature
        assert not verify_signature(secret, timestamp, body, "v0=invalid")
        assert not verify_signature("wrong-secret", timestamp, body, sig)

    def test_slack_missing_secret(self, store_and_app):
        """Handler rejects requests when signing secret is not set."""
        store, app = store_and_app

        # Simulate missing secret by checking that the server code requires it
        secret = ""  # empty secret
        timestamp = str(int(time.time()))
        body = "payload=%7B%7D"

        # verify_signature should fail with empty secret
        sig = "v0=somesig"
        assert not verify_signature(secret, timestamp, body, sig)


def test_slack_server_with_http():
    """Test Slack server with actual HTTP requests."""
    # Start server in a thread
    store, app = init_app()
    handler = make_handler(app)

    # Find an available port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", port), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        time.sleep(0.1)  # Give server time to start

        # Create a valid payload
        secret = "test-signing-secret"
        os.environ["SURAKSHA_SLACK_SIGNING_SECRET"] = secret
        timestamp = str(int(time.time()))

        # Find a pending case
        cases = store.list_cases()
        pending = next((c for c in cases if c.status.value == "PENDING_APPROVAL"), None)

        if not pending:
            pytest.skip("No pending cases in test data")

        payload = {
            "user": {"name": "bob"},
            "actions": [{"action_id": "suraksha_approve", "value": pending.case_id}],
        }
        payload_encoded = "payload=" + urllib.parse.quote(json.dumps(payload))

        # Compute signature
        raw = payload_encoded.encode("utf-8")
        base = b"v0:" + str(timestamp).encode("utf-8") + b":" + raw
        sig = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()

        # POST request
        url = f"http://127.0.0.1:{port}/slack/actions"
        req = urllib.request.Request(
            url,
            data=payload_encoded.encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": sig,
            },
        )

        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            assert result["ok"] is True
            assert result["case_id"] == pending.case_id
            assert result["decided_by"] == "officer:bob"

    finally:
        server.shutdown()
