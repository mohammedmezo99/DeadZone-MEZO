#!/usr/bin/env python3
"""Deterministic tests for the DeadZone SuperInspector callback emitter.

These tests cover the signing contract that previously caused the
``403 Forbidden`` response on the bot callback endpoint.

Run:
    python3 .github/scripts/test_dz_emit_callback.py
or under pytest:
    pytest .github/scripts/test_dz_emit_callback.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from typing import Any
from unittest import mock

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import dz_emit_callback  # noqa: E402


def _env(**extra: str) -> dict[str, str]:
    base = {
        "DEADZONE_CALLBACK_URL": "https://deadzone-bot.example.workers.dev/events",
        "DEADZONE_CALLBACK_SECRET": "super-secret-1234",
        "DEADZONE_JOB_ID": "super_e2e_test00001",
        "DEADZONE_INSPECTOR_REF": "abcdef1234567890",
        "DEADZONE_PROFILE_ID": "deadbeef00010203",
        "DEADZONE_TARGET_REPO": "mohammedmezo99/DeadZone-MEZO",
    }
    base.update(extra)
    return base


@contextmanager
def _apply(extra: dict[str, str]):  # type: ignore[no-untyped-def]
    saved = {k: os.environ.get(k) for k in extra}
    try:
        for k, v in extra.items():
            os.environ[k] = v
        yield
    finally:
        for k, prior in saved.items():
            if prior is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prior


def test_sign_includes_timestamp_prefix() -> None:
    secret = "abcdef"
    body = '{"x":1}'
    ts = "1700000000"
    sig = dz_emit_callback._sign(secret, body, ts)
    assert sig.startswith("sha256=")
    digest = sig.split("=", 1)[1]
    expected = hmac.new(
        secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    assert digest == expected, "HMAC payload must be f'{timestamp}.{body}'"


def test_sign_changes_with_timestamp_and_body() -> None:
    body = '{"x":1}'
    base = dz_emit_callback._sign("k", body, "1")
    assert base != dz_emit_callback._sign("k", body, "2")
    assert base != dz_emit_callback._sign("k", body + " ", "1")


def test_secret_fallback_chain() -> None:
    assert dz_emit_callback._secret_from_env() == ""
    with _apply({"DEADZONE_CALLBACK_SECRET": "primary"}):
        assert dz_emit_callback._secret_from_env() == "primary"
    with _apply({"DEADZONE_EVENT_SECRET": "evt"}):
        assert dz_emit_callback._secret_from_env() == "evt"
    with _apply({"BUILD_PROGRESS_SECRET": "bld"}):
        assert dz_emit_callback._secret_from_env() == "bld"


def test_canonical_payload_uses_env_metadata() -> None:
    with _apply(_env()):
        payload = dz_emit_callback._canonical_payload(
            "download.started", "download", "fetching ROM", {}
        )
    assert payload["type"] == "download.started"
    assert payload["stage"] == "download"
    assert payload["error"] == "fetching ROM"
    assert payload["jobId"] == "super_e2e_test00001"
    assert payload["inspectorRef"] == "abcdef1234567890"
    assert payload["profileId"] == "deadbeef00010203"
    assert payload["target"] == "mohammedmezo99/DeadZone-MEZO"


def test_canonical_payload_serialization_is_stable() -> None:
    with _apply(_env()):
        payload = dz_emit_callback._canonical_payload(
            "analyze.completed", "analyze", "OK", {"extra": "v"}
        )
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    assert '"extra":"v"' in body
    keys = sorted(payload.keys())
    body_keys = [
        line.split(":", 1)[0].strip().strip('"')
        for line in body.strip("{}").split(",")
    ]
    assert body_keys == keys


def test_emit_sends_x_deadzone_signature_and_timestamp_headers() -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, body, headers):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = {k.lower(): v for k, v in headers.items()}
        return 200, ""

    with _apply(_env()):
        with mock.patch.object(dz_emit_callback, "_post", wraps=fake_post):
            rc = dz_emit_callback.emit("setup.started", "setup", "begin")

    assert rc == 0
    headers = captured["headers"]
    assert headers["content-type"] == "application/json"
    assert "x-deadzone-timestamp" in headers
    assert "x-deadzone-signature" in headers
    sig_header = headers["x-deadzone-signature"]
    assert sig_header.startswith("sha256=")

    body = captured["body"].decode("utf-8")
    ts = headers["x-deadzone-timestamp"]
    secret = _env()["DEADZONE_CALLBACK_SECRET"]
    expected = hmac.new(
        secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    assert sig_header == f"sha256={expected}", "signature must match runtime contract"


def test_emit_skips_when_secret_or_url_missing() -> None:
    with _apply({}):
        os.environ.pop("DEADZONE_CALLBACK_SECRET", None)
        os.environ.pop("DEADZONE_EVENT_SECRET", None)
        os.environ.pop("BUILD_PROGRESS_SECRET", None)
        rc = dz_emit_callback.emit("x", "y", "z")
    assert rc == 0


def test_emit_does_not_block_on_4xx() -> None:
    with _apply(_env()):
        with mock.patch.object(
            dz_emit_callback, "_post", return_value=(403, "Forbidden")
        ):
            rc = dz_emit_callback.emit("analyze.failed", "analyze", "nope")
    assert rc == 0


def test_emit_retries_only_transient_statuses() -> None:
    calls: list[int] = []

    def fake_post(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        return 403, "Forbidden"

    with _apply(_env()):
        with mock.patch.object(dz_emit_callback, "_post", side_effect=fake_post):
            dz_emit_callback.emit("analyze.failed", "analyze", "nope")
    assert len(calls) == 1, "non-transient 4xx must not retry"


def test_emit_retries_transient_5xx() -> None:
    calls: list[int] = []

    def fake_post(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        return 503, "downstream"

    sleeps: list[float] = []
    with _apply(_env()):
        with mock.patch.object(dz_emit_callback, "_post", side_effect=fake_post):
            with mock.patch.object(dz_emit_callback.time, "sleep", side_effect=sleeps.append):
                dz_emit_callback.emit("analyze.failed", "analyze", "nope")
    assert len(calls) == dz_emit_callback.RETRY_ATTEMPTS
    assert sleeps, "transient 5xx must back off"


def test_main_dispatches_cli_args() -> None:
    with _apply(_env()):
        with mock.patch.object(
            dz_emit_callback, "emit", return_value=0
        ) as spy:
            rc = dz_emit_callback.main(
                [
                    "dz_emit_callback.py",
                    "download.failed",
                    "download",
                    "curl exit 22",
                    "extra=foo",
                ]
            )
    assert rc == 0
    spy.assert_called_once()
    args = spy.call_args.args
    assert args[0] == "download.failed"
    assert args[1] == "download"
    assert args[2] == "curl exit 22"
    assert args[3] == {"extra": "foo"}


def test_main_dispatches_with_callback_file(tmpdir: str) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, body, headers):  # type: ignore[no-untyped-def]
        captured["body"] = body
        captured["headers"] = {k.lower(): v for k, v in headers.items()}
        return 202, ""

    payload_path = os.path.join(tmpdir, "cb.json")
    with open(payload_path, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"type": "analyze.completed", "jobId": "j1", "profile": {"x": 1}})
        )
    with _apply(_env()):
        with mock.patch.object(dz_emit_callback, "_post", wraps=fake_post):
            rc = dz_emit_callback.main(
                [
                    "dz_emit_callback.py",
                    "--callback-file",
                    str(payload_path),
                    "analyze.completed",
                    "analyze",
                    "ROM analysis completed",
                ]
            )
    assert rc == 0
    body = captured["body"]
    parsed = json.loads(body)
    # The pre-built payload is signed verbatim - the emitter does NOT
    # rebuild the envelope.
    assert parsed["type"] == "analyze.completed"
    assert parsed["profile"] == {"x": 1}


def _run_subprocess_cli() -> None:
    """Smoke test invoking the script as a real subprocess against a stub
    HTTP server. The stub captures the outgoing request so we can assert
    the signing contract end-to-end."""
    import http.server
    import socket
    import threading

    class _Stub(http.server.BaseHTTPRequestHandler):  # type: ignore[misc]
        captured: dict[str, Any] = {}

        def do_POST(self):  # type: ignore[no-untyped-def]
            length = int(self.headers.get("content-length") or 0)
            self.rfile.read(length)
            _Stub.captured = {
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body_len": length,
            }
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return

    # Bind to a free ephemeral port.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = http.server.HTTPServer(("127.0.0.1", port), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = os.path.join(SCRIPT_DIR, "dz_emit_callback.py")
        with _apply({**_env(), "DEADZONE_CALLBACK_URL": f"http://127.0.0.1:{port}/events"}):
            # 3 mandatory positional args (type/stage/message); the
            # trailing ``extra=foo`` pair is optional and exercises the
            # extras parser as well.
            proc = subprocess.run(
                [
                    sys.executable,
                    script,
                    "setup.started",
                    "setup",
                    "worker ready",
                    "extra=foo",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        headers = _Stub.captured["headers"]
        assert "x-deadzone-signature" in headers
        assert "x-deadzone-timestamp" in headers
    finally:
        server.shutdown()


if __name__ == "__main__":
    test_sign_includes_timestamp_prefix()
    test_sign_changes_with_timestamp_and_body()
    test_secret_fallback_chain()
    test_canonical_payload_uses_env_metadata()
    test_canonical_payload_serialization_is_stable()
    test_emit_sends_x_deadzone_signature_and_timestamp_headers()
    test_emit_skips_when_secret_or_url_missing()
    test_emit_does_not_block_on_4xx()
    test_emit_retries_only_transient_statuses()
    test_emit_retries_transient_5xx()
    test_main_dispatches_cli_args()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        test_main_dispatches_with_callback_file(tmp)
    _run_subprocess_cli()
    print("dz_emit_callback tests: OK")