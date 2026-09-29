#!/usr/bin/env python3
"""End-to-end driver against the real Xiaomi Fastboot ROM.

This script downloads the ROM URL provided in the task spec, runs the
DeadZone-MEZO pipeline against it, and writes the full Device Profile +
extracted super + diagnostic artifacts to a local output directory.

It is deliberately self-contained: no GitHub Actions, no Lite/Port
publishing, no callback signing. It exercises the same code paths the
GitHub Actions worker runs but in a local Python process.

Usage:

    python3 .github/scripts/e2e_real_rom.py \
        --url https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com/.../zircon_images_*.tgz \
        --output ./real_rom_test

If the URL is unreachable the script can be pointed at a pre-downloaded
file via ``--rom-path``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "lib"))

import deadzone_mezo  # noqa: E402
from deadzone_mezo import analyze_rom_path  # noqa: E402

DEFAULT_URL = (
    "https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com/"
    "OS3.0.303.0.WNOCNXM/zircon_images_OS3.0.303.0.WNOCNXM_20260416.0000.00_"
    "16.0_cn_c754c7d760.tgz"
)


def _download(url: str, target: str, max_bytes: int = 16 * 1024 * 1024 * 1024) -> str:
    import urllib.request

    req = urllib.request.Request(
        url, headers={"User-Agent": "DeadZone-MEZO/1.0 (+https://github.com/mohammedmezo99)"}
    )
    print(f"downloading {url}")
    with urllib.request.urlopen(req, timeout=120) as resp, open(target + ".part", "wb") as out:
        total = 0
        while True:
            buf = resp.read(1 << 20)
            if not buf:
                break
            total += len(buf)
            if total > max_bytes:
                raise ValueError(f"download exceeds {max_bytes} bytes")
            out.write(buf)
    os.replace(target + ".part", target)
    return target


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(1 << 20)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="e2e_real_rom", description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="ROM URL")
    parser.add_argument("--rom-path", default="", help="Use a pre-downloaded file instead of --url")
    parser.add_argument("--output", required=True, help="output directory")
    parser.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024 * 1024)
    parser.add_argument("--json", action="store_true", help="emit JSON summary to stdout")
    args = parser.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    if args.rom_path:
        rom_path = args.rom_path
    else:
        rom_path = os.path.join(args.output, "rom.tgz")
        if not os.path.isfile(rom_path):
            _download(args.url, rom_path, max_bytes=args.max_bytes)
        else:
            print(f"reusing existing ROM at {rom_path}")

    rom_sha = _sha256(rom_path)
    print(f"ROM sha256: {rom_sha}")
    print(f"ROM size:   {os.path.getsize(rom_path)} bytes")

    started = time.monotonic()
    art = analyze_rom_path(
        rom_path,
        output_dir=args.output,
        archive_url=args.url,
        run_id="local-e2e",
        job_id="local",
        profile_id="0000000000000001",
        generator_repository="mohammedmezo99/DeadZone-MEZO",
        generator_ref="main",
        generator_resolved_sha="0000000000000000000000000000000000000000",
    )
    elapsed = time.monotonic() - started

    summary = {
        "rom_sha256": rom_sha,
        "rom_size": os.path.getsize(rom_path),
        "elapsed_seconds": round(elapsed, 2),
        "device": art.profile["device"],
        "rom": art.profile["rom"],
        "codename": art.profile["codename"],
        "analysis_status": art.profile["analysis_status"],
        "super_format": art.profile["super"]["format"],
        "super_partition_count": art.profile["super"]["partition_count"],
        "super_lp_metadata_size": art.profile["super"]["lp_metadata_size"],
        "super_inspector_witness": art.profile["super"]["witness_super_inspector"],
        "fastboot_method": art.profile["fastboot"]["fastboot_method"],
        "images_count": art.profile["inventory"]["image_count"],
        "validation": art.validation,
    }
    summary_path = os.path.join(args.output, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print(f"summary written to {summary_path}")
    if args.json:
        json.dump(summary, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(
            f"  device       : {art.profile['device']['primary_codename']}"
        )
        print(f"  android      : {art.profile['rom'].get('android_version')}")
        print(f"  build_id     : {art.profile['rom'].get('build_id')}")
        print(f"  super_format : {art.profile['super']['format']}")
        print(
            f"  partitions   : {art.profile['super']['partition_count']} "
            f"(witness: {art.profile['super']['witness_super_inspector']})"
        )
        print(
            f"  validation   : ok={art.validation['ok']} "
            f"errors={len(art.validation['errors'])} "
            f"warnings={len(art.validation['warnings'])}"
        )
        if art.validation["errors"]:
            print("  ERRORS:")
            for e in art.validation["errors"]:
                print(f"    - {e}")
    return 0 if art.validation["ok"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))