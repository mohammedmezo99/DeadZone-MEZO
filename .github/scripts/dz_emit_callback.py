#!/usr/bin/env python3
"""DeadZone Super Inspector callback emitter.

Sign and POST a single callback to the DeadZone bot. The signing contract
matches the central runtime's :mod:`report_build_event`:

    signature = "sha256=" + HMAC-SHA256(secret, f"{timestamp}.{body}")
    headers:
        Content-Type:        application/json
        X-DeadZone-Timestamp: <unix-seconds>
        X-DeadZone-Signature: <signature>
        User-Agent:           DeadZone-SuperInspector/<version>

A signature without the timestamp prefix (or sent under
``x-deadzone-signature``) is rejected by the bot as malformed, which is
the original cause of the ``403 Forbidden`` callback response observed
during the first SuperInspector worker runs.

Usage:
    dz_emit_callback.py <event_type> <stage> <message> [k=v ...]

Reads DEADZONE_CALLBACK_URL and DEADZONE_CALLBACK_SECRET (or its
aliases ``DEADZONE_EVENT_SECRET`` / ``BUILD_PROGRESS_SECRET``) from the
environment.

Failures are non-fatal so they never block the workflow - the worker
has its own retry behaviour and the orchestrator bot already handles
re-dispatch on missing callbacks.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

__version__ = "1.1.0"

USER_AGENT = f"DeadZone-SuperInspector/{__version__}"
TIMEOUT_SECONDS = 15
RETRY_ATTEMPTS = 3
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


def _secret_from_env() -> str:
    for key in (
        "DEADZONE_CALLBACK_SECRET",
        "DEADZONE_EVENT_SECRET",
        "BUILD_PROGRESS_SECRET",
    ):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _canonical_payload(
    event_type: str,
    stage: str,
    message: str,
    extras: dict[str, str],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": event_type,
        "jobId": os.environ.get("DEADZONE_JOB_ID", ""),
        "inspectorRef": os.environ.get("DEADZONE_INSPECTOR_REF", ""),
        "profileId": os.environ.get("DEADZONE_PROFILE_ID", ""),
        "target": os.environ.get("DEADZONE_TARGET_REPO", ""),
        "stage": stage,
        "error": message,
    }
    if "details" in os.environ:
        payload["details"] = os.environ["details"]
    if "CURL_STATUS" in os.environ and event_type == "download.failed":
        payload["details"] = "curl exit " + os.environ["CURL_STATUS"]
    payload.update(extras)
    return payload


def _sign(secret: str, body: str, timestamp: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            return int(getattr(resp, "status", resp.getcode())), ""
    except urllib.error.HTTPError as exc:
        # Re-read the response body so we can mention the server reason in
        # the warning line. Non-fatal: never block the workflow on a 4xx.
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # pragma: no cover - defensive
            detail = ""
        return int(exc.code), detail


def emit(
    event_type: str,
    stage: str,
    message: str,
    extras: dict[str, str] | None = None,
    callback_file: str = "",
) -> int:
    url = os.environ.get("DEADZONE_CALLBACK_URL", "").strip()
    if not url:
        sys.stderr.write("warning: DEADZONE_CALLBACK_URL empty; skipping callback\n")
        return 0

    secret = _secret_from_env()
    if not secret:
        sys.stderr.write("warning: callback secret empty; skipping callback\n")
        return 0

    if callback_file:
        # The caller has already built the canonical payload (e.g. the
        # ``analyze.completed`` profile from
        # ``emit_canonical_profile.py``). Sign and POST it verbatim so
        # the bot receives a byte-identical profile to what the bot
        # validator expects.
        try:
            with open(callback_file, "rb") as fh:
                payload_bytes = fh.read()
        except OSError as exc:
            sys.stderr.write(
                f"warning: could not read callback file {callback_file}: {exc}\n"
            )
            return 0
        payload = json.loads(payload_bytes.decode("utf-8"))
    else:
        payload = _canonical_payload(event_type, stage, message, extras or {})
        payload_bytes = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    body = payload_bytes.decode("utf-8")

    last_status = 0
    last_detail = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-DeadZone-Timestamp": timestamp,
            "X-DeadZone-Signature": _sign(secret, body, timestamp),
            "User-Agent": USER_AGENT,
        }
        last_status, last_detail = _post(url, body.encode("utf-8"), headers)
        if last_status in (200, 202):
            return 0
        # 4xx other than the retryable list are not transient.
        if last_status not in RETRYABLE_HTTP:
            break
        if attempt < RETRY_ATTEMPTS:
            time.sleep(min(2 ** (attempt - 1), 4))

    sys.stderr.write(
        f"warning: {event_type} callback failed: HTTP {last_status} {last_detail!r}\n"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        sys.stderr.write(
            f"usage: {argv[0]} [--callback-file PATH] <type> <stage> <message> [k=v ...]\n"
        )
        return 2

    args = list(argv[1:])
    callback_file = ""
    if args and args[0] == "--callback-file":
        if len(args) < 2:
            sys.stderr.write("error: --callback-file requires a path\n")
            return 2
        callback_file = args[1]
        args = args[2:]

    if len(args) < 3:
        sys.stderr.write(
            f"usage: {argv[0]} [--callback-file PATH] <type> <stage> <message> [k=v ...]\n"
        )
        return 2

    event_type = args[0]
    stage = args[1]
    message = args[2]
    extras: dict[str, str] = {}
    for arg in args[3:]:
        if "=" not in arg:
            continue
        key, _, value = arg.partition("=")
        extras[key] = value

    return emit(event_type, stage, message, extras, callback_file=callback_file)


if __name__ == "__main__":
    sys.exit(main(sys.argv))