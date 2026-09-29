#!/usr/bin/env python3
"""DeadZone Super Inspector callback emitter.

Usage:
    dz_emit_callback.py <event_type> <stage> <message> [extra_json_field=value ...]

Reads DEADZONE_CALLBACK_URL and DEADZONE_CALLBACK_SECRET from the
environment, builds a JSON payload, signs it with HMAC-SHA256, and
POSTs it to the callback endpoint. Failures are non-fatal so they
never block the workflow - the worker has its own retry behaviour
and the orchestrator bot already handles re-dispatch on missing
callbacks.

Reserved keys (`type`, `stage`, `error`, `jobId`, `inspectorRef`,
`profileId`, `target`) are populated automatically from the env.
Other keys are passed via `extra.key=value` pairs on the CLI.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import urllib.request


def main(argv):
    if len(argv) < 4:
        sys.stderr.write(
            f"usage: {argv[0]} <type> <stage> <message> [k=v ...]\n"
        )
        return 2

    event_type, stage, message = argv[1], argv[2], argv[3]
    extras = {}
    for arg in argv[4:]:
        if "=" not in arg:
            continue
        key, _, value = arg.partition("=")
        extras[key] = value

    payload = {
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

    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    secret = os.environ.get("DEADZONE_CALLBACK_SECRET", "")
    if not secret:
        sys.stderr.write(
            "warning: DEADZONE_CALLBACK_SECRET is empty; skipping callback\n"
        )
        return 0
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    url = os.environ.get("DEADZONE_CALLBACK_URL", "")
    if not url:
        sys.stderr.write(
            "warning: DEADZONE_CALLBACK_URL is empty; skipping callback\n"
        )
        return 0
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-deadzone-signature", sig)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:
        sys.stderr.write(
            "warning: " + event_type + " callback failed: " + str(exc) + "\n"
        )
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
