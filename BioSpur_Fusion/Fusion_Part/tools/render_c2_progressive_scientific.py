#!/usr/bin/env python3
"""Render frozen C2 scientific candidates with no scientific CLI overrides."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN_RELATIVE = Path("logs/c2_basis_progressive_20260829T102836Z")
AMENDMENT_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_010.json"
SEAL_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_010.json"
ACTIVATION_RELATIVE = RUN_RELATIVE / "P2_REAL_TRAINING_FIT_ACTIVATION_001.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _load_immutable(relative: Path) -> Mapping[str, Any]:
    path = (WORKSPACE / relative).resolve()
    path.relative_to(WORKSPACE)
    if not path.is_file() or path.stat().st_mode & 0o222:
        raise RuntimeError(f"renderer authority is missing or mutable: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_manifest(value: str) -> Path:
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative.parent != RUN_RELATIVE
        or not relative.name.startswith("P2_REAL_FROZEN_SCIENTIFIC_STATE_")
        or relative.suffix != ".json"
    ):
        raise argparse.ArgumentTypeError(
            "frozen manifest must be one P2_REAL_FROZEN_SCIENTIFIC_STATE_*.json file in the sealed run directory"
        )
    return relative


def _write_new_immutable(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-manifest", required=True, type=_relative_manifest)
    args = parser.parse_args()
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("scientific renderer must run only from canonical Fusion_Part")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    run_tmp = (WORKSPACE / RUN_RELATIVE / "tmp").resolve()
    run_tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(run_tmp)

    amendment = dict(_load_immutable(AMENDMENT_RELATIVE))
    seal = dict(_load_immutable(SEAL_RELATIVE))
    activation = dict(_load_immutable(ACTIVATION_RELATIVE))
    settings = amendment["effective_settings"]
    exact_seal_binding = {
        "path": str(SEAL_RELATIVE), "sha256": _sha(WORKSPACE / SEAL_RELATIVE),
    }
    exact_activation_binding = {
        "path": str(ACTIVATION_RELATIVE), "sha256": _sha(WORKSPACE / ACTIVATION_RELATIVE),
    }
    if (
        amendment.get("schema") != "biospur-c2-active-parameter-registry-prefit-amendment-v2"
        or seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
        or seal.get("amendment") != {
            "path": str(AMENDMENT_RELATIVE), "sha256": _sha(WORKSPACE / AMENDMENT_RELATIVE),
        }
        or seal.get("settings_semantic_sha256") != _semantic_sha(settings)
        or seal.get("qualified_source_hashes") != amendment.get("qualified_source_hashes")
        or activation.get("schema") != "biospur-c2-real-training-fit-activation-v1"
        or activation.get("prefit_registry_seal") != exact_seal_binding
        or activation.get("settings_semantic_sha256") != seal.get("settings_semantic_sha256")
        or activation.get("qualified_source_hashes") != seal.get("qualified_source_hashes")
        or activation.get("execution_authorized") is not True
        or activation.get("heldout_opened") is not False
    ):
        raise RuntimeError("scientific renderer amendment/seal authority is inconsistent")
    for relative, expected in seal["qualified_source_hashes"].items():
        path = (WORKSPACE / relative).resolve()
        path.relative_to(WORKSPACE)
        if not path.is_file() or _sha(path) != expected:
            raise RuntimeError(f"scientific renderer qualified source changed: {relative}")

    manifest_path = WORKSPACE / args.frozen_manifest
    manifest = _load_immutable(args.frozen_manifest)
    if (
        manifest.get("prefit_registry_seal") != exact_seal_binding
        or manifest.get("real_fit_activation") != exact_activation_binding
        or manifest.get("settings_semantic_sha256") != seal["settings_semantic_sha256"]
        or manifest.get("qualified_source_hashes") != seal["qualified_source_hashes"]
    ):
        raise RuntimeError("renderer frozen state is not bound to this exact fresh-verified authority")
    suffix = args.frozen_manifest.stem.rsplit("_", 1)[-1]
    output_relative = RUN_RELATIVE / f"P2_SCIENTIFIC_RENDER_{suffix}"
    output_directory = WORKSPACE / output_relative

    from biospur_fusion.v0.c2_progressive.scientific_renderer import (
        render_registered_scientific_triviews,
    )

    result = dict(render_registered_scientific_triviews(
        manifest_path=manifest_path,
        output_directory=output_directory,
        settings=settings,
    ))
    for row in result["artifacts"]:
        (WORKSPACE / row["path"]).chmod(0o444)
    for row in result["unavailable_diagnostic_artifacts"]:
        (WORKSPACE / row["path"]).chmod(0o444)
    result.update({
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "prefit_registry_seal": {
            "path": str(SEAL_RELATIVE),
            "sha256": _sha(WORKSPACE / SEAL_RELATIVE),
        },
        "settings_semantic_sha256": seal["settings_semantic_sha256"],
        "qualified_source_hashes": seal["qualified_source_hashes"],
        "actual_pixels_personally_inspected": False,
        "independent_pixel_acceptance": False,
        "status": "RENDERED_CANDIDATE_NOT_PASS;PIXEL_INSPECTION_REQUIRED",
    })
    audit_path = output_directory / "SCIENTIFIC_RENDER_AUDIT.json"
    _write_new_immutable(audit_path, result)
    print(json.dumps({
        "render_audit": {
            "path": str(audit_path.relative_to(WORKSPACE)),
            "sha256": _sha(audit_path),
        },
        "image_count": len(result["artifacts"]) + len(
            result["unavailable_diagnostic_artifacts"]
        ),
        "unavailable_checkpoint_count": len(result["unavailable_registered_checkpoints"]),
        "scientific_acceptance_pass": False,
        "pixel_inspection_required": True,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
