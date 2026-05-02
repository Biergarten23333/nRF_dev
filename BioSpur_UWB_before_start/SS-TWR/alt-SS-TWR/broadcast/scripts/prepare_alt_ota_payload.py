#!/usr/bin/env python3
import argparse
import hashlib
import json
import time
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_header(path: Path, kind: str, marker: str, build_dir: str, dfu_zip: str) -> None:
    if kind == "tag":
        prefix = "APP_MASTER_OTA_TAG"
    elif kind == "anchor":
        prefix = "APP_MASTER_OTA_ANCHOR"
    else:
        raise ValueError(kind)
    path.write_text(
        "#pragma once\n\n"
        f"#define {prefix}_FW_MARKER \"{marker}\"\n"
        f"#define {prefix}_BUILD_DIR \"{build_dir}\"\n"
        f"#define {prefix}_DFU_ZIP \"{dfu_zip}\"\n",
        encoding="utf-8",
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Prepare isolated alt-SS-TWR OTA payload metadata")
    p.add_argument("--kind", choices=["tag", "anchor"], required=True)
    p.add_argument("--marker", required=True)
    p.add_argument("--build-dir", required=True)
    p.add_argument("--signed-bin", required=True)
    p.add_argument("--dfu-zip", required=True)
    p.add_argument("--control-build-dir", default="-")
    p.add_argument("--generated-dir", default="apps/master_ota/generated")
    args = p.parse_args()

    gen = Path(args.generated_dir)
    gen.mkdir(parents=True, exist_ok=True)
    signed = Path(args.signed_bin)
    dfu = Path(args.dfu_zip)
    if not signed.exists():
        raise SystemExit(f"missing signed image: {signed}")
    if not dfu.exists():
        raise SystemExit(f"missing dfu zip: {dfu}")

    # Generate the C array used by apps/master_ota/src/main.c. Keep the existing symbol name.
    from subprocess import check_call
    ota_inc = gen / "ota_image.inc"
    check_call([
        "python3",
        "scripts/gen_ota_image_inc.py",
        str(signed),
        str(ota_inc),
        "--symbol-prefix",
        "tag_ota_image",
    ])

    manifest = {
        "kind": f"{args.kind}_ota_bundle",
        "fw_marker": args.marker,
        f"{args.kind}_build_dir": args.build_dir,
        "control_build_dir": args.control_build_dir,
        "signed_bin": str(signed),
        "dfu_zip": str(dfu),
        "generated_at_epoch": int(time.time()),
    }
    (gen / f"{args.kind}_ota_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    write_header(
        gen / f"{args.kind}_ota_manifest.h",
        args.kind,
        args.marker,
        args.build_dir,
        str(dfu),
    )

    active = {
        "kind": args.kind,
        "manifest_kind": manifest["kind"],
        "fw_marker": args.marker,
        f"{args.kind}_build_dir": args.build_dir,
        "control_build_dir": args.control_build_dir,
        "signed_bin": str(signed),
        "signed_bin_sha256": sha256_file(signed),
        "dfu_zip": str(dfu),
        "dfu_zip_sha256": sha256_file(dfu),
        "ota_image_inc": str(ota_inc),
        "ota_image_inc_sha256": sha256_file(ota_inc),
        "generated_at_epoch": manifest["generated_at_epoch"],
    }
    (gen / "active_ota_payload.json").write_text(
        json.dumps(active, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
