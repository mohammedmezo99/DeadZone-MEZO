#!/usr/bin/env python3
"""Tests for the DeadZone-MEZO in-process library.

Covers:

- Archive validation (Lite-equivalent) — accept / reject / path-traversal
- Build prop extraction
- Codename detection (multi-source)
- Image inventory discovery
- Super analysis delegation
- Profile assembly + validation
- End-to-end pipeline against the synthetic fixture

Run:
    python3 .github/scripts/test_deadzone_mezo.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "lib"))

import deadzone_mezo  # noqa: E402
from deadzone_mezo import (  # noqa: E402
    analyze_rom_path,
    detect_codename,
    discover_image_inventory,
    extract_rom_archive,
    validate_profile,
)

REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
FIXTURE = os.path.join(REPO_ROOT, "tests_fixtures", "rom_e2e_fixture.tgz")


def _write_tar(tmp: str, files: dict[str, bytes], gzipped: bool = True) -> str:
    """Build a tiny tar/tgz in a temp dir, return the path."""
    mode = "w:gz" if gzipped else "w"
    suffix = ".tgz" if gzipped else ".tar"
    path = os.path.join(tmp, f"fixture{suffix}")
    with tarfile.open(path, mode) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


# ---------- archive validation ----------

def test_extract_accepts_valid_tgz() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_tar(
            tmp,
            {
                "images/build.prop": b"ro.product.device=foo\n",
                "images/flash_all.sh": b"#!/bin/sh\nfastboot flash boot boot.img\n",
            },
        )
        summary = extract_rom_archive(path)
    assert summary.member_count >= 2
    assert summary.file_count == 2
    assert summary.format == "fastboot-tgz"
    assert summary.sha256 and len(summary.sha256) == 64


def test_extract_rejects_html_error_page() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "fake.tgz")
        with open(path, "wb") as fh:
            fh.write(b"<html>404 not found</html>")
        try:
            extract_rom_archive(path)
        except ValueError as exc:
            assert "not a valid" in str(exc) or "no members" in str(exc)
        else:
            raise AssertionError("expected ValueError for HTML")


def test_extract_rejects_empty_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "empty.tgz")
        with open(path, "wb") as fh:
            fh.write(b"")
        try:
            extract_rom_archive(path)
        except ValueError as exc:
            assert "empty" in str(exc)
        else:
            raise AssertionError("expected ValueError for empty archive")


def test_extract_rejects_directory_only_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_tar(tmp, {}, gzipped=True)
        try:
            extract_rom_archive(path)
        except ValueError as exc:
            assert ("no files" in str(exc)) or ("no members" in str(exc))
        else:
            raise AssertionError("expected ValueError for directory-only archive")


def test_extract_rejects_path_traversal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "evil.tgz")
        with tarfile.open(path, "w:gz") as tf:
            info = tarfile.TarInfo("../../etc/passwd")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"evil"))
        try:
            extract_rom_archive(path)
        except ValueError as exc:
            assert "unsafe" in str(exc)
        else:
            raise AssertionError("expected ValueError for path traversal")


def test_extract_rejects_absolute_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "abs.tgz")
        with tarfile.open(path, "w:gz") as tf:
            info = tarfile.TarInfo("/etc/passwd")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"evil"))
        try:
            extract_rom_archive(path)
        except ValueError as exc:
            assert "unsafe" in str(exc)
        else:
            raise AssertionError("expected ValueError for absolute path")


# ---------- codename detection ----------

_BUILD_PROP_FOO = (
    b"# build.prop\n"
    b"ro.product.manufacturer=Xiaomi\n"
    b"ro.product.brand=Xiaomi\n"
    b"ro.product.device=zircon\n"
    b"ro.build.version.release=16\n"
    b"ro.build.id=OS3.0.303.0.WNOCNXM\n"
    b"ro.build.fingerprint=Xiaomi/zircon_global/zircon:16/OS3.0.303.0.WNOCNXM/test-keys\n"
    b"ro.build.version.security_patch=2026-04-01\n"
    b"ro.product.locale.region=cn\n"
)


def test_codename_from_build_prop_is_authoritative() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rom = _write_tar(
            tmp,
            {
                "images/build.prop": _BUILD_PROP_FOO,
                "images/flash_all_except_storage.sh": (
                    b"#!/bin/sh\nfastboot flash super super.img\n"
                ),
            },
        )
        rep = detect_codename(
            rom_path=rom,
            filename="zircon_images_OS3.0.303.0.WNOCNXM_20260416.0000.00_16.0_cn.tgz",
        )
    # build_prop is authoritative.
    assert rep.primary_codename == "zircon", rep
    assert "zircon" in rep.codenames
    sources = {ev.source for ev in rep.evidence}
    assert "build_prop" in sources


def test_codename_filename_pattern() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rom = _write_tar(tmp, {"images/a.txt": b"hi"})
        rep = detect_codename(
            rom_path=rom,
            filename="zircon_images_OS3.0.303.0.WNOCNXM_20260416.0000.00_16.0_cn_abcdef0123.tgz",
        )
    # No build.prop -> only filename evidence.
    assert rep.primary_codename == "zircon"
    sources = {ev.source for ev in rep.evidence}
    assert "filename" in sources


def test_codename_conflict_recorded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rom = _write_tar(
            tmp,
            {
                "images/build.prop": b"ro.product.device=zircon\n",
                # Different script codename hint.
                "images/flash_all_ares.sh": b"#!/bin/sh\n",
            },
        )
        rep = detect_codename(
            rom_path=rom,
            filename="zircon_images_X.tgz",
        )
    assert rep.primary_codename == "zircon"
    assert "ares" in rep.codenames or "zircon" in rep.codenames
    assert rep.conflicts != [] or len(rep.codenames) > 1


# ---------- inventory ----------

def test_inventory_discovers_images_and_scripts() -> None:
    if not os.path.isfile(FIXTURE):
        return
    inv = discover_image_inventory(FIXTURE)
    assert inv.super_images, inv
    assert inv.scripts, inv
    assert inv.build_props, inv


# ---------- pipeline ----------

def test_pipeline_produces_valid_profile_on_fixture() -> None:
    if not os.path.isfile(FIXTURE):
        return
    with tempfile.TemporaryDirectory() as tmp:
        art = analyze_rom_path(
            FIXTURE,
            output_dir=tmp,
            archive_url="https://example.test/rom.tgz",
            run_id="12345",
            job_id="super_test",
            profile_id="0000000000000001",
        )
    assert art.profile["device"]["primary_codename"], art.profile["device"]
    assert art.profile["rom"]["sha256"], art.profile["rom"]
    assert art.validation["ok"], art.validation
    # analysis_status confirms archive validation + super analysis
    assert art.profile["analysis_status"]["archive_validated"] is True
    assert art.profile["analysis_status"]["super_inspector_witness"] is True
    print(
        f"  pipeline OK: device={art.profile['device']['primary_codename']} "
        f"images={art.profile['analysis_status']['images_discovered']}"
    )


def test_pipeline_fails_on_invalid_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "bad.tgz")
        with open(bad, "wb") as fh:
            fh.write(b"not a tar file")
        try:
            analyze_rom_path(bad, output_dir=tmp)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for non-tar archive")


def test_pipeline_validates_required_fields() -> None:
    profile = {
        "schema_version": "1.0.0",
        "device": {"primary_codename": None},
        "rom": {"sha256": ""},
    }
    result = validate_profile(profile)
    assert not result["ok"]
    assert any("primary_codename" in e for e in result["errors"])


# ---------- super analysis delegation ----------

def test_super_analysis_delegates_to_dz_inspector() -> None:
    """Verify that analyze_super_image produces a non-empty analysis
    when the dz-inspector is available, and records the witness flag."""
    if not os.path.isfile(FIXTURE):
        return
    # Reuse the fixture; extract super.img
    import tarfile
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(FIXTURE, "r:*") as tf:
            for m in tf:
                if os.path.basename(m.name) == "super.img" and m.isfile():
                    extracted = os.path.join(tmp, "super.img")
                    with tf.extractfile(m) as src, open(extracted, "wb") as out:
                        out.write(src.read())
                    break
        rep = deadzone_mezo.analyze_super_image(extracted)
    assert rep.format in ("raw", "android_sparse"), rep.format
    if rep.witness_super_inspector:
        assert rep.partition_count is not None
        assert rep.partitions, "partitions should be non-empty"
        # Every partition should have a name.
        for p in rep.partitions:
            assert p.get("name"), p


# ---------- main ----------

if __name__ == "__main__":
    test_extract_accepts_valid_tgz()
    test_extract_rejects_html_error_page()
    test_extract_rejects_empty_archive()
    test_extract_rejects_directory_only_archive()
    test_extract_rejects_path_traversal()
    test_extract_rejects_absolute_path()
    test_codename_from_build_prop_is_authoritative()
    test_codename_filename_pattern()
    test_codename_conflict_recorded()
    test_inventory_discovers_images_and_scripts()
    test_pipeline_produces_valid_profile_on_fixture()
    test_pipeline_fails_on_invalid_archive()
    test_pipeline_validates_required_fields()
    test_super_analysis_delegates_to_dz_inspector()
    print("deadzone_mezo tests: OK")