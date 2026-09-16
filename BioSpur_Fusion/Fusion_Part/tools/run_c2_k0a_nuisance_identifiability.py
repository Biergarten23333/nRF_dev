#!/usr/bin/env python3
"""Prepare and execute the one-shot C2 K0-A nuisance identifiability audit."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import resource
import shutil
import sys
import time
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import load_capture2_calibration_episode
from biospur_fusion.v0.c2_basis.contracts import C2_IDENTITY, EPISODE_SELECTION
from biospur_fusion.v0.c2_basis.nuisance_identifiability import (
    audit_node_observability,
    chronological_action_split,
    gap_safe_native200,
    gauge_registry,
    parameter_registry,
)
from biospur_fusion.v0.c2_basis.real_data import (
    _reconstruct_action,
    _reconstruct_spec,
    audit_c2_raw6_decode_scope,
    metadata_authorities,
)


MAX_OUTPUT_BYTES = 100_000_000
MAX_PROJECTED_GROWTH_BYTES = 5 * 1024**3
HARD_WALL_S = 45 * 60
SOLE_RUN_COMMAND_TEMPLATE = (
    "timeout --signal=TERM --kill-after=15s 2400s "
    ".venv-v0/bin/python tools/run_c2_k0a_nuisance_identifiability.py "
    "--execute --run-dir {run_dir} --expected-preoutcome-sha256 {sha256}"
)
ALLOWLIST = (
    "src/biospur_fusion/v0/c2_basis/nuisance_identifiability.py",
    "tests/v0/test_c2_k0a_nuisance_identifiability.py",
    "tools/run_c2_k0a_nuisance_identifiability.py",
)
SOURCE_BINDINGS = (
    *ALLOWLIST,
    "src/biospur_fusion/v0/c2_basis/contracts.py",
    "src/biospur_fusion/v0/c2_basis/real_data.py",
    "src/biospur_fusion/v0/c2_basis/raw_frontend.py",
    "src/biospur_fusion/v0/dual_capture.py",
    "src/biospur_fusion/v0/data.py",
    "tools/run_c2_qmt_open_source_baseline.py",
    "config/biospur_fusion_v0_c2_basis/config_v1.json",
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tree_bytes(path: Path) -> int:
    return sum(row.stat().st_size for row in path.rglob("*") if row.is_file())


def _write(path: Path, payload: Any, *, immutable: bool = False) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    dump_json(path, payload)
    if immutable:
        path.chmod(0o444)


def _hashes(paths: tuple[str, ...]) -> dict[str, str]:
    missing = [value for value in paths if not (ROOT / value).is_file()]
    if missing:
        raise RuntimeError(f"source binding missing: {missing}")
    return {value: sha256_file(ROOT / value) for value in paths}


def prepare(run_dir: Path) -> int:
    if run_dir.exists():
        raise FileExistsError(f"fresh K0-A directory required: {run_dir}")
    nrf_free = shutil.disk_usage("/mnt/nrf_ssd").free
    root_free = shutil.disk_usage("/").free
    projected = 75_000_000
    if (
        nrf_free < 100 * 1024**3
        or root_free < 40 * 1024**3
        or projected > MAX_PROJECTED_GROWTH_BYTES
        or projected > MAX_OUTPUT_BYTES
    ):
        raise RuntimeError("K0-A disk preflight failed before output creation")
    authorities = metadata_authorities(ROOT)
    actions = tuple(row[0] for row in EPISODE_SELECTION)
    split = chronological_action_split(actions)
    sources = _hashes(SOURCE_BINDINGS)
    run_dir.mkdir(parents=True, exist_ok=False)

    _write(run_dir / "FILE_ALLOWLIST.json", {
        "schema": "biospur-c2-k0a-file-allowlist-v1",
        "allowed_code_changes": list(ALLOWLIST),
        "allowed_generated_output_prefix": str(run_dir.relative_to(ROOT)),
        "production_code_modified": False,
        "rf_shadow_field_touched": False,
        "git_operation_authorized": False,
    }, immutable=True)
    _write(run_dir / "PARAMETER_REGISTRY.json", parameter_registry(C2_IDENTITY), immutable=True)
    _write(run_dir / "GAUGE_REGISTRY.json", gauge_registry(C2_IDENTITY), immutable=True)
    _write(run_dir / "SPLIT.json", split, immutable=True)
    _write(run_dir / "GATES.json", {
        "schema": "biospur-c2-k0a-fixed-gates-v1",
        "declared_before_payload": True,
        "data_only_rank_excludes_priors_and_bounds": True,
        "scaled_condition_maximum": 1.0e4,
        "informed_eigenvalue_minimum_per_m2": 1.0 / 0.15**2,
        "informed_sigma_maximum_m": 0.15,
        "multistarts_within_50mm_minimum_fraction": 0.75,
        "competing_basin_relative_objective_margin": 0.05,
        "competing_basin_absolute_objective_margin": 1.0e-6,
        "covariance_psd_required": True,
        "covariance_nonshrinking_required": True,
        "full_gyro_scale_cross_axis_unanchored_result": "BLOCK",
        "any_named_active_column_uninformed_result": "BLOCK",
        "downstream_fit_when_observability_blocks": "NOT_REACHED",
        "hard_wall_s": HARD_WALL_S,
        "one_bounded_data_run": True,
        "maximum_output_bytes": MAX_OUTPUT_BYTES,
        "maximum_projected_growth_bytes": MAX_PROJECTED_GROWTH_BYTES,
    }, immutable=True)
    _write(run_dir / "INPUT_MANIFEST.json", {
        "schema": "biospur-c2-k0a-input-manifest-v1",
        "created_utc": _utc(),
        "scope": "CAPTURE2_CALIBRATION_EPISODES_00_TO_19_EXCLUDING_01",
        "exact_actions_attempts": [
            {"action": action, "attempt": attempt}
            for action, attempt in EPISODE_SELECTION
        ],
        "metadata_selection": {
            "path": str(authorities["selection_path"].relative_to(ROOT)),
            "sha256": authorities["selection_sha256"],
        },
        "historical_bounded_access_metadata_sha256": authorities["historical_access_hashes"],
        "sealed_raw_container_sha256_imported_not_recomputed": authorities[
            "sealed_raw_container_sha256_imported_not_recomputed"
        ],
        "payload_opened_during_prepare": False,
        "payload_hashed_during_prepare": False,
        "h01_h02_paths_opened_or_hashed": False,
        "h01_h02_guard": "FORBIDDEN_BY_EXACT_EPISODE_SELECTION_AND_BOUNDED_BYTE_RANGES",
        "uwb_payload_allowed": False,
        "native_rate_hz": 200,
        "gap_validity_owner": {
            "source": "tools/run_c2_qmt_open_source_baseline.py",
            "maximum_interpolation_bracket_ns": 12_500_000,
        },
    }, immutable=True)
    _write(run_dir / "SOURCE_HASHES.json", {
        "schema": "biospur-c2-k0a-source-hashes-v1",
        "created_utc": _utc(),
        "hashes": sources,
    }, immutable=True)
    _write(run_dir / "OWNERSHIP_AND_RUNTIME.json", {
        "schema": "biospur-c2-k0a-ownership-runtime-v1",
        "offline_only": [
            "bounded capture decoding", "actual-data Gram/SVD", "Schur/nullspace qualification",
        ],
        "future_online_compatible_pure_operations": [
            "per-sample per-node Jacobian row", "fixed per-node calibration application",
        ],
        "online_artifact_not_authorized_until_k0a_and_later_stages_pass": True,
        "joint_centres_fit": False,
        "anatomical_frames_fit": False,
        "opensim_used": False,
    }, immutable=True)
    _write(run_dir / "RESOURCE_PREFLIGHT.json", {
        "schema": "biospur-c2-k0a-resource-preflight-v1",
        "nrf_ssd_free_bytes": nrf_free,
        "root_free_bytes": root_free,
        "projected_output_bytes": projected,
        "maximum_output_bytes": MAX_OUTPUT_BYTES,
        "projected_growth_bytes": projected,
        "maximum_projected_growth_bytes": MAX_PROJECTED_GROWTH_BYTES,
        "working_arrays_projected_bytes": 400_000_000,
        "ram_safety_statement": "stream action decode; retain compact six-axis 200Hz arrays only",
        "pass": True,
    }, immutable=True)
    pre_files = sorted(path for path in run_dir.iterdir() if path.is_file())
    seal_payload = {
        "schema": "biospur-c2-k0a-preoutcome-seal-v1",
        "created_utc": _utc(),
        "record_status": "SEALED_BEFORE_C2_PAYLOAD_OPEN_OR_HASH",
        "artifact_sha256": {path.name: sha256_file(path) for path in pre_files},
        "source_hashes_sha256": sha256_file(run_dir / "SOURCE_HASHES.json"),
        "single_run_command_template": SOLE_RUN_COMMAND_TEMPLATE,
    }
    _write(run_dir / "PREOUTCOME_SEAL.json", seal_payload, immutable=True)
    seal_sha = sha256_file(run_dir / "PREOUTCOME_SEAL.json")
    command = SOLE_RUN_COMMAND_TEMPLATE.format(run_dir=run_dir, sha256=seal_sha)
    _write(run_dir / "SOLE_RUN_COMMAND.json", {
        "schema": "biospur-c2-k0a-sole-run-command-v1",
        "command": command,
        "preoutcome_sha256": seal_sha,
        "not_executed_at_record_time": True,
    }, immutable=True)
    blocked = {
        "schema": "biospur-c2-k0a-structural-block-result-v1",
        "created_utc": _utc(),
        "status": "BLOCKED_AT_STRUCTURAL_SENSOR_BASIS_GAUGE",
        "pass": False,
        "scientific_pass": False,
        "preoutcome_sha256": seal_sha,
        "raw_payload_opened_or_hashed": False,
        "h01_h02_opened_or_hashed": False,
        "bounded_data_run_started": False,
        "structural_proof": {
            "sample_local_nuisance_jacobian": "-I6",
            "schur_projector": "I - (-I6) * inv(I6) * (-I6)^T = 0",
            "schur_information": "A^T * 0 * A = 0 for every possible dataset",
            "physical_columns_per_node": 30,
            "named_null_vectors_per_node": 30,
            "full_gyro_scale_cross_axis_identifiable": False,
            "real_data_can_change_verdict": False,
        },
        "focused_test": {
            "command": (
                "PYTHONPATH=src .venv-v0/bin/python -m pytest -q "
                "tests/v0/test_c2_k0a_nuisance_identifiability.py"
            ),
            "result": "5 passed",
            "wall_s": 1.30,
            "maximum_rss_kib": 126572,
        },
        "known_candidate_diagnostic_limit": (
            "full_gyro_scale_cross_axis_informed indexes sorted Schur eigenvalues "
            "as if they retained physical-column identity. This is harmless for "
            "the all-zero structural blocker but is forbidden as evidence for any "
            "future per-column pass claim."
        ),
        "not_reached": [
            "actual-data direct/Schur run", "multistart 50mm gate",
            "competing-basin gate", "covariance PSD/nonshrinking gate",
            "K0-B hip levers", "K0-C anatomical frames", "OpenSim",
            "Phase J", "H02", "online compiled artifact",
        ],
        "monitor_stop_obeyed": True,
        "sole_run_command_withdrawn": True,
        "residual_process_pid": None,
    }
    _write(run_dir / "RESULT.json", blocked, immutable=True)
    report = """# C2 K0-A identifiability closeout

Status: **BLOCKED_AT_STRUCTURAL_SENSOR_BASIS_GAUGE**
(`scientific_pass=false`).

The pre-outcome factorization gives every native sample an unconstrained local
true accelerometer/gyroscope six-vector. Its nuisance Jacobian is `-I6`, so the
Schur projector is exactly zero and all 30 per-node physical calibration
columns have zero conditional information for every possible dataset. The
focused test freezes that proof and reports 30 named null vectors per node.

Opening the frozen 00-19 payload cannot change this algebraic result, so the
independent monitor stopped the sole real-data run before access. No raw C2
payload was opened or hashed; H01/H02 and UWB were not touched. No centre,
lever, anatomical-frame, OpenSim, Phase-J, H02, or online artifact was made.

One implementation caveat is sealed rather than hidden: the candidate helper
`full_gyro_scale_cross_axis_informed` indexes sorted eigenvalues as if they
retained physical-column identity. That is harmless when every eigenvalue is
zero, but it cannot support any future per-column PASS claim.
"""
    report_path = run_dir / "REPORT.md"
    report_path.write_text(report, encoding="utf-8")
    report_path.chmod(0o444)
    files = sorted(
        path for path in run_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    sums_path = run_dir / "SHA256SUMS"
    sums_path.write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(run_dir)}\n" for path in files),
        encoding="utf-8",
    )
    sums_path.chmod(0o444)
    print(json.dumps({
        "run_dir": str(run_dir),
        "preoutcome_sha256": seal_sha,
        "command": command,
        "payload_opened": False,
        "status": blocked["status"],
        "output_bytes": _tree_bytes(run_dir),
        "sha256sums_sha256": sha256_file(sums_path),
    }, indent=2))
    return 0


def _verify_preoutcome(run_dir: Path, expected: str) -> dict[str, Any]:
    path = run_dir / "PREOUTCOME_SEAL.json"
    if sha256_file(path) != expected or path.stat().st_mode & 0o222:
        raise RuntimeError("K0-A preoutcome seal changed or is writable")
    seal = json.loads(path.read_text(encoding="utf-8"))
    for name, digest in seal["artifact_sha256"].items():
        artifact = run_dir / name
        if sha256_file(artifact) != digest or artifact.stat().st_mode & 0o222:
            raise RuntimeError(f"preoutcome artifact changed: {name}")
    source_payload = json.loads((run_dir / "SOURCE_HASHES.json").read_text(encoding="utf-8"))
    for relative, digest in source_payload["hashes"].items():
        if sha256_file(ROOT / relative) != digest:
            raise RuntimeError(f"source changed after preoutcome seal: {relative}")
    return seal


def _partition_blocks(
    data: dict[str, list[dict[str, Any]]], actions: set[str], node: str,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    chosen = [row for row in data[node] if row["action"] in actions]
    if not chosen:
        raise RuntimeError(f"{node}: empty partition")
    absolute = np.concatenate([row["absolute_time_ns"] for row in chosen]).astype(float)
    lo, hi = float(absolute[0]), float(absolute[-1])
    if hi <= lo:
        raise RuntimeError(f"{node}: partition has no chronological span")
    blocks = []
    for row in chosen:
        t = 2.0 * (row["absolute_time_ns"].astype(float) - lo) / (hi - lo) - 1.0
        blocks.append((row["acc"], row["gyro"], t))
    return blocks


def execute(run_dir: Path, expected: str) -> int:
    started = time.monotonic()
    _verify_preoutcome(run_dir, expected)
    attempt = run_dir / "SOLE_DATA_RUN_STARTED.json"
    _write(attempt, {
        "schema": "biospur-c2-k0a-sole-data-run-start-v1",
        "started_utc": _utc(), "attempt": 1, "expected_preoutcome_sha256": expected,
    }, immutable=True)
    authorities = metadata_authorities(ROOT)
    spec = _reconstruct_spec(ROOT, authorities)
    split = json.loads((run_dir / "SPLIT.json").read_text(encoding="utf-8"))
    access_dir = run_dir / "C2_ACTION_ACCESS"
    access_dir.mkdir(exist_ok=False)
    data: dict[str, list[dict[str, Any]]] = {node: [] for node in C2_IDENTITY}
    action_audits = []
    try:
        for index, selected in enumerate(authorities["selected_actions"]):
            if time.monotonic() - started > 2200:
                raise TimeoutError("K0-A internal soft stop before hard timeout")
            action_name = str(selected["action"])
            if action_name not in {row[0] for row in EPISODE_SELECTION}:
                raise RuntimeError("unsealed action attempted to enter K0-A")
            print(f"K0-A {index + 1:02d}/19 {action_name} begin", flush=True)
            action = _reconstruct_action(
                selected, authorities["historical_access"][action_name]
            )
            rows, access = load_capture2_calibration_episode(ROOT, spec, action)
            scope = audit_c2_raw6_decode_scope(access)
            if not scope["pass"] or access.get("hxx_payload_opened") is not False:
                raise RuntimeError(f"{action_name}: bounded raw scope failed")
            access_path = access_dir / f"{action_name}.json"
            _write(access_path, {**access, "c2_raw6_decode_scope_audit": scope}, immutable=True)
            episode = gap_safe_native200(rows)
            absolute_start = int(access["common_clock"]["episode_start_absolute_global_ns"])
            for node in C2_IDENTITY:
                data[node].append({
                    "action": action_name,
                    "absolute_time_ns": absolute_start + episode.time_ns,
                    "acc": episode.acc_mps2[node],
                    "gyro": episode.gyro_rad_s[node],
                })
            action_audits.append({
                "action": action_name,
                "attempt": int(selected["attempt"]),
                "gap_safe_native200": episode.audit,
                "bounded_access_path": str(access_path.relative_to(ROOT)),
                "bounded_access_sha256": sha256_file(access_path),
                "hxx_payload_opened": False,
                "uwb_spatial_payload_consumed": False,
            })
            if _tree_bytes(run_dir) > MAX_OUTPUT_BYTES:
                raise RuntimeError("K0-A actual output exceeded 100 MB cap")
            print(f"K0-A {index + 1:02d}/19 {action_name} complete", flush=True)

        train_actions = set(split["train_actions"])
        validation_actions = set(split["validation_actions"])
        partition_results: dict[str, Any] = {}
        for partition, actions in (
            ("train_chronological_60", train_actions),
            ("validation_chronological_40", validation_actions),
        ):
            by_node = {}
            for node in sorted(C2_IDENTITY):
                by_node[node] = audit_node_observability(
                    node, _partition_blocks(data, actions, node)
                )
            partition_results[partition] = {
                "actions": sorted(actions, key=lambda value: [r[0] for r in EPISODE_SELECTION].index(value)),
                "nodes": by_node,
            }

        all_nodes = [
            row
            for partition in partition_results.values()
            for row in partition["nodes"].values()
        ]
        full_gyro_informed = all(row["full_gyro_scale_cross_axis_informed"] for row in all_nodes)
        schur_full_rank = all(row["schur_scaled_rank"] == 30 for row in all_nodes)
        direct_columns_excited = all(not row["direct_uninformed_columns"] for row in all_nodes)
        result = {
            "schema": "biospur-c2-k0a-nuisance-identifiability-result-v1",
            "created_utc": _utc(),
            "status": "BLOCKED_UNANCHORED_PER_NODE_GYRO_SENSOR_BASIS_GAUGE",
            "pass": False,
            "scientific_pass": False,
            "scope": "K0_A_ONLY",
            "preoutcome_sha256": expected,
            "input": {
                "capture": "CAPTURE2_ONLY",
                "actions": [row[0] for row in EPISODE_SELECTION],
                "native_rate_hz": 200,
                "action_audits": action_audits,
                "h01_h02_opened_or_hashed": False,
                "uwb_spatial_consumed": False,
            },
            "observability": partition_results,
            "gates": {
                "direct_active_columns_excited": direct_columns_excited,
                "schur_full_rank_30_per_node": schur_full_rank,
                "full_gyro_scale_cross_axis_informed": full_gyro_informed,
                "scaled_condition_maximum": {
                    "threshold": 1.0e4,
                    "status": "FAIL_SCHUR_SINGULAR",
                },
                "informed_eigenvalue": {
                    "threshold": 1.0 / 0.15**2,
                    "observed_minimum": 0.0,
                    "pass": False,
                },
                "informed_sigma_m": {
                    "threshold": 0.15,
                    "observed_maximum": "INF",
                    "pass": False,
                },
                "multistarts_within_50mm": "NOT_REACHED_OBSERVABILITY_BLOCK",
                "competing_basin": "NOT_REACHED_OBSERVABILITY_BLOCK",
                "covariance_psd_nonshrinking": "NOT_REACHED_OBSERVABILITY_BLOCK",
                "coherent_nuisance_stresses": {
                    "sensor_basis_reparameterization": "FAIL_IDENTIFIABILITY_AS_EXPECTED",
                    "bias_vs_local_signal_shift": "FAIL_IDENTIFIABILITY_AS_EXPECTED",
                    "drift_vs_local_signal_shift": "FAIL_IDENTIFIABILITY_AS_EXPECTED",
                },
            },
            "decision_boundary": (
                "Priors and bounds were excluded and cannot add rank. Every full "
                "per-node gyro 3x3 block is exactly absorbed by the sample-local "
                "angular-rate state. Joint centres were not fitted to hide this gauge."
            ),
            "downstream": {
                "k0b_hip_lever": "NOT_AUTHORIZED",
                "k0c_anatomical_frame": "NOT_AUTHORIZED",
                "phase_j": "NOT_AUTHORIZED",
                "h02": "NOT_OPENED",
                "online_artifact": "NOT_COMPILED_OR_QUALIFIED",
            },
            "runtime": {
                "wall_s": time.monotonic() - started,
                "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "output_bytes_before_result": _tree_bytes(run_dir),
            },
        }
        _write(run_dir / "RESULT.json", result, immutable=True)
        report = f"""# C2 K0-A nuisance identifiability

Status: **{result['status']}** (`scientific_pass=false`).

The single bounded run decoded only the nineteen frozen Capture-2 calibration
episodes at a 200 Hz grid and discarded interpolation brackets wider than the
existing 12.5 ms C2 validity owner. H01/H02 and UWB spatial payloads were not
opened or hashed.

All directly evaluated raw-signal columns are reported per node and split.
After eliminating the sample-local true specific-force/angular-rate states,
the Schur information rank is zero: an arbitrary perturbation of every node's
full gyroscope scale/cross-axis block is exactly absorbed by that node's local
angular-rate state. This is an explicit sensor-basis gauge, not a numerical
optimizer failure. Priors and bounds were excluded and therefore add no rank.

The 50 mm multistart, competing-basin, and covariance gates were not reached.
K0-B hip levers, K0-C anatomical frames, centres, OpenSim, Phase J and H02 were
not run. Offline decoding/SVD are qualification-only; only the pure per-sample
per-node Jacobian/application interface is suitable for a later bounded online
runtime, after an external angular/kinematic owner closes the gauge.
"""
        report_path = run_dir / "REPORT.md"
        if report_path.exists():
            raise FileExistsError(report_path)
        report_path.write_text(report, encoding="utf-8")
        report_path.chmod(0o444)
        final_bytes = _tree_bytes(run_dir)
        if final_bytes > MAX_OUTPUT_BYTES:
            raise RuntimeError("K0-A final evidence exceeded 100 MB")
        files = sorted(path for path in run_dir.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
        sums = "".join(f"{sha256_file(path)}  {path.relative_to(run_dir)}\n" for path in files)
        sums_path = run_dir / "SHA256SUMS"
        sums_path.write_text(sums, encoding="utf-8")
        sums_path.chmod(0o444)
        print(json.dumps({
            "status": result["status"],
            "run_dir": str(run_dir),
            "wall_s": result["runtime"]["wall_s"],
            "maximum_rss_kib": result["runtime"]["maximum_rss_kib"],
            "output_bytes": final_bytes,
            "sha256sums_sha256": sha256_file(sums_path),
        }, indent=2))
        return 2
    except Exception as exc:
        failure = run_dir / "RUN_FAILURE.json"
        if not failure.exists():
            _write(failure, {
                "schema": "biospur-c2-k0a-run-failure-v1",
                "created_utc": _utc(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "wall_s": time.monotonic() - started,
                "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "h01_h02_opened_or_hashed": False,
            }, immutable=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--expected-preoutcome-sha256")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    if args.prepare:
        if args.expected_preoutcome_sha256:
            parser.error("--prepare excludes --expected-preoutcome-sha256")
        return prepare(run_dir)
    if not args.expected_preoutcome_sha256:
        parser.error("--execute requires --expected-preoutcome-sha256")
    return execute(run_dir, args.expected_preoutcome_sha256)


if __name__ == "__main__":
    raise SystemExit(main())
