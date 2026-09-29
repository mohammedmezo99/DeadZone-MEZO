#!/usr/bin/env python3
"""Build a synthetic Xiaomi Fastboot ROM fixture for local E2E tests.

The fixture mimics the layout of the real ``zircon_images_*.tgz`` ROM
delivered to the SuperInspector worker: a single top-level directory,
``flash_all_except_storage.sh``/``.bat`` flash scripts, an ``images/``
subtree with a real (raw) ``super.img`` whose LP metadata is the
same shape the worker would see on the real ROM.

Generated layout::

    <codename>_images_OS3.0.303.0.WNOCNXM_16.0/
        images/build.prop         -> zircon identity
        images/anti_version.txt   -> "0"
        images/super.img          -> raw super with LP metadata at end
        images/boot.img           -> 1 KiB zero-filled
        flash_all_except_storage.sh
        flash_all_except_storage.bat

Usage::

    python3 .github/scripts/build_zircon_fixture.py \
        --output tests_fixtures/zircon_fixture.tgz
"""
from __future__ import annotations

import argparse
import io
import os
import struct
import sys
import tarfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "lib"))


# AOSP LP metadata magic values (per AOSP liblp metadata_format.h).
LP_GEOMETRY_MAGIC = 0x616C4467
LP_HEADER_MAGIC = 0x414C5030  # '0PLA'
LP_PARTITION_MAGIC = 0x505A7672  # 'rvZP' ('Pvr\0')
LP_EXTENT_MAGIC = 0x0C5D
LP_GROUP_MAGIC = 0x0C5A
LP_BLOCK_DEVICE_MAGIC = 0x0C5B


def _build_lp_blob() -> bytes:
    """Build a minimal LP metadata blob with one 'system' partition.

    Schema (per AOSP ``metadata_format.h``):

    - 28-byte geometry block
    - 56-byte header (magic + version + sizes + entry counts)
    - 4 tables (partitions / extents / groups / block_devices)
      each prefixed by a 16-byte table header.
    """
    PARTITION_ENTRY_SIZE = 44
    EXTENT_ENTRY_SIZE = 28
    GROUP_ENTRY_SIZE = 44
    BLOCK_DEVICE_ENTRY_SIZE = 56

    metadata_max_size = 4096

    # Geometry: 6 u32 fields = 24 bytes. We pad to 28 bytes because the
    # ``deadzone_superinspector`` LP parser requires ``struct_size >= 28``
    # and uses ``struct_size`` as the header offset.
    geometry = struct.pack(
        "<IIIIII",
        LP_GEOMETRY_MAGIC, 28, 0, metadata_max_size, 1, 4096,
    ) + b"\x00" * 4

    # Header (56 bytes): u32 magic + u16 major + u16 minor + u32 header_size +
    # u32 header_checksum + u32 tables_size + u32 tables_checksum + 4 * (u32
    # entry_size + u32 entry_count)
    tables_size = (
        PARTITION_ENTRY_SIZE
        + EXTENT_ENTRY_SIZE
        + GROUP_ENTRY_SIZE
        + BLOCK_DEVICE_ENTRY_SIZE
        + 16 * 4
    )

    header = struct.pack(
        "<I",
        LP_HEADER_MAGIC,
    ) + struct.pack(
        "<HHI",
        1, 0, 56,  # major, minor, header_size
    ) + struct.pack(
        "<III",
        0,            # header_checksum
        tables_size,
        0,            # tables_checksum
    ) + struct.pack(
        "<IIIIIIII",
        PARTITION_ENTRY_SIZE, 1,
        EXTENT_ENTRY_SIZE, 1,
        GROUP_ENTRY_SIZE, 1,
        BLOCK_DEVICE_ENTRY_SIZE, 1,
    )
    assert len(header) == 56, f"header is {len(header)} bytes"

    # Partition table: 1 entry "system"
    partition_entry = (
        struct.pack("<IIII", 0, 0, 1, 0) + b"system\x00"
    ).ljust(PARTITION_ENTRY_SIZE, b"\x00")
    # Table headers (per deadzone_superinspector parse_table_header):
    # u32 magic + u16 major + u16 minor + u32 entry_size + u32 entry_count
    # (16 bytes without a separate header_size field).
    partition_table_header = struct.pack(
        "<IHHII",
        LP_PARTITION_MAGIC, 1, 0, PARTITION_ENTRY_SIZE, 1,
    )

    # Extent: linear (LP_TARGET_TYPE_LINEAR = 1), 2048 sectors at sector 0 of block device 0.
    # Layout: u32 type + u32 num_sectors + u32 target_type + u64 target_data + u64 target_source
    # = 4 + 4 + 4 + 8 + 8 = 28 bytes.
    LP_TARGET_TYPE_LINEAR = 1
    extent_entry = struct.pack(
        "<IIIQQ",
        0,           # type (none, deprecated)
        2048,        # num_sectors
        LP_TARGET_TYPE_LINEAR,  # target_type
        0,           # target_data (sector offset)
        0,           # target_source (block device index)
    ).ljust(EXTENT_ENTRY_SIZE, b"\x00")
    extent_table_header = struct.pack(
        "<IHHII",
        LP_EXTENT_MAGIC, 1, 0, EXTENT_ENTRY_SIZE, 1,
    )

    # Group: "default", maximum_size=8 MiB
    group_entry = (
        struct.pack("<II", 0, 0)
        + struct.pack("<Q", 8 * 1024 * 1024)
        + b"default\x00"
    ).ljust(GROUP_ENTRY_SIZE, b"\x00")
    group_table_header = struct.pack(
        "<IHHII",
        LP_GROUP_MAGIC, 1, 0, GROUP_ENTRY_SIZE, 1,
    )

    # Block device: "super", alignment 4096, size 8 GiB.
    # Parser layout: u32 first_logical_sector (0) + u32 alignment (4) + u32
    # alignment_offset (8) + 4 bytes reserved (12) + u64 size (16) + u32 flags
    # (24) + u8[16] reserved (28) + cstring partition_name (44) + cstring name.
    bd_entry = (
        struct.pack("<III", 0, 4096, 0)          # first_logical_sector, alignment, alignment_offset
        + struct.pack("<I", 0)                   # reserved (padding to offset 16)
        + struct.pack("<Q", 8 * 1024 * 1024 * 1024)  # size = 8 GiB at offset 16
        + struct.pack("<I", 0)                   # flags at offset 24
        + b"\x00" * 16                           # reserved at offset 28
        + b"\x00"                                # partition_name (empty) at offset 44
        + b"super\x00"                           # name
    ).ljust(BLOCK_DEVICE_ENTRY_SIZE, b"\x00")
    bd_table_header = struct.pack(
        "<IHHII",
        LP_BLOCK_DEVICE_MAGIC, 1, 0, BLOCK_DEVICE_ENTRY_SIZE, 1,
    )

    tables = (
        partition_table_header + partition_entry
        + extent_table_header + extent_entry
        + group_table_header + group_entry
        + bd_table_header + bd_entry
    )

    blob = geometry + header + tables
    blob = blob.ljust(metadata_max_size, b"\x00")
    return blob


def _build_super_raw() -> bytes:
    """Build a raw super.img with LP metadata at the end."""
    body = b"\x00" * (1 * 1024 * 1024)  # 1 MiB body
    lp = _build_lp_blob()
    return body + lp


_BUILD_PROP = (
    b"# build.prop for the synthetic zircon fixture\n"
    b"ro.product.manufacturer=Xiaomi\n"
    b"ro.product.brand=Xiaomi\n"
    b"ro.product.device=zircon\n"
    b"ro.product.model=Synthetic zircon\n"
    b"ro.product.product=zircon_global\n"
    b"ro.product.variant=zircon_variant\n"
    b"ro.product.locale.region=cn\n"
    b"ro.product.board=zircon\n"
    b"ro.build.version.release=16\n"
    b"ro.build.id=OS3.0.303.0.WNOCNXM\n"
    b"ro.build.fingerprint=Xiaomi/zircon_global/zircon:16/OS3.0.303.0.WNOCNXM/test-keys\n"
    b"ro.build.version.incremental=OS3.0.303.0.WNOCNXM\n"
    b"ro.build.version.security_patch=2026-04-01\n"
    b"ro.build.display.id=OS3.0.303.0.WNOCNXM\n"
    b"ro.miui.ui.version.name=OS3\n"
    b"ro.build.version.baseband=1.0.0\n"
)

_FLASH_SH = (
    b"#!/bin/sh\n"
    b"set -e\n"
    b"fastboot $* getvar product 2>&1 | grep \"zircon\"\n"
    b"fastboot $* flash super            images/super.img\n"
    b"fastboot $* flash boot              images/boot.img\n"
    b"fastboot $* reboot-bootloader\n"
)

_FLASH_BAT = (
    b"@echo off\r\n"
    b"fastboot %* flash super            images\\super.img\r\n"
    b"fastboot %* flash boot              images\\boot.img\r\n"
    b"fastboot %* reboot-bootloader\r\n"
)


def build(output_path: str) -> None:
    super_raw = _build_super_raw()
    boot_img = b"\x00" * 1024
    files = {
        "zircon_images_OS3.0.303.0.WNOCNXM_16.0/images/build.prop": _BUILD_PROP,
        "zircon_images_OS3.0.303.0.WNOCNXM_16.0/images/anti_version.txt": b"0",
        "zircon_images_OS3.0.303.0.WNOCNXM_16.0/images/super.img": super_raw,
        "zircon_images_OS3.0.303.0.WNOCNXM_16.0/images/boot.img": boot_img,
        "zircon_images_OS3.0.303.0.WNOCNXM_16.0/flash_all_except_storage.sh": _FLASH_SH,
        "zircon_images_OS3.0.303.0.WNOCNXM_16.0/flash_all_except_storage.bat": _FLASH_BAT,
    }
    with tarfile.open(output_path, "w:gz") as tf:
        # Top-level directory
        top_info = tarfile.TarInfo(name="zircon_images_OS3.0.303.0.WNOCNXM_16.0")
        top_info.type = tarfile.DIRTYPE
        top_info.mode = 0o755
        tf.addfile(top_info)
        # images/ directory
        img_info = tarfile.TarInfo(
            name="zircon_images_OS3.0.303.0.WNOCNXM_16.0/images"
        )
        img_info.type = tarfile.DIRTYPE
        img_info.mode = 0o755
        tf.addfile(img_info)
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    print(f"wrote {output_path} ({os.path.getsize(output_path)} bytes)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="build_zircon_fixture")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    build(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))