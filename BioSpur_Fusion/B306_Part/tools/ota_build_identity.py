"""Generate and validate immutable OTA build identity manifests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import argparse
from pathlib import Path

SCHEMA = "biospur-ota-build-identity-v1"


def canonical_digest(inputs: dict[str, object]) -> str:
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_manifest(inputs: dict[str, object], signed_payload: Path) -> dict[str, object]:
    payload_sha = hashlib.sha256(signed_payload.read_bytes()).hexdigest()
    return {
        "schema": SCHEMA,
        "fwid": canonical_digest(inputs),
        "signed_payload_sha256": payload_sha,
        "build_inputs": inputs,
    }


def atomic_write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def register(manifest: dict[str, object], registry_path: Path) -> None:
    if manifest.get("schema") != SCHEMA:
        raise ValueError("unsupported identity manifest schema")
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    fwid = str(manifest["fwid"])
    payload = str(manifest["signed_payload_sha256"])
    previous = registry.get(fwid)
    if previous is not None and previous != payload:
        raise ValueError(
            f"FWID collision: {fwid} was registered for {previous}, not {payload}"
        )
    registry[fwid] = payload
    atomic_write(registry_path, registry)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True, type=Path,
                        help="canonical JSON: source commit, dirty digest, configs, SDK patch, toolchain")
    parser.add_argument("--signed-payload", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    args = parser.parse_args()
    inputs = json.loads(args.inputs.read_text(encoding="utf-8"))
    required = {"source_commit", "dirty_state_digest", "effective_configs",
                "sdk_patch_identity", "toolchain"}
    missing = sorted(required - inputs.keys())
    if missing:
        raise SystemExit(f"build inputs missing required keys: {missing}")
    manifest = build_manifest(inputs, args.signed_payload)
    register(manifest, args.registry)
    atomic_write(args.output, manifest)
    print(f"FWID={manifest['fwid']} PAYLOAD_SHA256={manifest['signed_payload_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
