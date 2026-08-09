from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TERMINAL_STATUSES = {
    "fail": "failed",
    "failed": "failed",
    "cancel": "cancelled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
}

PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
REQUEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,99}$")


def _first_env(runtime: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = runtime.get(name, "").strip()
        if value:
            return value
    return ""


def _safe_https(value: str, field: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or len(normalized) > 2048
    ):
        raise ValueError(f"{field} must be a safe HTTPS URL")
    return normalized


def _normalized_status(value: str) -> str:
    status = TERMINAL_STATUSES.get(value.strip().lower())
    if not status:
        raise ValueError("status must be failed or cancelled")
    return status


def build_terminal_payload(
    status: str,
    *,
    project: str,
    message: str,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    runtime = os.environ if env is None else env
    normalized_status = _normalized_status(status)
    normalized_project = project.strip().lower()
    if not PROJECT_RE.fullmatch(normalized_project):
        raise ValueError("project is invalid")

    request_id = _first_env(runtime, "DEADZONE_REQUEST_ID", "DZ_REQUEST_ID")
    if not REQUEST_RE.fullmatch(request_id):
        raise ValueError("DeadZone request ID is missing or invalid")

    normalized_message = message.strip()
    if not normalized_message or len(normalized_message) > 500:
        raise ValueError("message is missing or too long")

    run_id = _first_env(runtime, "DEADZONE_RUN_ID_OVERRIDE", "GITHUB_RUN_ID")
    repository = _first_env(runtime, "GITHUB_REPOSITORY")
    server_url = _first_env(runtime, "GITHUB_SERVER_URL") or "https://github.com"
    run_url_override = _first_env(runtime, "DEADZONE_RUN_URL_OVERRIDE")

    run_url = ""
    if run_url_override:
        run_url = _safe_https(run_url_override, "run_url")
    elif run_id and repository:
        run_url = _safe_https(
            f"{server_url.rstrip('/')}/{repository}/actions/runs/{run_id}",
            "run_url",
        )

    event_id = (
        f"{request_id}:{run_id or 'terminal'}:"
        f"{runtime.get('GITHUB_RUN_ATTEMPT', '1')}:{normalized_status}:"
        f"{uuid.uuid4().hex[:12]}"
    )[:100]

    payload: dict[str, object] = {
        "event_id": event_id,
        "request_id": request_id,
        "project": normalized_project,
        "status": normalized_status,
        "message": normalized_message,
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "error_code": (
            "github_actions_failure"
            if normalized_status == "failed"
            else "github_actions_cancelled"
        ),
    }
    if run_id:
        payload["run_id"] = run_id[:64]
    if run_url:
        payload["run_url"] = run_url

    # Terminal events intentionally omit progress. The Worker keeps the last
    # confirmed percentage and marks that exact pipeline stage as terminal.
    return payload


def serialize_event(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sign_event(secret: str, timestamp: str, body: str) -> str:
    normalized_secret = secret.strip()
    if not normalized_secret:
        raise ValueError("DeadZone build-event secret is missing")
    digest = hmac.new(
        normalized_secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def post_terminal_event(
    payload: Mapping[str, object],
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    runtime = os.environ if env is None else env
    callback_url = _first_env(runtime, "DEADZONE_CALLBACK_URL", "DZ_CALLBACK_URL")
    secret = _first_env(
        runtime,
        "DEADZONE_EVENT_SECRET",
        "BUILD_PROGRESS_SECRET",
        "DZ_EVENT_SECRET",
    )
    target = _safe_https(callback_url, "callback_url")
    body = serialize_event(payload)
    timestamp = str(int(time.time()))
    request = Request(
        target,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-DeadZone-Timestamp": timestamp,
            "X-DeadZone-Signature": sign_event(secret, timestamp, body),
            "User-Agent": "DeadZone-MEZO/terminal-reporter",
        },
    )
    with urlopen(request, timeout=20) as response:
        response_status = int(getattr(response, "status", response.getcode()))
        if response_status not in {200, 202}:
            raise RuntimeError(f"DeadZone callback failed with HTTP {response_status}")
        return response_status


def run_self_test() -> int:
    sample_env = {
        "DEADZONE_REQUEST_ID": "port_1234567890abcdef",
        "GITHUB_RUN_ID": "31315940452",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "mohammedmezo99/DeadZone-MEZO",
    }
    payload = build_terminal_payload(
        "failed",
        project="port",
        message="Port ROM build failed",
        env=sample_env,
    )
    assert payload["status"] == "failed"
    assert payload["request_id"] == sample_env["DEADZONE_REQUEST_ID"]
    assert payload["run_id"] == sample_env["GITHUB_RUN_ID"]
    assert payload["run_url"] == (
        "https://github.com/mohammedmezo99/DeadZone-MEZO/actions/runs/31315940452"
    )
    assert payload["error_code"] == "github_actions_failure"
    assert "progress" not in payload

    body = '{"request_id":"port_test","status":"failed"}'
    expected = hmac.new(
        b"secret",
        f"1700000000.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert sign_event("secret", "1700000000", body) == f"sha256={expected}"
    print("DeadZone terminal reporter self-test: OK")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return run_self_test()

    parser = argparse.ArgumentParser(
        description="Send a signed terminal DeadZone build event without depending on a private engine checkout."
    )
    parser.add_argument("status", help="failed/fail or cancelled/canceled")
    parser.add_argument("--project", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args(argv)

    payload = build_terminal_payload(
        args.status,
        project=args.project,
        message=args.message,
    )
    response_status = post_terminal_event(payload)
    print(
        f"DeadZone terminal event accepted: status={payload['status']} "
        f"request={payload['request_id']} http={response_status}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"DeadZone terminal reporter failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
