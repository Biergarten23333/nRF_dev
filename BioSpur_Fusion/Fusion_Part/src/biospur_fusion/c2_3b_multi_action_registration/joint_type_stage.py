"""Bounded execution of the monitor-approved joint-type mechanism micro-stage."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import numpy as np

from .contracts import load_approved_contract
from .joint_type_contract import (
    JOINT_TYPE_GATE_SHA256,
    PREFIT_BOUNDARY,
    load_joint_type_contract,
    sha256_file,
)
from .joint_type_fixtures import (
    CountedOfficialState,
    OfficialStateCache,
    generate_jtf01,
    generate_official_control_and_center,
    run_jtf01,
    run_jtf02_jtf03,
    run_jtf04,
    run_jtf05,
    run_jtf06,
    run_rtp01,
    write_imt_input,
)
from .joint_type_runtime import PinnedImtRuntime, RuntimeBound
from .synthetic_axis_stage import _load


SOURCE_PATHS = (
    "src/biospur_fusion/c2_3b_multi_action_registration/joint_type_contract.py",
    "src/biospur_fusion/c2_3b_multi_action_registration/joint_type_runtime.py",
    "src/biospur_fusion/c2_3b_multi_action_registration/joint_type_fixtures.py",
    "src/biospur_fusion/c2_3b_multi_action_registration/joint_type_stage.py",
    "src/biospur_fusion/c2_3b_multi_action_registration/__main__.py",
    "tests/test_c2_3b_joint_type_mechanisms.py",
)


def _write_text(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _tree_hashes(path: Path) -> dict[str, str]:
    return {
        str(item.relative_to(path)): sha256_file(item)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _disk_gate(repo_root: Path, transient_limit: int) -> dict[str, Any]:
    nrf = shutil.disk_usage("/mnt/nrf_ssd")
    root = shutil.disk_usage("/")
    result = {
        "nrf_ssd_free_bytes": nrf.free,
        "nrf_ssd_required_bytes": 100 * 1024**3,
        "root_free_bytes": root.free,
        "root_required_bytes": 40 * 1024**3,
        "projected_peak_transient_bytes": transient_limit,
        "project_growth_limit_bytes": 5 * 1024**3,
        "canonical_path": str(repo_root),
    }
    result["pass"] = bool(
        nrf.free >= result["nrf_ssd_required_bytes"]
        and root.free >= result["root_required_bytes"]
        and transient_limit <= result["project_growth_limit_bytes"]
        and repo_root == Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
    )
    return result


def _manifest(output_dir: Path, result: dict[str, Any]) -> None:
    paths = sorted(
        path for path in output_dir.rglob("*")
        if path.is_file() and path.name not in {"MANIFEST.json", "SHA256SUMS"}
    )
    manifest = {
        "schema": "biospur.c2_3b.multi_action.joint_type_attempt_manifest.v1",
        "phase": "TINY_MECHANISM_EXECUTION",
        "approved_gate_sha256": JOINT_TYPE_GATE_SHA256,
        "prefit_boundary": PREFIT_BOUNDARY,
        "mechanism_qualification_pass": result.get("mechanism_qualification_pass", False),
        "scientific_pass": False,
        "broad_synthetic_executed": False,
        "heading_executed": False,
        "opensense_executed": False,
        "real_c2_executed": False,
        "a_mutated": False,
        "uwb_consumed": False,
        "objects": [
            {"path": str(path.relative_to(output_dir)), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in paths
        ],
    }
    _write_json(output_dir / "MANIFEST.json", manifest)
    checksum_paths = sorted(path for path in output_dir.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    _write_text(
        output_dir / "SHA256SUMS",
        "".join(f"{sha256_file(path)}  ./{path.relative_to(output_dir)}\n" for path in checksum_paths),
    )


def _report(result: dict[str, Any]) -> str:
    rows = [
        "# C2 3B joint-type tiny mechanism checkpoint",
        "",
        f"Approved gate: `{result['approved_gate_sha256']}`",
        f"Mechanism fixtures all pass: `{str(result.get('mechanism_qualification_pass', False)).lower()}`",
        f"First failing fixture: `{result.get('first_failing_fixture')}`",
        f"Aggregate wall: `{result['aggregate_wall_s']:.6f} s` / `300 s`",
        "",
        "This attempt executes only the approved RTG00/RTP01/JTF01–JTF06 micro-stage. It does not run a mount fit, broad synthetic matrix, heading, OpenSense, real C2, A mutation, or UWB path.",
        "",
        "The prefit structural outcome remains",
        f"`{PREFIT_BOUNDARY}`.",
        "Passing tiny mechanisms cannot bypass that outcome; failures are causal mechanism evidence only and are not a terminal 3B verdict.",
    ]
    return "\n".join(rows) + "\n"


def run_joint_type_stage(repo_root: Path, output_dir: Path) -> dict[str, Any]:
    """Execute exactly the approved micro-stage and seal every observed outcome."""

    aggregate_started = time.monotonic()
    aggregate_deadline = aggregate_started + 300.0
    root = repo_root.resolve()
    output = output_dir.resolve()
    if output.exists():
        raise FileExistsError(output)
    contract = load_joint_type_contract(root)
    transient_limit = int(contract["runtime"]["disk"]["projected_peak_transient_bytes"])
    disk = _disk_gate(root, transient_limit)
    if not disk["pass"]:
        raise RuntimeBound("DISK_OR_CANONICAL_PATH_GATE")
    output.mkdir(parents=True, exist_ok=False)

    prior_paths = {
        "joint_type_revision_2": root / "logs/c2_3b_multi_action_registration_precode_joint_type_revision_20260902_223918",
        "r5_gate": root / "logs/c2_3b_multi_action_registration_precode_revision_20260902_210920",
        "axis_attempt_003": root / "logs/c2_3b_multi_action_registration_synthetic_20260902_204748/axis_attempt_003",
    }
    prior_before = {name: _tree_hashes(path) for name, path in prior_paths.items()}
    source_before = {relative: sha256_file(root / relative) for relative in SOURCE_PATHS}
    _write_json(output / "RUN_CONTRACT_BINDING.json", {
        "approved_gate_path": str(contract["gate"]),
        "approved_gate_sha256": contract["gate_sha256"],
        "argv": sys.argv,
        "python_executable": sys.executable,
        "python_dont_write_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"),
        "source_sha256_before": source_before,
        "disk_gate": disk,
        "aggregate_clock_started_before_guards": True,
        "aggregate_hard_limit_s": 300.0,
        "transient_disk_limit_bytes": transient_limit,
        "prefit_boundary": PREFIT_BOUNDARY,
        "broad_synthetic_authorized": False,
        "opensense_authorized": False,
        "real_c2_authorized": False,
        "uwb_consumed": False,
    })

    runtime: PinnedImtRuntime | None = None
    stages: dict[str, Any] = {}
    failure: str | None = None
    try:
        r2_gate = root / "logs/c2_3b_multi_action_registration_precode_joint_type_revision_20260902_223918"
        closure = _load(r2_gate / "IMT_RUNTIME_CLOSURE.json")
        canonical_import = _load(r2_gate / "precode_tests/PUBLIC_IMPORT_CLOSURE_ATTEMPT_001.json")
        runtime = PinnedImtRuntime(root, output, aggregate_started, aggregate_deadline, transient_limit)
        stages["RTG00"] = runtime.bootstrap(closure, canonical_import)
        _write_json(output / "RTG00_RUNTIME_AND_HASH_GUARD.json", stages["RTG00"])

        approved = load_approved_contract(root)
        r2 = root / "logs/c2_3b_multi_action_registration_precode_revision_20260902_180947"
        r2_synthetic = _load(r2 / "SYNTHETIC_FIXTURE_SPEC.json")
        r2_static = _load(r2 / "STATIC_REGISTRATION_AND_PHYSICAL_GATES.json")
        r2_mechanism = _load(r2 / "MECHANISM_AND_API_CONTRACT.json")
        knee_contract = _load(r2_gate / "KNEE_CONSTANT_POINT_CONTRACT.json")
        sensitivity = _load(r2_gate / "SEEL_IDENTIFIABILITY_AND_THRESHOLDS.json")
        mechanics = _load(
            root / "logs/c2_3b_multi_action_registration_precode_joint_type_20260902_220508/OFFICIAL_JOINT_MECHANICS.json"
        )
        import opensim as osim
        osim.Logger.setLevelString("error")
        osim.Logger.removeFileSink()
        osim.Logger.addFileSink(str(output / "opensim_joint_type.log"))
        model = osim.Model(str(approved["model_path"]))
        enabled = tuple(r2_synthetic["model_truth"]["enabled_coordinates"])
        owner = CountedOfficialState(osim, model, enabled)
        body_names = tuple(dict.fromkeys(r2_static["body_by_segment"].values()))
        cache = OfficialStateCache(
            owner,
            body_names,
            {
                "model_sha256": sha256_file(approved["model_path"]),
                "state_lifecycle_sha256": sha256_file(
                    root / "logs/c2_3b_multi_action_registration_precode_revision_20260902_210920/STATE_INITIALIZATION_BINDING_R5.json"
                ),
                "lock_default_map_sha256": sha256_file(
                    root / "logs/c2_3b_multi_action_registration_precode_revision_20260902_210920/STATE_INITIALIZATION_BINDING_R5.json"
                ),
            },
            aggregate_deadline,
        )

        jtf01_inputs, parameters, jtf01_metadata = generate_jtf01(
            cache, model, approved, r2_synthetic, r2_static, r2_mechanism,
            min(aggregate_deadline, time.monotonic() + 50.0),
        )
        jtf02_cache, jtf03_cache, jtf04_inputs, jtf04_truth = generate_official_control_and_center(
            cache, model, parameters, r2_synthetic, knee_contract,
            min(aggregate_deadline, time.monotonic() + 60.0),
        )
        if cache.assembly_calls != 2624 or cache.realize_position_calls != 2624:
            raise RuntimeBound(f"OFFICIAL_CALL_ACCOUNTING: {cache.assembly_calls}")
        if cache.cache_hits != 9:
            raise RuntimeBound(f"OFFICIAL_STATE_CACHE_REUSE: {cache.cache_hits}")
        runtime.sample_disk("official_trajectory_caches")

        full_input = runtime.temp / "jtf04_input.npz"
        prefix_input = runtime.temp / "rtp01_input.npz"
        write_imt_input(full_input, jtf04_inputs)
        write_imt_input(prefix_input, {key: value[:100] for key, value in jtf04_inputs.items()})
        runtime.sample_disk("imt_input_arrays")
        rtp_deadline = min(aggregate_deadline, time.monotonic() + 25.0)
        stages["RTP01"] = run_rtp01(runtime, prefix_input, rtp_deadline)
        _write_json(output / "RTP01_IMT_100_ROW_TIMING_PREFIX.json", stages["RTP01"])
        if not stages["RTP01"]["pass"]:
            raise RuntimeBound(stages["RTP01"]["first_gate"])

        prefix_call = stages["RTP01"]["call"]
        prefix_bfgs_wall = prefix_call["public_wall_s"] + sum(
            row["wall_s"] for row in prefix_call["comparators"]
        )
        prefix_process_overhead = max(0.0, prefix_call["subprocess_wall_s"] - prefix_bfgs_wall)
        projected_jtf04_imt = prefix_bfgs_wall * (2 * 1001 / 100) + 2 * prefix_process_overhead
        actual_official_wall = (
            jtf01_metadata["generation_wall_s"]
            + jtf02_cache["generation_wall_s"]
            + jtf03_cache["generation_wall_s"]
            + float(jtf04_truth["generation_wall_s"])
        )
        projected_total = (
            stages["RTG00"]["wall_s"]["stage"] + actual_official_wall
            + stages["RTP01"]["wall_s"] + projected_jtf04_imt + 40.0 + 10.0
        )
        projection = {
            "dimensions": {
                "official_unique_rows": 2624,
                "qmt_public_calls": 1,
                "qmt_internal_branches": 2,
                "imt_public_calls": 3,
                "total_bfgs_calls": 18,
            },
            "measured_prefix_bfgs_wall_s": prefix_bfgs_wall,
            "measured_prefix_process_overhead_s": prefix_process_overhead,
            "measured_official_cache_wall_s": actual_official_wall,
            "predicted_jtf04_imt_wall_s": projected_jtf04_imt,
            "qmt_contract_projection_s": 40.0,
            "other_fixture_allowance_s": 10.0,
            "predicted_aggregate_wall_s": projected_total,
            "prelaunch_projection_limit_s": 240.0,
            "scaling_assumption": "official caches measured once; IMT BFGS cost linear in rows and exact call ratio; fixed contract QMT allowance",
            "pass": projected_total <= 240.0,
        }
        _write_json(output / "PRELAUNCH_RUNTIME_PROJECTION.json", projection)
        if not projection["pass"]:
            raise RuntimeBound("FAILED_RUNTIME_PROJECTION_OR_WALL")

        stages["JTF01"] = run_jtf01(
            jtf01_inputs, parameters, jtf01_metadata, r2_static,
            min(aggregate_deadline, time.monotonic() + max(0.0, 50.0 - jtf01_metadata["generation_wall_s"])),
        )
        _write_json(output / "JTF01_PIN_ELBOW_LEFT.json", stages["JTF01"])

        stages["JTF02"], stages["JTF03"] = run_jtf02_jtf03(
            jtf02_cache, jtf03_cache, mechanics,
        )
        _write_json(output / "JTF02_BACK_CUSTOMJOINT_3DOF.json", stages["JTF02"])
        _write_json(output / "JTF03_WALKER_KNEE_LEFT_CONSTANT_POINT.json", stages["JTF03"])

        stages["JTF04"] = run_jtf04(
            runtime, full_input, jtf04_truth, sensitivity,
            min(aggregate_deadline, time.monotonic() + max(0.0, 60.0 - float(jtf04_truth["generation_wall_s"]))),
        )
        _write_json(output / "JTF04_SEEL_FIXED_CENTER_POSITIVE.json", stages["JTF04"])
        stages["JTF05"] = run_jtf05()
        _write_json(output / "JTF05_SEEL_STATIONARY_DEGENERACY.json", stages["JTF05"])
        stages["JTF06"] = run_jtf06()
        _write_json(output / "JTF06_SEEL_IDENTICAL_PAIR_NULLSPACE.json", stages["JTF06"])
    except (RuntimeBound, ValueError, AssertionError, TimeoutError) as error:
        failure = f"{type(error).__name__}: {error}"
    finally:
        disk_samples = [] if runtime is None else list(runtime.disk_samples)
        if runtime is not None:
            runtime.close()

    aggregate_wall = time.monotonic() - aggregate_started
    test_env = os.environ.copy()
    test_env["PYTHONDONTWRITEBYTECODE"] = "1"
    focused = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            str(root / "tests/test_c2_3b_joint_type_mechanisms.py"),
        ],
        cwd=root,
        env=test_env,
        text=True,
        capture_output=True,
        timeout=max(1.0, aggregate_deadline - time.monotonic()),
        check=False,
    )
    _write_text(
        output / "FOCUSED_TESTS.txt",
        "command=" + " ".join(focused.args) + "\n"
        + f"exit_status={focused.returncode}\n"
        + "stdout:\n" + focused.stdout
        + "stderr:\n" + focused.stderr,
    )
    aggregate_wall = time.monotonic() - aggregate_started
    source_after = {relative: sha256_file(root / relative) for relative in SOURCE_PATHS}
    prior_after = {name: _tree_hashes(path) for name, path in prior_paths.items()}
    required_stage_ids = ("RTG00", "RTP01", "JTF01", "JTF02", "JTF03", "JTF04", "JTF05", "JTF06")
    completed = tuple(stages) == required_stage_ids
    first_failing = next(
        (name for name in required_stage_ids if name in stages and not stages[name].get("pass", False)),
        None,
    )
    mechanism_pass = bool(
        completed
        and all(stages[name]["pass"] for name in required_stage_ids)
        and aggregate_wall <= 300.0
        and source_before == source_after
        and prior_before == prior_after
        and focused.returncode == 0
    )
    result = {
        "schema": "biospur.c2_3b.multi_action.joint_type_micro_stage.v1",
        "approved_gate_sha256": contract["gate_sha256"],
        "completed_stage_ids": list(stages),
        "required_stage_ids": list(required_stage_ids),
        "all_required_stages_completed": completed,
        "stage_pass": {name: bool(row.get("pass", False)) for name, row in stages.items()},
        "mechanism_qualification_pass": mechanism_pass,
        "first_failing_fixture": first_failing,
        "execution_failure": failure,
        "aggregate_wall_s": aggregate_wall,
        "aggregate_wall_limit_s": 300.0,
        "focused_tests_exit_status": focused.returncode,
        "disk_samples": disk_samples,
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "source_unchanged_during_run": source_before == source_after,
        "prior_evidence_unchanged": prior_before == prior_after,
        "prefit_outcome": PREFIT_BOUNDARY,
        "terminal_3b_verdict_claimed": False,
        "scientific_pass": False,
        "broad_synthetic_executed": False,
        "mount_fit_executed": False,
        "heading_executed": False,
        "opensense_executed": False,
        "real_c2_executed": False,
        "a_mutated": False,
        "uwb_consumed": False,
    }
    _write_json(output / "STAGE_RESULT.json", result)
    _write_text(output / "CAUSAL_CHECKPOINT_REPORT.md", _report(result))
    _manifest(output, result)
    return result
