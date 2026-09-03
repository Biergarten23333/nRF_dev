"""CLI for one bounded Root-R6A0 scaffold verification and evidence package."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import subprocess
import time

from .body import load_body_model
from .provenance import (
    disk_gate,
    file_inventory,
    git_snapshot,
    root_r4_verification,
    verify_protected_baseline,
)
from .rehearsal import run_c1_wiring_rehearsal
from .reporting import REQUIRED_ARTIFACTS, write_formal_artifacts
from .synthetic import build_synthetic_scenario, synthetic_gate_results


EXPECTED_ROOT_R4_SHA256 = "fb5000d759e35abcc7d568711d4b5e2ef2d3cca6862c76f7b1cb75c3ec35e48e"
RUN_PREFIX = "root_r6a0_whole_body_scaffold_"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fusion-root", type=Path, required=True)
    parser.add_argument("--git-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--protected-baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root-r4", type=Path, required=True)
    parser.add_argument("--typed-ledger", type=Path, required=True)
    parser.add_argument("--raw-range-events", type=Path, required=True)
    parser.add_argument("--m1", type=Path, required=True)
    parser.add_argument("--event-accounting", type=Path, required=True)
    parser.add_argument("--c1-duration-s", type=float, default=30.0)
    return parser


def _path_gate(args: argparse.Namespace) -> dict:
    fusion = args.fusion_root.resolve()
    expected = {
        "src": fusion / "src/biospur_fusion",
        "tests": fusion / "tests",
        "config": fusion / "config",
        "logs": fusion / "logs",
    }
    placement = sorted(path.name for path in fusion.glob("SOURCE_PLACEMENT_DECISION*.md"))
    output = args.output.resolve()
    checks = {
        "cwd_exact": Path.cwd().resolve() == fusion,
        "fusion_root_exists": fusion.is_dir(),
        "required_directories": all(path.is_dir() for path in expected.values()),
        "source_placement_documents": len(placement) >= 3,
        "config_inside_authorized_dir": args.config.resolve().parent == fusion / "config/root_r6a0",
        "baseline_inside_authorized_dir": args.protected_baseline.resolve().parent == fusion / "config/root_r6a0",
        "output_parent_exact": output.parent == fusion / "logs",
        "output_prefix": output.name.startswith(RUN_PREFIX),
        "output_absent": not output.exists(),
    }
    checks["pass"] = all(checks.values())
    if not checks["pass"]:
        raise RuntimeError(f"BLOCKED_WRONG_WORKTREE_OR_SOURCE_PLACEMENT: {checks}")
    return {"checks": checks, "placement_documents": placement}


def _pytest(fusion_root: Path) -> dict:
    started = time.monotonic()
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(fusion_root / "src")
    command = ["pytest", "-q", "-p", "no:cacheprovider", "tests/root_r6a0"]
    result = subprocess.run(
        command, cwd=fusion_root, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1200, check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {
        "command": " ".join(command),
        "returncode": result.returncode,
        "duration_seconds": time.monotonic() - started,
        "summary": lines[-1] if lines else "NO_OUTPUT",
        "stdout": result.stdout,
        "pass": result.returncode == 0,
    }


def _reproduction_command(args: argparse.Namespace) -> str:
    fusion = args.fusion_root.resolve()
    output_template = fusion / "logs" / f"{RUN_PREFIX}<NEW_UTC_TIMESTAMP>"
    values = [
        "PYTHONDONTWRITEBYTECODE=1",
        f"PYTHONPATH={shlex.quote(str(fusion / 'src'))}",
        "python3", "-m", "biospur_fusion.root_r6a0.runner",
        "--fusion-root", shlex.quote(str(fusion)),
        "--git-root", shlex.quote(str(args.git_root.resolve())),
        "--config", shlex.quote(str(args.config.resolve())),
        "--protected-baseline", shlex.quote(str(args.protected_baseline.resolve())),
        "--output", shlex.quote(str(output_template)),
        "--root-r4", shlex.quote(str(args.root_r4.resolve())),
        "--typed-ledger", shlex.quote(str(args.typed_ledger.resolve())),
        "--raw-range-events", shlex.quote(str(args.raw_range_events.resolve())),
        "--m1", shlex.quote(str(args.m1.resolve())),
        "--event-accounting", shlex.quote(str(args.event_accounting.resolve())),
        "--c1-duration-s", f"{args.c1_duration_s:g}",
    ]
    return " ".join(values)


def run(args: argparse.Namespace) -> int:
    start_monotonic = time.monotonic()
    created = datetime.now(timezone.utc).isoformat()
    fusion = args.fusion_root.resolve()
    path_gate = _path_gate(args)
    storage = disk_gate(fusion)
    if not storage["pass"]:
        raise RuntimeError(f"disk gate failed: {storage}")
    output = args.output.resolve()
    output.mkdir(parents=False, exist_ok=False)
    git_start = git_snapshot(args.git_root, "BioSpur_Fusion/Fusion_Part")
    protected_before = verify_protected_baseline(fusion, args.protected_baseline)
    if not protected_before["pass"]:
        raise RuntimeError(f"protected pre-edit baseline mismatch: {protected_before}")
    root_r4_before = root_r4_verification(args.root_r4, EXPECTED_ROOT_R4_SHA256)
    if not root_r4_before["manifest_exact"]:
        raise RuntimeError(f"frozen Root-R4 manifest mismatch: {root_r4_before}")

    print("Root-R6A0: deterministic whole-body synthetic graph", flush=True)
    model = load_body_model(args.config)
    scenario = build_synthetic_scenario(model)
    synthetic = synthetic_gate_results(scenario)
    if not synthetic["pass"]:
        raise RuntimeError(f"synthetic structural gates failed: {synthetic['checks']}")
    print("Root-R6A0: pytest contract suite", flush=True)
    pytest_result = _pytest(fusion)
    if not pytest_result["pass"]:
        raise RuntimeError(f"Root-R6A0 tests failed: {pytest_result['stdout']}")
    print("Root-R6A0: bounded C1 wiring rehearsal (no optimization)", flush=True)
    c1_summary, c1_graph = run_c1_wiring_rehearsal(
        model=model,
        typed_ledger_path=args.typed_ledger,
        raw_range_events_path=args.raw_range_events,
        m1_path=args.m1,
        event_accounting_path=args.event_accounting,
        duration_s=args.c1_duration_s,
    )
    protected_after = verify_protected_baseline(fusion, args.protected_baseline)
    root_r4_after = root_r4_verification(args.root_r4, EXPECTED_ROOT_R4_SHA256)
    git_end = git_snapshot(args.git_root, "BioSpur_Fusion/Fusion_Part")
    complete = (
        path_gate["checks"]["pass"] and storage["pass"] and protected_before["pass"]
        and protected_after["pass"] and root_r4_before["manifest_exact"]
        and root_r4_after["manifest_exact"]
        and root_r4_before["tree"] == root_r4_after["tree"]
        and synthetic["pass"] and pytest_result["pass"] and c1_summary["pass"]
    )
    label = ("WHOLE_BODY_ARTICULATED_SHADOW_SKELETON_VERIFIED" if complete
             else "PARTIAL_WHOLE_BODY_SKELETON_WITH_EXPLICIT_BLOCKERS")
    source_roots = (
        fusion / "src/biospur_fusion/root_r6a0",
        fusion / "tests/root_r6a0",
        fusion / "config/root_r6a0",
    )
    inventory = file_inventory(source_roots, fusion)
    inventory.extend({"path": f"logs/{output.name}/{name}", "role": "formal_or_verification_artifact"}
                     for name in (*REQUIRED_ARTIFACTS, "C1_WIRING_REHEARSAL.json",
                                  "PROTECTED_TREE_VERIFICATION.json", "FORMAL_ARTIFACT_SHA256.json", "SHA256SUMS"))
    completed = datetime.now(timezone.utc).isoformat()
    context = {
        "fusion_root": str(fusion),
        "model": model,
        "scenario": scenario,
        "model_identity_provenance": model.identity_provenance,
        "synthetic_gates": synthetic,
        "pytest": pytest_result,
        "c1_summary": c1_summary,
        "c1_graph": c1_graph,
        "path_gate": path_gate,
        "disk_gate": storage,
        "git_start": git_start,
        "git_end": git_end,
        "protected_before": protected_before,
        "protected_after": protected_after,
        "root_r4_before": root_r4_before,
        "root_r4_after": root_r4_after,
        "root_r4_expected_sha256": EXPECTED_ROOT_R4_SHA256,
        "created_utc": created,
        "completed_utc": completed,
        "elapsed_seconds": time.monotonic() - start_monotonic,
        "reproduction_command": _reproduction_command(args),
        "file_inventory": inventory,
    }
    package = write_formal_artifacts(output, context, label)
    if not package["all_present"]:
        raise RuntimeError("formal artifact set incomplete")
    print(label, flush=True)
    print(f"FUSION_ROOT={fusion}", flush=True)
    print("WRITE_BOUNDARY_VIOLATIONS=[]", flush=True)
    print("REAL_FUSION_EXECUTED=false", flush=True)
    print("REAL_C1_STATE_UPDATED=false", flush=True)
    print("PRODUCTION_AUTHORIZED=false", flush=True)
    print("CALIBRATION_MODIFIED=false", flush=True)
    print("BAD_TAG_TRUTH_ESTABLISHED=false", flush=True)
    print("EXTERNAL_TRUTH_OPENED=false", flush=True)
    print("COMMIT_CREATED=false", flush=True)
    print("PUSH_PERFORMED=false", flush=True)
    print("MERGE_PERFORMED=false", flush=True)
    print(f"RUN_DIR={output}", flush=True)
    return 0 if complete else 2


def main() -> int:
    return run(_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
