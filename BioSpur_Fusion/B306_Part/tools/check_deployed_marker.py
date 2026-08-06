#!/usr/bin/env python3
"""Reject accidental reuse of a deployed B306 firmware marker.

STANDING RULE — read before "fixing" a mismatch here.

The artifact this guard hashes is `zephyr.signed.bin`, and under
`SB_CONFIG_BOOT_SIGNATURE_TYPE_ECDSA_P256` that file is NOT reproducible:
imgtool draws a fresh random nonce for every signature, so two builds of
byte-identical firmware differ in their signature trailer (measured on v40:
identical for 219,648 bytes, differing in the last 66).

Therefore:

  * A hash mismatch reported here does NOT mean the image is corrupt. On a
    marker that has already been deployed it means somebody rebuilt it, and the
    correct response is to cut a NEW MARKER -- never to update the registry to
    match the rebuild, and never to conclude the flashed bytes were bad.
  * The registered hash is an ARTIFACT identity. The deployment gate must
    compare against the frozen file that was actually signed and flashed.
    "Verify by rebuilding" is not a valid check and must not be added.
  * Build reproducibility is judged elsewhere, on the unsigned application and
    on MCUboot, which are byte-stable.

See B306_Part/firmware/README.md, "Standing rule: what a signed hash is".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


MARKER_RE = re.compile(rb"(?<![A-Za-z0-9._-])(b306-[A-Za-z0-9._-]+)\x00")


def marker_from_elf(path: Path) -> str:
    matches = {
        match.group(1).decode("ascii")
        for match in MARKER_RE.finditer(path.read_bytes())
    }
    if len(matches) != 1:
        raise SystemExit(
            f"MARKER_GUARD_FAIL expected one embedded b306 marker, found "
            f"{sorted(matches)} in {path}"
        )
    return matches.pop()


def check(elf: Path, artifact: Path, manifest_path: Path) -> tuple[str, str]:
    manifest = json.loads(manifest_path.read_text())
    marker = marker_from_elf(elf)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    entry = manifest["markers"].get(marker)
    if entry is None:
        return marker, digest
    if entry.get("retired", False):
        raise SystemExit(
            f"MARKER_GUARD_FAIL marker={marker} status=retired sha256={digest}"
        )
    if digest not in entry.get("signed_sha256", []):
        raise SystemExit(
            f"MARKER_GUARD_FAIL marker={marker} deployed=1 "
            f"sha256={digest} expected={entry.get('signed_sha256', [])} "
            f"note=ECDSA-P256_resigns_with_a_fresh_nonce_so_a_rebuild_of_"
            f"identical_source_ALWAYS_produces_a_new_signed_hash;_this_is_not_"
            f"image_corruption;_cut_a_new_marker_instead_of_editing_the_registry"
        )
    return marker, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    marker, digest = check(args.elf, args.artifact, args.manifest)
    print(f"MARKER_GUARD_PASS marker={marker} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

