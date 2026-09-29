#!/usr/bin/env bash
# DeadZone MEZO - Test runner for SuperInspector integration
#
# Runs every script-level test for the SuperInspector worker and the
# supporting helpers. Used by CI and by the workflow's own self-check
# step before dispatch.
#
# Exit code is non-zero if any suite fails.

set -Eeuo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"
cd "$REPO_ROOT"

failed=0
run_suite() {
    local title="$1"
    local path="$2"
    printf '%s\n' "==== $title ===="
    if ! python3 "$path"; then
        echo "FAILED: $title"
        failed=1
    fi
}

run_suite "callback emitter unit tests" "$SCRIPT_DIR/test_dz_emit_callback.py"
run_suite "ROM archive validator tests" "$SCRIPT_DIR/test_dz_validate_rom.py"
run_suite "workflow ordering static checks" "$SCRIPT_DIR/test_workflow_ordering.py"
run_suite "SuperInspector E2E pipeline" "$SCRIPT_DIR/test_superinspector_e2e.py"

if (( failed )); then
    echo "One or more test suites failed."
    exit 1
fi
echo "All DeadZone MEZO test suites passed."