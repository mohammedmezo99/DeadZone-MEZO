#!/usr/bin/env python3
"""Deterministic tests for the DeadZone ROM archive validator.

Run:
    python3 .github/scripts/test_dz_validate_rom.py
"""
from __future__ import annotations

import gzip
import io
import os
import sys
import tarfile
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import dz_validate_rom  # noqa: E402


def _write_tar(tmp: str, files: dict[str, bytes], gzipped: bool) -> str:
    buf = io.BytesIO()
    mode = "w:gz" if gzipped else "w"
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    path = os.path.join(tmp, "rom" if not gzipped else "rom.tgz")
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    return path


def test_validate_plain_tar_succeeds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_tar(
            tmp,
            {
                "images/build.prop": b"ro.product.device=zircon\n",
                "images/flash_all.sh": b"#!/bin/sh\nfastboot flash boot boot.img\n",
            },
            gzipped=False,
        )
        summary = dz_validate_rom.validate_rom(path)
    assert summary["file_count"] == 2
    assert summary["member_count"] == 2
    assert summary["size_bytes"] > 0
    assert "images/build.prop" in summary["first_members"]


def test_validate_gzipped_tar_succeeds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_tar(tmp, {"a/b/c.bin": b"x" * 4096}, gzipped=True)
        summary = dz_validate_rom.validate_rom(path)
    assert summary["file_count"] == 1
    assert summary["uncompressed_bytes"] == 4096


def test_validate_rejects_html_error_page() -> None:
    """A real failure mode for the production worker: server returns 200 OK
    with an HTML error body when behind a CDN. ``tarfile`` rejects it."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "fake.tgz")
        with open(path, "wb") as fh:
            fh.write(b"<html>404 not found</html>")
        try:
            dz_validate_rom.validate_rom(path)
        except dz_validate_rom.ArchiveValidationError as exc:
            assert "not a valid" in str(exc)
        else:
            raise AssertionError("expected ArchiveValidationError")


def test_validate_rejects_empty_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "empty.tgz")
        with open(path, "wb") as fh:
            fh.write(b"")
        try:
            dz_validate_rom.validate_rom(path)
        except dz_validate_rom.ArchiveValidationError as exc:
            assert "empty" in str(exc)
        else:
            raise AssertionError("expected ArchiveValidationError")


def test_validate_rejects_truncated_gzip() -> None:
    """A gzip stream cut mid-body looks like a gzip header but
    tarfile fails the trailer."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "trunc.tgz")
        # Craft a valid single-member gzip then truncate.
        with tarfile.open(path, "w:gz") as tf:
            info = tarfile.TarInfo("a.txt")
            data = b"hello"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        with open(path, "rb") as fh:
            full = fh.read()
        with open(path, "wb") as fh:
            fh.write(full[: len(full) // 2])
        try:
            dz_validate_rom.validate_rom(path)
        except dz_validate_rom.ArchiveValidationError:
            pass
        else:
            raise AssertionError("expected ArchiveValidationError for truncated gzip")


def test_validate_rejects_tar_with_only_directories() -> None:
    """A tar with only directory entries should be rejected - a Xiaomi
    Fastboot ROM is never directory-only."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "dirs.tgz")
        with tarfile.open(path, "w:gz") as tf:
            info = tarfile.TarInfo("images/")
            info.type = tarfile.DIRTYPE
            tf.addfile(info)
        try:
            dz_validate_rom.validate_rom(path)
        except dz_validate_rom.ArchiveValidationError as exc:
            assert "no files" in str(exc)
        else:
            raise AssertionError("expected ArchiveValidationError")


def test_validate_rejects_path_traversal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "evil.tgz")
        with tarfile.open(path, "w:gz") as tf:
            info = tarfile.TarInfo("../../etc/passwd")
            data = b"bad"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        try:
            dz_validate_rom.validate_rom(path)
        except dz_validate_rom.ArchiveValidationError as exc:
            assert "unsafe member path" in str(exc)
        else:
            raise AssertionError("expected ArchiveValidationError")


def test_validate_rejects_absolute_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "abs.tgz")
        with tarfile.open(path, "w:gz") as tf:
            info = tarfile.TarInfo("/etc/passwd")
            data = b"bad"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        try:
            dz_validate_rom.validate_rom(path)
        except dz_validate_rom.ArchiveValidationError as exc:
            assert "unsafe member path" in str(exc)
        else:
            raise AssertionError("expected ArchiveValidationError")


def test_validate_rejects_nonexistent() -> None:
    try:
        dz_validate_rom.validate_rom("/tmp/does-not-exist-zzz.tgz")
    except dz_validate_rom.ArchiveValidationError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("expected ArchiveValidationError")


def test_main_returns_2_on_failure() -> None:
    rc = dz_validate_rom.main(["script", "/tmp/nope"])
    assert rc == 2


def test_main_returns_2_on_missing_arg() -> None:
    rc = dz_validate_rom.main(["script"])
    assert rc == 2


def test_main_returns_0_on_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_tar(tmp, {"a.txt": b"hello"}, gzipped=True)
        rc = dz_validate_rom.main(["script", path])
    assert rc == 0


def test_safe_member_rejects_dotdot() -> None:
    try:
        dz_validate_rom._safe_member("../escape")
    except dz_validate_rom.ArchiveValidationError:
        pass
    else:
        raise AssertionError("expected error for ..")


def test_safe_member_accepts_relative() -> None:
    assert dz_validate_rom._safe_member("images/build.prop") == "images/build.prop"


if __name__ == "__main__":
    test_validate_plain_tar_succeeds()
    test_validate_gzipped_tar_succeeds()
    test_validate_rejects_html_error_page()
    test_validate_rejects_empty_archive()
    test_validate_rejects_truncated_gzip()
    test_validate_rejects_tar_with_only_directories()
    test_validate_rejects_path_traversal()
    test_validate_rejects_absolute_path()
    test_validate_rejects_nonexistent()
    test_main_returns_2_on_failure()
    test_main_returns_2_on_missing_arg()
    test_main_returns_0_on_success()
    test_safe_member_rejects_dotdot()
    test_safe_member_accepts_relative()
    print("dz_validate_rom tests: OK")