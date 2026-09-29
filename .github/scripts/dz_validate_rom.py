#!/usr/bin/env python3
"""Validate a downloaded ROM archive before invoking the analyzer.

The SuperInspector pipeline streams the ROM through ``tarfile.open(..., "r:*")``
so a non-tar payload (HTML error page, truncated download, wrong file) only
fails inside ``analyze_rom`` - by then the engine has already constructed
temporary state. Catching the failure earlier keeps the worker log readable
and produces a precise ``download.failed`` callback message.

Usage:
    python3 dz_validate_rom.py <path-to-rom>

Exits 0 when the archive is valid (and reports member count to stdout);
exits 2 when it is not. Path traversal protection lives here so callers
do not need to repeat the check.
"""
from __future__ import annotations

import json
import os
import sys
import tarfile


class ArchiveValidationError(Exception):
    """Raised when an archive cannot be safely analysed."""


def _safe_member(name: str) -> str:
    """Reject absolute paths and ``..`` segments to prevent path traversal."""
    norm = os.path.normpath(name)
    if norm.startswith("..") or os.path.isabs(norm):
        raise ArchiveValidationError(f"unsafe member path: {name!r}")
    return norm


def validate_rom(path: str) -> dict[str, object]:
    """Open *path* as a tar archive (any supported compression) and
    return a small JSON-serialisable summary. Raises
    :class:`ArchiveValidationError` on any structural problem.
    """
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise ArchiveValidationError(f"archive does not exist: {p}")
    size = os.path.getsize(p)
    if size <= 0:
        raise ArchiveValidationError(f"archive is empty: {p}")

    try:
        with tarfile.open(p, "r:*") as tf:
            members = list(tf)
    except tarfile.ReadError as exc:
        raise ArchiveValidationError(
            f"not a valid tar.gz/tar archive (read error: {exc})"
        ) from exc
    except (OSError, EOFError) as exc:
        raise ArchiveValidationError(
            f"failed to read archive: {exc}"
        ) from exc

    if not members:
        raise ArchiveValidationError("archive contains no members")

    file_count = 0
    total_bytes = 0
    seen_names: list[str] = []
    for member in members:
        try:
            _safe_member(member.name)
        except ArchiveValidationError:
            raise
        seen_names.append(member.name)
        if member.isfile():
            file_count += 1
            total_bytes += int(member.size or 0)

    # A valid Xiaomi Fastboot ROM always carries at least one image or
    # script inside the archive. A tarball that only contains directory
    # entries is rejected so the worker surfaces a clear "empty archive"
    # failure rather than letting the engine silently produce an empty
    # profile.
    if file_count == 0:
        raise ArchiveValidationError(
            "archive contains no files (only directories); not a valid ROM"
        )

    return {
        "path": p,
        "size_bytes": size,
        "member_count": len(members),
        "file_count": file_count,
        "uncompressed_bytes": total_bytes,
        "first_members": seen_names[:5],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(f"usage: {argv[0]} <rom-path>\n")
        return 2
    try:
        summary = validate_rom(argv[1])
    except ArchiveValidationError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))