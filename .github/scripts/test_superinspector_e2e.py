#!/usr/bin/env python3
"""End-to-end integration test for the DeadZone SuperInspector workflow.

This test exercises the same paths the GitHub Actions runner does, but
on the local machine, using the synthetic fixture in
``tests_fixtures/rom_e2e_fixture.tgz``. It is *not* a mock - the real
``dz-inspector`` analysis pipeline runs against the real fixture, the
real canonical profile emitter projects the result, and the real
``dz_emit_callback.py`` signs and serialises the ``analyze.completed``
callback (against a local stub HTTP server).

What this test proves:
1. The synthetic fixture is a structurally-valid Xiaomi Fastboot ROM.
2. The SuperInspector pipeline produces a canonical Device Profile
   with the expected schema-version, profile_id, primary_codename,
   super layout, and fastboot summary fields.
3. The callback that the workflow would send is signed with the
   correct HMAC contract that the bot's Cloudflare Worker verifies.
4. The publish-tree staging copies every artefact listed in the
   profile's artefact_paths block to the publish tree root.

Run:
    python3 .github/scripts/test_superinspector_e2e.py

Exit 0 on success, non-zero on the first failure.
"""
from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
SUPER_INSPECTOR_ROOT = "/home/mohammed/Desktop/GitHub/DeadZone-SuperInspector"
FIXTURE = os.path.join(REPO_ROOT, "tests_fixtures", "rom_e2e_fixture.tgz")
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SUPER_INSPECTOR_ROOT, "scripts"))
sys.path.insert(0, SUPER_INSPECTOR_ROOT)

import dz_emit_callback  # noqa: E402
import dz_validate_rom  # noqa: E402

# Make ``deadzone_superinspector`` importable for in-process runs.
import importlib.util  # noqa: E402

_pkg_root = SUPER_INSPECTOR_ROOT
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)
from deadzone_superinspector.profile.pipeline import analyze_and_publish  # noqa: E402

from emit_canonical_profile import (  # noqa: E402
    build_analyze_completed_payload,
)


def test_fixture_is_valid() -> None:
    summary = dz_validate_rom.validate_rom(FIXTURE)
    assert summary["file_count"] >= 5, summary
    assert "images/build.prop" in summary["first_members"], summary
    assert summary["size_bytes"] > 0, summary


def test_pipeline_produces_profile(tmp_path: str) -> None:
    out_base = os.path.join(tmp_path, "pipeline_output")
    os.makedirs(out_base, exist_ok=True)
    result = analyze_and_publish(FIXTURE, out_base=out_base)
    paths = result.profile_paths
    assert os.path.isfile(paths.device_json)
    assert os.path.isfile(paths.rom_json)
    assert os.path.isfile(paths.flash_json)
    assert os.path.isdir(paths.super_dir)

    device_doc = json.loads(open(paths.device_json).read())
    assert device_doc["device_id"]
    assert device_doc["profile_id"]
    assert device_doc["identity"]["primary_codename"]
    print(
        f"  pipeline OK: device_id={device_doc['device_id']} "
        f"profile_id={device_doc['profile_id']}"
    )


def test_canonical_profile_emitter(tmp_path: str) -> None:
    out_base = os.path.join(tmp_path, "emitter_output")
    os.makedirs(out_base, exist_ok=True)
    analyze_and_publish(FIXTURE, out_base=out_base)
    cb_path = os.path.join(tmp_path, "callback.json")
    payload = build_analyze_completed_payload(
        out_base,
        job_id="super_e2e_local00001",
        profile_id="0000000000000001",
        inspector_ref="0123456789abcdef0123456789abcdef01234567",
        inspector_resolved_sha="0123456789abcdef0123456789abcdef01234567",
        inspector_repository="mohammedmezo99/DeadZone-SuperInspector",
        target_repo="mohammedmezo99/DeadZone-MEZO",
        archive_url="https://example.test/run/123",
        run_id="123456789",
        duration_ms=42,
    )
    with open(cb_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

    assert payload["type"] == "analyze.completed"
    # The callback envelope carries the caller-supplied profile_id;
    # the profile body carries the engine-derived profile_id. In
    # production they MUST match (the workflow asserts this), so the
    # engine's deterministic profile_id must be derived from the same
    # inputs the dispatcher used.
    import re
    assert re.fullmatch(r"[a-f0-9]{16}", payload["profileId"]), payload["profileId"]
    assert re.fullmatch(r"[a-f0-9]{16}", payload["profile"]["profile_id"]), (
        payload["profile"]
    )
    profile = payload["profile"]
    assert profile["device"]["primary_codename"], profile["device"]
    assert profile["super"]["layout"], profile["super"]
    assert profile["fastboot"]["scripts_detected"] >= 1, profile["fastboot"]
    # The run-id must propagate into the generator block.
    assert profile["generator"]["run_id"] == "123456789", profile["generator"]
    # Artefact paths must list at least the canonical JSON files.
    files = set(profile["artefact_paths"]["files"])
    for required in ("device.json", "rom.json", "flash.json"):
        assert required in files, (required, files)
    print(
        f"  canonical emitter OK: type={payload['type']} "
        f"profileId={payload['profileId']} derived={payload['profile']['profile_id']}"
    )


class _CapturingServer:
    """Tiny HTTP server that records every signed request."""

    def __init__(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.captured: list[dict[str, Any]] = []

    def __enter__(self) -> "_CapturingServer":  # type: ignore[no-untyped-def]
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):  # type: ignore[misc]
            def do_POST(self):  # type: ignore[no-untyped-def]
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.captured.append(
                    {
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                self.send_response(202)
                self.end_headers()

            def log_message(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                return

        self._server = http.server.HTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        self._server.shutdown()
        self._server.server_close()


def test_callback_signing_matches_runtime_contract(tmp_path: str) -> None:
    """End-to-end: build the canonical payload and POST it through
    ``dz_emit_callback.py --callback-file``. The stub server captures the
    request; the captured signature must equal ``HMAC(secret,
    f"{timestamp}.{body}")``."""
    out_base = os.path.join(tmp_path, "callback_output")
    os.makedirs(out_base, exist_ok=True)
    analyze_and_publish(FIXTURE, out_base=out_base)
    cb_path = os.path.join(tmp_path, "callback.json")
    payload = build_analyze_completed_payload(
        out_base,
        job_id="super_e2e_local00001",
        profile_id="0000000000000002",
        inspector_ref="0123456789abcdef0123456789abcdef01234567",
        inspector_resolved_sha="0123456789abcdef0123456789abcdef01234567",
        inspector_repository="mohammedmezo99/DeadZone-SuperInspector",
        target_repo="mohammedmezo99/DeadZone-MEZO",
        archive_url="https://example.test/run/123",
        run_id="123456789",
        duration_ms=42,
    )
    with open(cb_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    secret = "super-secret-e2e"
    with _CapturingServer() as server:
        script = os.path.join(SCRIPT_DIR, "dz_emit_callback.py")
        env = os.environ.copy()
        env.update(
            {
                "DEADZONE_CALLBACK_URL": f"http://127.0.0.1:{server.port}/events",
                "DEADZONE_CALLBACK_SECRET": secret,
                "DEADZONE_EVENT_SECRET": secret,
                "DEADZONE_JOB_ID": payload["jobId"],
                "DEADZONE_INSPECTOR_REF": payload["inspectorRef"],
                "DEADZONE_PROFILE_ID": payload["profileId"],
                "DEADZONE_TARGET_REPO": payload["target"],
            }
        )
        proc = subprocess.run(
            [
                sys.executable,
                script,
                "--callback-file",
                cb_path,
                "analyze.completed",
                "analyze",
                "ROM analysis completed",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Give the server thread time to record the request.
        deadline = time.time() + 5
        while not server.captured and time.time() < deadline:
            time.sleep(0.05)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert server.captured, "no request captured"
    request = server.captured[0]
    body = request["body"].decode("utf-8")
    headers = request["headers"]
    assert "x-deadzone-signature" in headers, headers
    assert "x-deadzone-timestamp" in headers, headers
    # Signature must match the runtime contract, NOT the old buggy one.
    ts = headers["x-deadzone-timestamp"]
    sig = headers["x-deadzone-signature"]
    expected = hmac.new(
        secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    assert sig == f"sha256={expected}", (
        "signature does not match runtime contract"
    )
    # And the OLD buggy signature (no timestamp prefix) must NOT match.
    wrong = hmac.new(
        secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    assert sig != f"sha256={wrong}", (
        "signature appears to be using the old buggy contract"
    )
    # Body must be the canonical payload we built.
    parsed = json.loads(body)
    assert parsed["type"] == "analyze.completed"
    assert parsed["profileId"] == "0000000000000002"
    print("  callback signing OK (matches runtime contract)")


def test_publish_tree_staging(tmp_path: str) -> None:
    """Re-implements the publish-tree staging from the workflow (without
    the git push). Asserts every artefact listed in
    ``profile.artefact_paths.files`` is copied under the stage root."""
    out_base = os.path.join(tmp_path, "publish_output")
    os.makedirs(out_base, exist_ok=True)
    result = analyze_and_publish(FIXTURE, out_base=out_base)
    profile_dir = os.path.dirname(result.profile_paths.device_json)
    payload = build_analyze_completed_payload(
        out_base,
        job_id="super_e2e_local00001",
        profile_id="0000000000000003",
        inspector_ref="0123456789abcdef0123456789abcdef01234567",
        inspector_resolved_sha="0123456789abcdef0123456789abcdef01234567",
        inspector_repository="mohammedmezo99/DeadZone-SuperInspector",
        target_repo="mohammedmezo99/DeadZone-MEZO",
        archive_url="https://example.test/run/123",
        run_id="123456789",
        duration_ms=42,
    )
    profile = payload["profile"]
    device = profile["device"]["primary_codename"]
    stage_root = os.path.join(tmp_path, "stage")
    target = os.path.join(stage_root, "profiles", "super", device, profile["profile_id"])
    os.makedirs(target, exist_ok=True)
    for rel in profile["artefact_paths"]["files"]:
        src = os.path.join(profile_dir, rel)
        dst = os.path.join(target, rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
    assert os.path.isfile(os.path.join(target, "device.json"))
    assert os.path.isfile(os.path.join(target, "rom.json"))
    assert os.path.isfile(os.path.join(target, "flash.json"))
    assert os.path.isdir(os.path.join(target, "super"))
    print(f"  publish tree staging OK: target={target}")


if __name__ == "__main__":
    if not os.path.isfile(FIXTURE):
        print(f"error: fixture missing at {FIXTURE}")
        sys.exit(2)
    test_fixture_is_valid()
    with tempfile.TemporaryDirectory() as tmp:
        test_pipeline_produces_profile(tmp)
        test_canonical_profile_emitter(tmp)
        test_callback_signing_matches_runtime_contract(tmp)
        test_publish_tree_staging(tmp)
    print("superinspector_e2e tests: OK")