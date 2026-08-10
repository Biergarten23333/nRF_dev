"""Prepare and finalize immutable OTA build identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import tempfile
from pathlib import Path

PREPARED_SCHEMA = "biospur-ota-prepared-identity-v1"
SCHEMA = "biospur-ota-build-identity-v2"
REQUIRED_INPUTS = {"source_commit", "dirty_state_digest", "effective_configs",
                   "sdk_patch_identity", "toolchain"}


def canonical_digest(inputs: dict[str, object]) -> str:
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def atomic_write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def validate_inputs(inputs: dict[str, object]) -> None:
    missing = sorted(REQUIRED_INPUTS - inputs.keys())
    if missing:
        raise ValueError(f"build inputs missing required keys: {missing}")


def prepare_identity(inputs: dict[str, object]) -> dict[str, object]:
    validate_inputs(inputs)
    return {"schema": PREPARED_SCHEMA, "fwid": canonical_digest(inputs),
            "build_inputs": inputs}


def mcuboot_image_hash(payload: bytes) -> str:
    if len(payload) < 32:
        raise ValueError("signed payload is too small for an MCUboot header")
    magic, _, header_size, protected_size, image_size, _, _, _ = struct.unpack_from(
        "<IIHHII8sI", payload, 0)
    if magic != 0x96F3B83D:
        raise ValueError(f"invalid MCUboot image magic 0x{magic:08x}")
    hashed_length = header_size + image_size + protected_size
    if hashed_length > len(payload):
        raise ValueError("MCUboot hashed region exceeds signed payload")
    return hashlib.sha256(payload[:hashed_length]).hexdigest()


def finalize_identity(prepared: dict[str, object], signed_payload: Path) -> dict[str, object]:
    if prepared.get("schema") != PREPARED_SCHEMA:
        raise ValueError("unsupported prepared identity schema")
    inputs = prepared.get("build_inputs")
    if not isinstance(inputs, dict):
        raise ValueError("prepared identity lacks build inputs")
    expected_fwid = canonical_digest(inputs)
    if prepared.get("fwid") != expected_fwid:
        raise ValueError("prepared FWID does not match its build inputs")
    payload = signed_payload.read_bytes()
    embedded = expected_fwid.encode("ascii")
    if embedded not in payload:
        raise ValueError("final payload does not embed the prepared FWID")
    return {
        "schema": SCHEMA, "fwid": expected_fwid,
        "signed_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "mcuboot_image_sha256": mcuboot_image_hash(payload),
        "payload_path": str(signed_payload.resolve()), "build_inputs": inputs,
        "source_commit": inputs["source_commit"],
        "dirty_state_digest": inputs["dirty_state_digest"],
        "sdk_patch_identity": inputs["sdk_patch_identity"],
        "toolchain": inputs["toolchain"],
    }


def register(manifest: dict[str, object], registry_path: Path) -> None:
    if manifest.get("schema") != SCHEMA:
        raise ValueError("unsupported finalized identity schema")
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    fwid = str(manifest["fwid"])
    binding = {"signed_payload_sha256": manifest["signed_payload_sha256"],
               "mcuboot_image_sha256": manifest["mcuboot_image_sha256"]}
    previous = registry.get(fwid)
    if previous is not None and previous != binding:
        raise ValueError(f"FWID collision: {fwid} has a different payload binding")
    registry[fwid] = binding
    atomic_write(registry_path, registry)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--inputs", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--prepared", required=True, type=Path)
    finalize.add_argument("--signed-payload", required=True, type=Path)
    finalize.add_argument("--output", required=True, type=Path)
    finalize.add_argument("--registry", required=True, type=Path)
    args = parser.parse_args()
    if args.stage == "prepare":
        value = prepare_identity(json.loads(args.inputs.read_text(encoding="utf-8")))
        atomic_write(args.output, value)
        print(f"FWID={value['fwid']}")
    else:
        value = finalize_identity(json.loads(args.prepared.read_text(encoding="utf-8")),
                                  args.signed_payload)
        register(value, args.registry); atomic_write(args.output, value)
        print(f"FWID={value['fwid']} PAYLOAD_SHA256={value['signed_payload_sha256']} "
              f"MCUBOOT_IMAGE_SHA256={value['mcuboot_image_sha256']}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
