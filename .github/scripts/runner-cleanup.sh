#!/usr/bin/env bash
#
# DeadZone MEZO — Central Runner Cleanup Helper
#
# This script provides centralized cleanup operations for all MEZO workflows.
# It is ALLOWLIST-driven and safe to use.
#
# IMPORTANT: All ROM workflows use working-directory: toolbuild
# Paths are relative to $GITHUB_WORKSPACE where toolbuild is checked out
#
# Usage:
#   runner-cleanup.sh <mode> [options]
#
# Modes:
#   bootstrap    - Initial runner setup cleanup (pre-build)
#   pre-build    - Cleanup before ROM build starts
#   pre-package  - Cleanup before packaging stage
#   post-package - Cleanup after final ZIP is created (preserves final ZIP)
#   final        - Final cleanup (always runs, on success/failure/cancel)
#   light        - Lightweight cleanup for non-ROM workflows
#
set -Eeuo pipefail

# Default to fail-safe behavior
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-0}"

# Base paths - always use toolbuild for ROM workflows
WORKSPACE="${GITHUB_WORKSPACE:-.}"
TOOLBUILD_DIR="$WORKSPACE/toolbuild"
ENGINE_OUT_DIR="$TOOLBUILD_DIR/out"
ENGINE_BUILD_DIR="$TOOLBUILD_DIR/build"

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
    if [[ -d "$WORKSPACE" ]]; then
        df -h "$WORKSPACE" 2>/dev/null | tail -1 | awk -v label="Workspace" '{printf "  %s: %s available\n", label, $4}'
    fi
    if [[ -d "$TOOLBUILD_DIR" ]]; then
        local out_used
        out_used=$(du -sh "$ENGINE_OUT_DIR" 2>/dev/null | cut -f1 || echo "unknown")
        log_info "  toolbuild/out: ${out_used}"
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

    # Docker cleanup (GitHub Actions runners don't typically use Docker for ROM builds)
    log_info "Cleaning Docker images..."
    run_cmd docker image prune --all --force 2>/dev/null || true
    run_cmd docker builder prune --all --force 2>/dev/null || true
    run_cmd docker system prune --all --force 2>/dev/null || true

    # Remove large unused SDK components
    log_info "Removing unused SDK components..."
    run_cmd sudo rm -rf /usr/local/lib/android 2>/dev/null || true

    # Remove unused APT packages (proven safe for ROM builds)
    log_info "Removing unused system packages..."
    run_cmd sudo apt-get purge -y \
        azure-cli \
        firefox \
        powershell \
        2>/dev/null || true

    log_info "Bootstrap cleanup completed"
}

# Pre-build cleanup: Clean up before ROM build
cleanup_pre_build() {
    log_info "Running pre-build cleanup..."

    report_disk
    report_memory

    # Ensure toolbuild directories are clean
    run_cmd sudo rm -rf "$ENGINE_OUT_DIR"/* 2>/dev/null || true
    run_cmd sudo rm -rf "$ENGINE_BUILD_DIR"/* 2>/dev/null || true

    log_info "Pre-build cleanup completed"
}

# Pre-package cleanup: Before ZIP generation
cleanup_pre_package() {
    log_info "Running pre-package cleanup..."

    # Clean up build artifacts that are no longer needed
    # Keep super.img as it's needed for packaging

    if [[ -d "$ENGINE_BUILD_DIR/baserom/images" ]]; then
        if [[ -f "$ENGINE_BUILD_DIR/baserom/images/super.img" ]]; then
            log_info "Preserving super.img for packaging..."
            # Remove other baserom files to free space
            find "$ENGINE_BUILD_DIR/baserom" -type f ! -name "super.img" -delete 2>/dev/null || true
            find "$ENGINE_BUILD_DIR/baserom" -type d -empty -delete 2>/dev/null || true
        fi
    fi

    report_disk
    log_info "Pre-package cleanup completed"
}

# Post-package cleanup: After final ZIP is created
# CRITICAL: This preserves the final ZIP until all uploads complete
cleanup_post_package() {
    log_info "Running post-package cleanup..."

    # The final ZIP is in ENGINE_OUT_DIR and must be preserved
    # Only clean build artifacts that are no longer needed

    # Clean baserom staging
    if [[ -d "$ENGINE_BUILD_DIR/baserom" ]]; then
        log_info "Removing baserom staging..."
        run_cmd sudo rm -rf "$ENGINE_BUILD_DIR/baserom" 2>/dev/null || true
    fi

    # Clean build artifacts no longer needed after packaging
    log_info "Removing build artifacts..."
    run_cmd sudo rm -rf "$ENGINE_BUILD_DIR/payload" 2>/dev/null || true
    run_cmd sudo rm -rf "$ENGINE_BUILD_DIR/work" 2>/dev/null || true
    run_cmd sudo rm -rf "$ENGINE_BUILD_DIR/out" 2>/dev/null || true

    # Clean temporary files
    find "$WORKSPACE" -name "*.tmp" -delete 2>/dev/null || true
    find "$WORKSPACE" -name "*.temp" -delete 2>/dev/null || true

    report_disk
    log_info "Post-package cleanup completed (final ZIP preserved)"
}

# Final cleanup: Always runs (on success, failure, or cancel)
cleanup_final() {
    log_info "Running final cleanup..."

    # Clean runtime directories
    run_cmd rm -rf "$WORKSPACE/runtime" 2>/dev/null || true

    # Clean rclone config (credentials)
    run_cmd rm -f "$TOOLBUILD_DIR/rclone.conf" 2>/dev/null || true
    run_cmd rm -f "$WORKSPACE/rclone.conf" 2>/dev/null || true

    # Clean ALL of toolbuild directory (final ZIP, build tree, everything)
    log_info "Cleaning toolbuild directory..."
    run_cmd sudo rm -rf "$TOOLBUILD_DIR" 2>/dev/null || true

    # Clean temporary directories
    run_cmd rm -rf "$WORKSPACE/temp" 2>/dev/null || true
    run_cmd rm -rf "$WORKSPACE/.deadzone-runtime" 2>/dev/null || true

    # Clean pip cache
    run_cmd pip3 cache purge 2>/dev/null || true

    # Clean APT cache
    run_cmd sudo apt-get clean 2>/dev/null || true
    run_cmd sudo rm -rf /var/lib/apt/lists/* 2>/dev/null || true

    report_disk
    log_info "Final cleanup completed"
}

# Light cleanup: For non-ROM workflows (bot, website, etc.)
cleanup_light() {
    log_info "Running lightweight cleanup..."

    # Only remove clearly temporary files
    run_cmd rm -rf "$WORKSPACE/temp" 2>/dev/null || true
    run_cmd rm -rf "$WORKSPACE/.deadzone-runtime" 2>/dev/null || true

    # Clean npm cache if present
    if [[ -d "$WORKSPACE/node_modules" ]]; then
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
  post-package Cleanup after final ZIP is created (preserves final ZIP)
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
