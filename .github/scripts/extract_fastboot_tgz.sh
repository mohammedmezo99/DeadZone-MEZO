#!/usr/bin/env bash
# DeadZone-MEZO Fastboot TGZ extractor.
# Lite-equivalent extractor for Xiaomi Fastboot ROMs distributed as
# `.tgz` archives. Used by the GitHub Actions worker and the local E2E
# test driver.
#
# Behavior:
#   - extracts every member of the archive into the working directory,
#     refusing entries that escape the destination (path-traversal).
#   - locates the Fastboot `images/` directory (the only one whose
#     contents include `super.img` or split chunks).
#   - prints a machine-readable JSON summary that the Python pipeline
#     consumes for codename / version / partition inventory.
#
# Usage:
#   extract_fastboot_tgz.sh <rom.tgz> <work_dir>
#
# Side effects:
#   <work_dir>/extracted/      - extracted archive tree
#   <work_dir>/extract.json    - machine-readable extraction summary
#
# Exit codes:
#   0 - archive extracted successfully
#   1 - usage / archive validation failure
#   2 - path-traversal rejection
#   3 - no Fastboot images directory found
set -Eeuo pipefail

rom="${1:-}"
work_dir="${2:-}"
if [[ -z "$rom" || -z "$work_dir" ]]; then
  echo "usage: $(basename "$0") <rom.tgz> <work_dir>" >&2
  exit 1
fi
if [[ ! -s "$rom" ]]; then
  echo "error: ROM not found or empty: $rom" >&2
  exit 1
fi

mkdir -p "$work_dir"
extracted="$work_dir/extracted"
rm -rf "$extracted"
mkdir -p "$extracted"

# Refuse path-traversal entries before extraction.
if tar -tzf "$rom" 2>/dev/null | grep -qE '(^|/)\.\.(/|$)'; then
  echo "error: archive contains unsafe path traversal entries: $rom" >&2
  exit 2
fi

case "$rom" in
  *.tgz|*.tar.gz)
    tar -xzf "$rom" -C "$extracted"
    ;;
  *.tar)
    tar -xf "$rom" -C "$extracted"
    ;;
  *)
    # Generic: try gz first; fall back to plain tar.
    if tar -xzf "$rom" -C "$extracted" 2>/dev/null; then
      :
    elif tar -xf "$rom" -C "$extracted" 2>/dev/null; then
      :
    else
      echo "error: archive is not a recognised tar/tgz: $rom" >&2
      exit 1
    fi
    ;;
esac

# Find the canonical images directory.
images_dir="$(python3 - "$extracted" <<'PY'
import os, sys
root = sys.argv[1]
candidates = []
for r, dirs, files in os.walk(root):
    if "images" in dirs:
        candidates.append(os.path.join(r, "images"))
for c in sorted(candidates):
    super_path = os.path.join(c, "super.img")
    if os.path.exists(super_path):
        print(c)
        sys.exit(0)
# Fallback: any images dir.
if candidates:
    print(sorted(candidates)[0])
    sys.exit(0)
sys.exit(1)
PY
)" || {
  echo "error: no Fastboot images/ directory found in $rom" >&2
  exit 3
}

# Identify a top-level dir (if the archive is wrapped in a single folder).
top_dir="$(python3 - "$extracted" <<'PY'
import os, sys
root = sys.argv[1]
entries = sorted(os.listdir(root))
if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])):
    print(entries[0])
else:
    print("")
PY
)"

# Count images and scripts.
image_count="$(find "$extracted" -type f \( -name '*.img' -o -name '*.img.*' \) | wc -l | tr -d ' ')"
script_count="$(find "$extracted" -type f \( -name 'flash_*.sh' -o -name 'flash_*.bat' -o -name 'flash.sh' -o -name 'flash.bat' \) | wc -l | tr -d ' ')"
prop_count="$(find "$extracted" -type f -name '*.prop' | wc -l | tr -d ' ')"
super_path="$images_dir/super.img"
super_size=0
super_format="missing"
if [[ -f "$super_path" ]]; then
  super_size=$(stat -c '%s' "$super_path")
  magic=$(od -An -N4 -tx1 -- "$super_path" 2>/dev/null | tr -d ' \t\n\r' || true)
  if [[ "$magic" == "3aff26ed" ]]; then
    super_format="android_sparse"
  else
    super_format="raw"
  fi
elif find "$images_dir" -maxdepth 1 -type f -name 'super.img.*' 2>/dev/null | grep -q .; then
  super_format="split_raw_or_sparse"
fi

# Hash the archive for the worker report.
sha256="$(sha256sum "$rom" | awk '{print $1}')"

python3 - "$work_dir" "$sha256" "$images_dir" "$top_dir" "$image_count" "$script_count" "$prop_count" "$super_path" "$super_size" "$super_format" <<'PY'
import json, os, sys
out, sha, images, top, ic, sc, pc, sp, ss, fmt = sys.argv[1:]
doc = {
    "archive_sha256": sha,
    "images_dir": images,
    "top_level_dir": top,
    "image_count": int(ic),
    "script_count": int(sc),
    "build_prop_count": int(pc),
    "super": {
        "path": sp,
        "size_bytes": int(ss),
        "format": fmt,
    },
}
with open(os.path.join(out, "extract.json"), "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY

echo "extracted -> $extracted"
echo "images    -> $images_dir"
echo "super     -> ${super_path:-<missing>} ($super_format)"
echo "summary   -> $work_dir/extract.json"