#!/usr/bin/env bash
#
# DeadZone MEZO — Central Runner Cleanup Helper
#
# This script provides centralized cleanup operations for all MEZO workflows.
# It is ALLOWLIST-driven and safe to use.
#
# Usage:
#   runner-cleanup.sh <mode> [options]
#
# Modes:
#   bootstrap    - Initial runner setup cleanup (pre-build)
#   pre-build    - Cleanup before ROM build starts
#   pre-package  - Cleanup before packaging stage
#   post-package - Cleanup after final ZIP is created
#   final        - Final cleanup (always runs, on success/failure/cancel)
#   light        - Lightweight cleanup for non-ROM workflows
#
set -Eeuo pipefail

# Default to fail-safe behavior
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-0}"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[CLEANUP]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[CLEANUP-WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[CLEANUP-ERROR]${NC} $*" >&2
}

# Dry run prefix
run_cmd() {
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "[DRY-RUN] Would execute: $*"
    else
        "$@"
    fi
}

# Check available disk space
report_disk() {
    log_info "Disk space report:"
    df -h / | tail -1 | awk -v label="Root" '{printf "  %s: %s available\n", label, $4}'
    if [[ -d "/workspace" ]]; then
        df -h /workspace 2>/dev/null | tail -1 | awk -v label="Workspace" '{printf "  %s: %s available\n", label, $4}'
    fi
    if [[ -d "$GITHUB_WORKSPACE" && "$GITHUB_WORKSPACE" != "/" ]]; then
        df -h "$GITHUB_WORKSPACE" 2>/dev/null | tail -1 | awk -v label="GitHub Workspace" '{printf "  %s: %s available\n", label, $4}'
    fi
}

# Report memory info
report_memory() {
    log_info "Memory report:"
    free -h | awk '/^Mem:/ {printf "  RAM: %s total, %s used, %s available\n", $2, $3, $7}'
    nproc | xargs -I{} echo "  CPU cores: {}"
}

# Bootstrap cleanup: Remove large unused pre-installed components
cleanup_bootstrap() {
    log_info "Running bootstrap cleanup..."

    # Docker cleanup (if Docker is not used later in this workflow)
    if ! grep -q "docker" "$GITHUB_WORKSPACE/.github/workflows/"*.yml 2>/dev/null; then
        log_info "Docker not required, cleaning Docker images..."
        run_cmd docker image prune --all --force 2>/dev/null || true
        run_cmd docker builder prune --all --force 2>/dev/null || true
        run_cmd docker system prune --all --force 2>/dev/null || true
    fi

    # Remove large unused SDK components (be careful not to remove what's needed)
    if [[ -d "/usr/local/lib/android" ]]; then
        # Check if android SDK is actually needed
        if ! grep -q "android-sdk" "$GITHUB_WORKSPACE/.github/workflows/"*.yml 2>/dev/null; then
            log_info "Android SDK not required, cleaning..."
            run_cmd sudo rm -rf /usr/local/lib/android 2>/dev/null || true
        fi
    fi

    # Remove unused APT packages
    log_info "Removing unused system packages..."
    run_cmd sudo apt-get purge -y \
        azure-cli \
        "ghc*" \
        "zulu*" \
        "hhvm*" \
        "llvm*" \
        firefox \
        "google*" \
        "dotnet*" \
        powershell \
        "mysql*" \
        "php*" \
        2>/dev/null || true

    log_info "Bootstrap cleanup completed"
}

# Pre-build cleanup: Clean up before ROM build
cleanup_pre_build() {
    log_info "Running pre-build cleanup..."

    report_disk
    report_memory

    # Ensure workspace is clean
    run_cmd sudo rm -rf "$GITHUB_WORKSPACE/temp" 2>/dev/null || true
    run_cmd rm -rf "$GITHUB_WORKSPACE/out"/* 2>/dev/null || true
    run_cmd rm -rf "$GITHUB_WORKSPACE/build"/* 2>/dev/null || true

    log_info "Pre-build cleanup completed"
}

# Pre-package cleanup: Before ZIP generation
cleanup_pre_package() {
    log_info "Running pre-package cleanup..."

    # Clean up build artifacts that are no longer needed
    # These are typically large files used during build but not in final package

    # Clean extracted baserom if not needed
    if [[ -d "$GITHUB_WORKSPACE/build/baserom" ]]; then
        # Check if super.img exists (needed for packaging)
        if [[ -f "$GITHUB_WORKSPACE/build/baserom/images/super.img" ]]; then
            log_info "Preserving super.img for packaging, cleaning other baserom files..."
            # Only remove other files, keep super.img
            find "$GITHUB_WORKSPACE/build/baserom" -type f ! -name "super.img" -delete 2>/dev/null || true
            find "$GITHUB_WORKSPACE/build/baserom" -type d -empty -delete 2>/dev/null || true
        fi
    fi

    report_disk
    log_info "Pre-package cleanup completed"
}

# Post-package cleanup: After final ZIP is created
cleanup_post_package() {
    log_info "Running post-package cleanup..."

    # The final ZIP should already exist in out/ directory
    # Clean up everything else that can be regenerated

    # Clean build/baserom - the final ZIP is independent now
    if [[ -d "$GITHUB_WORKSPACE/build/baserom" ]]; then
        log_info "Removing baserom staging directory..."
        run_cmd sudo rm -rf "$GITHUB_WORKSPACE/build/baserom" 2>/dev/null || true
    fi

    # Clean build artifacts that are no longer needed
    log_info "Removing build artifacts..."
    run_cmd sudo rm -rf "$GITHUB_WORKSPACE/build/out" 2>/dev/null || true
    run_cmd sudo rm -rf "$GITHUB_WORKSPACE/build/payload" 2>/dev/null || true
    run_cmd sudo rm -rf "$GITHUB_WORKSPACE/build/work" 2>/dev/null || true

    # Clean any temporary files
    find "$GITHUB_WORKSPACE" -name "*.tmp" -delete 2>/dev/null || true
    find "$GITHUB_WORKSPACE" -name "*.temp" -delete 2>/dev/null || true
    find "$GITHUB_WORKSPACE" -name "*.log" -path "*/build/*" -delete 2>/dev/null || true

    report_disk
    log_info "Post-package cleanup completed"
}

# Final cleanup: Always runs (on success, failure, or cancel)
cleanup_final() {
    log_info "Running final cleanup..."

    # Clean up runtime directories
    run_cmd rm -rf "$GITHUB_WORKSPACE/runtime" 2>/dev/null || true

    # Clean up rclone config
    run_cmd rm -f "$GITHUB_WORKSPACE/toolbuild/rclone.conf" 2>/dev/null || true
    run_cmd rm -f "$GITHUB_WORKSPACE/rclone.conf" 2>/dev/null || true

    # Clean up build and out directories
    run_cmd sudo rm -rf "$GITHUB_WORKSPACE/toolbuild/out" 2>/dev/null || true
    run_cmd sudo rm -rf "$GITHUB_WORKSPACE/toolbuild/build" 2>/dev/null || true

    # Clean up temporary directories from previous steps
    run_cmd rm -rf "$GITHUB_WORKSPACE/temp" 2>/dev/null || true
    run_cmd rm -rf "$GITHUB_WORKSPACE/.deadzone-runtime" 2>/dev/null || true

    # Clean pip cache
    run_cmd pip3 cache purge 2>/dev/null || true

    # Clean APT cache if no more installs needed
    run_cmd sudo apt-get clean 2>/dev/null || true
    run_cmd sudo rm -rf /var/lib/apt/lists/* 2>/dev/null || true

    # Report final disk state
    report_disk
    log_info "Final cleanup completed"
}

# Light cleanup: For non-ROM workflows (bot, website, etc.)
cleanup_light() {
    log_info "Running lightweight cleanup..."

    # Only remove clearly temporary files
    run_cmd rm -rf "$GITHUB_WORKSPACE/temp" 2>/dev/null || true
    run_cmd rm -rf "$GITHUB_WORKSPACE/.deadzone-runtime" 2>/dev/null || true

    # Clean npm/yarn cache if present
    if [[ -d "$GITHUB_WORKSPACE/node_modules" ]]; then
        log_info "Cleaning npm cache..."
        run_cmd npm cache clean --force 2>/dev/null || true
    fi

    log_info "Lightweight cleanup completed"
}

# Print usage
usage() {
    cat <<EOF
DeadZone MEZO — Central Runner Cleanup Helper

Usage: $(basename "$0") <mode> [options]

Modes:
  bootstrap    Initial runner setup cleanup (pre-build)
  pre-build    Cleanup before ROM build starts
  pre-package  Cleanup before packaging stage
  post-package Cleanup after final ZIP is created
  final        Final cleanup (always runs)
  light        Lightweight cleanup for non-ROM workflows

Options:
  --dry-run    Show what would be deleted without deleting
  --verbose    Show detailed output
  --disk       Report disk space
  --memory     Report memory info

Examples:
  $(basename "$0") bootstrap
  $(basename "$0") pre-package --disk
  $(basename "$0") final --verbose
  $(basename "$0") light --dry-run

EOF
}

# Main entry point
main() {
    local mode="${1:-}"

    # Parse options
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)
                DRY_RUN=1
                shift
                ;;
            --verbose)
                VERBOSE=1
                shift
                ;;
            --disk)
                report_disk
                exit 0
                ;;
            --memory)
                report_memory
                exit 0
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
            *)
                break
                ;;
        esac
    done

    case "$mode" in
        bootstrap)
            cleanup_bootstrap
            ;;
        pre-build)
            cleanup_pre_build
            ;;
        pre-package)
            cleanup_pre_package
            ;;
        post-package)
            cleanup_post_package
            ;;
        final)
            cleanup_final
            ;;
        light)
            cleanup_light
            ;;
        "")
            log_error "No mode specified"
            usage
            exit 1
            ;;
        *)
            log_error "Unknown mode: $mode"
            usage
            exit 1
            ;;
    esac
}

main "$@"
