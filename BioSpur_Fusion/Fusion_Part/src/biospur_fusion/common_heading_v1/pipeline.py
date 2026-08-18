from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import time
from typing import Mapping

import numpy as np

from .analysis import build_candidate
from .core import atomic_json, canonical_json_bytes, sha256_file, sha256_payload, stable_seed
from .frontend_cache import build_frontend_cache
from .synthetic_oracle import run_independent_synthetic
from .validation import run_formal_validation
from .validator import compute_verdict, write_final_artifacts


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _benchmark_task(task_id: str, seed: int) -> tuple[str, str]:
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(24):
        a = rng.normal(size=(48, 12))
        matrix = a.T@a+np.eye(12)*1e-6
        values.append(np.linalg.eigvalsh(matrix))
    payload = np.asarray(values, dtype="<f8").tobytes(order="C")
    return task_id, hashlib.sha256(payload).hexdigest()


def _worker_benchmark(contract: Mapping) -> dict:
    tasks = [(f"synthetic-work-{i:03d}", stable_seed(int(contract["master_seed"]), f"worker-benchmark-{i:03d}")) for i in range(72)]
    runs = []
    canonical = None
    for workers in contract["worker_policy"]["benchmark_counts"]:
        start = time.perf_counter()
        with ProcessPoolExecutor(max_workers=int(workers)) as pool:
            values = list(pool.map(lambda_args_star, tasks))
        elapsed = time.perf_counter()-start
        values.sort()
        digest = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        canonical = digest if canonical is None else canonical
        runs.append({"workers": int(workers), "wall_seconds_noncanonical": elapsed,
                     "canonical_payload_sha256": digest, "canonical_identical": digest == canonical})
    identical = len({row["canonical_payload_sha256"] for row in runs}) == 1
    if not identical:
        chosen = 1
        reason = "worker-count canonical payload mismatch; frozen fallback to one worker"
    else:
        chosen = min(runs, key=lambda row: row["wall_seconds_noncanonical"])["workers"]
        reason = "fastest synthetic-only benchmark with byte-identical canonical payload"
    return {
        "schema":"biospur-phase3r23-worker-benchmark-v1", "workload":"synthetic representative eigensystems only",
        "real_fit_seen_for_selection":False, "validation_seen_for_selection":False, "h_seen_for_selection":False,
        "runs":runs, "cross_worker_canonical_identical":identical, "chosen_workers":chosen,
        "choice_reason":reason, "fallback_policy":[chosen,4,1] if chosen == 6 else [chosen,1],
        "blas_threads_per_worker":1,
    }


def lambda_args_star(args: tuple[str, int]) -> tuple[str, str]:
    return _benchmark_task(*args)


def _frame_contract(repo: Path) -> tuple[dict, str]:
    import qmt, vqf
    qmt_path = Path(inspect.getfile(qmt)).resolve()
    vqf_path = Path(inspect.getfile(vqf)).resolve()
    vqf_extension = next(vqf_path.parent.glob("vqf*.so"), None)
    dependency_root = Path("/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r-deps/phase3r_20260817T192852Z")
    qmt_source = dependency_root/"qmt/qmt/functions/joint_axis_est_hinge_olsson.py"
    vqf_source = dependency_root/"vqf/vqf/pyvqf.py"
    qmt_license = dependency_root/"qmt/LICENSES/LicenseRef-Unspecified.txt"
    vqf_license = dependency_root/"vqf/LICENSES/MIT.txt"
    payload = {
        "schema":"biospur-phase3r23-frame-common-heading-contract-v1",
        "Q_i": {"symbol":"R_EiI", "source":"sensor/IMU frame I", "target":"per-node VQF earth/reference frame E_i",
                "action":"active source-to-target", "quaternion_order":"wxyz", "composition":"R_GI=Rz(hbar_i) R_EiI",
                "six_d_yaw_gauge":"independent per-node global-left yaw"},
        "common_heading": {"Hbar_i":"R_GEi=Rz(hbar_i), left multiplication", "pelvis_hbar_rad":0.0,
                           "psi_GP":"R_GP=Rz(psi_GP), profiled nuisance", "session_static":"best fit over FIT, not t0"},
        "qmt": {"version":importlib.metadata.version("qmt"), "runtime_path":str(qmt_path),
                "upstream_commit":"0fa8d32eb461e14d78e9ddbd569664ea59bcea19",
                "joint_axis_source_sha256":sha256_file(qmt_source), "license_file_sha256":sha256_file(qmt_license),
                "jhat1":"sensor-1 local RP2 axis", "jhat2":"sensor-2 local RP2 axis",
                "headingCorrection_calls":0, "axis_physical_truth":"POSSIBLE_NOT_PROVEN",
                "commercial_license_conclusion":"NOT_MADE"},
        "vqf": {"version":importlib.metadata.version("vqf"), "runtime_path":str(vqf_path),
                "runtime_extension_sha256":sha256_file(vqf_extension) if vqf_extension else None,
                "upstream_commit":"86ba56bdd3158b9b05f9f9fe5596866ba326438c",
                "pyvqf_source_sha256":sha256_file(vqf_source), "license_file_sha256":sha256_file(vqf_license),
                "api":"VQF.updateBatchFullState()['quat6D']"},
        "algebra": [
            "u_i^G = Rz(hbar_i) R_EiI u_i^I",
            "d^G = Rz(psi_GP) d^P",
            "protocol axis-line residual = wrap_mod_pi(hbar_i + yaw(u_i^Ei) - psi_GP - yaw(d^P))",
            "hinge residual compares Rz(hbar_parent) R_EpIp a_parent^Ip and Rz(hbar_child) R_EcIc a_child^Ic in G",
        ],
        "forbidden_conflations": ["left heading is not a right sensor/segment extrinsic", "pelvis-fixed does not fix psi_GP",
                                   "axis line cross-product has two tangent dimensions, not three independent ranks"],
        "opensense_started":False,"phase4_started":False,
    }
    md = """# Frame and common-heading contract

`Q_i` is the official VQF scalar-first active rotation `R_EiI`: it maps an IMU-local vector into that node's own 6D earth/reference frame. The missing alignment is a world/reference-side transform, `R_GI = Rz(hbar_i) R_EiI`; it is not a fixed right sensor/segment extrinsic.

The operator protocol frame `P` is related by the nuisance `R_GP = Rz(psi_GP)`. Pelvis heading is fixed to zero only to define relative coordinates. That convention does not supply evidence for `psi_GP`, and a surviving `psi_GP` null therefore counts against nine-dimensional identifiability.

Official qmt hinge axes are retained as paired sensor-local RP2 nuisances with antipodal symmetry and cross-covariance. This stage does not call qmt heading correction, does not estimate full `R_IS`, and does not start OpenSense or Phase 4.
"""
    return payload, md


def _planned_graph(contract: Mapping, authority: Mapping, split: Mapping) -> dict:
    heading_actions = {row["action_id"]: row for row in authority["rows"] if row.get("heading_bearing")}
    templates = []
    for action, cycle_info in split["action_cycle_split"].items():
        if action not in heading_actions or cycle_info.get("kind") != "dynamic":
            continue
        for cycle, split_class in cycle_info["assignments"].items():
            row = heading_actions[action]
            templates.append({
                "action_id":action,"cycle_ordinal":int(cycle),"split_class":split_class,
                "classification":row["classification"],"segments":row["segments"],
                "target_frame":row["target_frame"],"geometry":row["geometry"],
                "residual_dimension":row["residual_dimension"],"heading_bearing":row["heading_bearing"],
                "numeric_acceptance":"PENDING_FROZEN_FORMULA_APPLICATION",
            })
    return {
        "schema":"biospur-phase3r23-prefit-heading-evidence-factor-graph-v1",
        "heading_nodes":contract["relative_heading_order"],"nuisance_nodes":["psi_GP","paired_qmt_RP2_axes","Tpose_local_long_axes"],
        "pelvis_node":"pelvis_fixed_convention","factor_templates":templates,
        "subtree_clusters": {name:{"segments":segments,"prefit_path_to_pelvis":[],"connected_to_psi_GP_only":True}
                             for name,segments in contract["subtrees"].items()},
        "structural_warning":"no authorized heading-bearing pelvis factor; psi_GP-to-pelvis edge absent",
        "uid_lineage_source":"HEADING_SPLIT_AND_UID_MANIFEST.per_action_node_class",
        "uncertainty_policy":"equal complete blocks; between-block robust dispersion with frozen 5 degree floor; qmt axes are paired RP2 nuisances",
    }


def _closure(repo: Path, report_dir: Path) -> dict:
    roots = [
        repo/"BioSpur_Fusion/Fusion_Part/src/biospur_fusion/common_heading_v1",
        repo/"BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3r23",
        repo/"BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r23",
        repo/"BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase3r23",
    ]
    files = []
    for root in roots:
        files.extend(path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    files.extend(report_dir/name for name in (
        "FRAME_AND_COMMON_HEADING_CONTRACT.md", "FRAME_AND_COMMON_HEADING_CONTRACT.json",
        "ACTION_SEMANTIC_AUTHORITY_TABLE.json", "HEADING_SPLIT_AND_UID_MANIFEST.json",
        "HEADING_EVIDENCE_FACTOR_GRAPH.json", "WORKER_BENCHMARK.json",
    ))
    rows = []
    for path in sorted(set(files)):
        relative = path.relative_to(repo).as_posix()
        rows.append({"path":relative,"mode":oct(path.stat().st_mode & 0o777),"size":path.stat().st_size,"sha256":sha256_file(path)})
    payload = {"schema":"biospur-phase3r23-scientific-closure-v1","files":rows,
               "source_closure_sha256":hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
               "frozen_before_real_axis_or_heading_fit":True,
               "validation_rules_frozen":True,"thresholds_frozen":True,"mode_rules_frozen":True}
    return payload


def cmd_prepare(args: argparse.Namespace) -> int:
    contract = _json(args.contract); authority = _json(args.authority)
    session = _json(args.session_manifest); mapping = _json(args.mapping)
    if args.frontend_root.exists():
        split = _json(args.frontend_root/"FRONTEND_AND_SPLIT_MANIFEST.json")
        if split.get("total_development_rows") != 1_522_793 or split.get("unique_uid_count") != 1_522_793 or split.get("uid_overlap") != 0:
            raise RuntimeError("existing frontend checkpoint failed UID reconciliation")
        if any(not (args.frontend_root/name.lower()/"CACHE_MANIFEST.json").is_file() for name in split["class_counts"]):
            raise RuntimeError("existing frontend checkpoint is incomplete")
    else:
        split = build_frontend_cache(cache_root=args.cache_root, output_root=args.frontend_root,
                                     contract=contract, session_manifest=session,
                                     actual_mapping={node:segment for segment,node in mapping.items()})
    args.report_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(args.report_dir/"HEADING_SPLIT_AND_UID_MANIFEST.json", split)
    frame, frame_md = _frame_contract(args.repo)
    atomic_json(args.report_dir/"FRAME_AND_COMMON_HEADING_CONTRACT.json", frame)
    (args.report_dir/"FRAME_AND_COMMON_HEADING_CONTRACT.md").write_text(frame_md)
    authority_out = dict(authority)
    authority_out["source_files"] = {"authority_config_sha256":sha256_file(args.authority),
                                     "capture_plan_sha256":sha256_file(args.capture_plan),
                                     "execution_table_sha256":sha256_file(args.execution_table),
                                     "facing_sha256":sha256_file(args.facing)}
    authority_out["heading_bearing_rows"] = sum(bool(row.get("heading_bearing")) for row in authority["rows"])
    authority_out["target_in_G_without_psi_count"] = 0
    atomic_json(args.report_dir/"ACTION_SEMANTIC_AUTHORITY_TABLE.json", authority_out)
    atomic_json(args.report_dir/"HEADING_EVIDENCE_FACTOR_GRAPH.json", _planned_graph(contract, authority, split))
    benchmark = _worker_benchmark(contract)
    atomic_json(args.report_dir/"WORKER_BENCHMARK.json", benchmark)
    closure = _closure(args.repo, args.report_dir)
    atomic_json(args.report_dir/"SCIENTIFIC_CLOSURE_MANIFEST.json", closure)
    atomic_json(args.state_root/"CHECKPOINT_006_SCIENTIFIC_FREEZE.json", {
        "schema":"biospur-phase3r23-checkpoint-v1","stage":"SCIENTIFIC_CLOSURE_FROZEN",
        "source_closure_sha256":closure["source_closure_sha256"],
        "split_manifest_sha256":sha256_file(args.report_dir/"HEADING_SPLIT_AND_UID_MANIFEST.json"),
        "worker_benchmark_sha256":sha256_file(args.report_dir/"WORKER_BENCHMARK.json"),
        "real_axis_fit_started":False,"real_heading_fit_started":False,"validation_factor_view_opened":False,
    })
    print(json.dumps({"rows":split["total_development_rows"],"closure":closure["source_closure_sha256"],
                      "workers":benchmark["chosen_workers"]},sort_keys=True))
    return 0


def cmd_synthetic(args: argparse.Namespace) -> int:
    contract = _json(args.contract)
    result = run_independent_synthetic(args.report_dir/"COMMON_HEADING_SYNTHETIC_AND_MUTATION_RESULT.json",
                                       int(contract["master_seed"]))
    if not result["synthetic_engineering_pass"]:
        raise RuntimeError("independent synthetic/mutation qualification failed")
    print(json.dumps({"pass":True,"sha256":sha256_file(args.report_dir/"COMMON_HEADING_SYNTHETIC_AND_MUTATION_RESULT.json")},sort_keys=True))
    return 0


def cmd_fit(args: argparse.Namespace) -> int:
    contract = _json(args.contract); authority = _json(args.authority)
    split = _json(args.report_dir/"HEADING_SPLIT_AND_UID_MANIFEST.json")
    closure = _json(args.report_dir/"SCIENTIFIC_CLOSURE_MANIFEST.json")
    synthetic_path = args.report_dir/"COMMON_HEADING_SYNTHETIC_AND_MUTATION_RESULT.json"
    candidate = build_candidate(report_dir=args.report_dir,evidence_dir=args.evidence_dir,
                                frontend_root=args.frontend_root,contract=contract,authority=authority,
                                split_manifest=split,source_closure_sha256=closure["source_closure_sha256"],
                                synthetic_result_sha256=sha256_file(synthetic_path))
    atomic_json(args.state_root/"CHECKPOINT_011_PREVALIDATION_CANDIDATE.json", {
        "schema":"biospur-phase3r23-checkpoint-v1","stage":"PREVALIDATION_CANDIDATE_FROZEN",
        "candidate_payload_sha256":candidate["candidate_payload_sha256"],
        "candidate_file_sha256":sha256_file(args.report_dir/"PREVALIDATION_SESSION_STATIC_HEADING_CANDIDATE.json"),
        "source_closure_sha256":closure["source_closure_sha256"],"validation_factor_view_opened":False,
    })
    print(json.dumps({"candidate":candidate["candidate_payload_sha256"],"modes":candidate["joint_mode_count"]},sort_keys=True))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    contract=_json(args.contract);authority=_json(args.authority)
    candidate=_json(args.report_dir/"PREVALIDATION_SESSION_STATIC_HEADING_CANDIDATE.json")
    axis=_json(args.report_dir/"AXIS_FIT_LINEAGE_AND_UNCERTAINTY.json")
    split=_json(args.report_dir/"HEADING_SPLIT_AND_UID_MANIFEST.json")
    information=_json(args.report_dir/"COMMON_HEADING_ACTUAL_INFORMATION_AUDIT.json")
    bootstrap=_json(args.report_dir/"COMMON_HEADING_BOOTSTRAP_REPORT.json")
    timing=_json(args.report_dir/"COMMON_HEADING_TIMING_SENSITIVITY.json")
    closure=_json(args.report_dir/"SCIENTIFIC_CLOSURE_MANIFEST.json")
    drift=run_formal_validation(frontend_root=args.frontend_root,report_dir=args.report_dir,
                                evidence_dir=args.evidence_dir,contract=contract,authority=authority,
                                candidate=candidate,axis_payload=axis,split_manifest=split,
                                exact_candidate_sha=args.exact_candidate_sha)
    result=compute_verdict(candidate=candidate,information=information,split=split,axis=axis,
                           bootstrap=bootstrap,timing=timing,drift=drift,contract=contract)
    benchmark=_json(args.report_dir/"WORKER_BENCHMARK.json")
    write_final_artifacts(report_dir=args.report_dir,result=result,candidate=candidate,
                          information=information,axis=axis,bootstrap=bootstrap,drift=drift,
                          exact_candidate_sha=args.exact_candidate_sha,
                          source_closure_sha256=closure["source_closure_sha256"],
                          test_count=args.test_count,worker_benchmark=benchmark)
    print(json.dumps({"verdict":result["verdict"],"ready":result["opensense_common_heading_prerequisite_ready"]},sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest="command",required=True)
    common=argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo",type=Path,required=True);common.add_argument("--contract",type=Path,required=True)
    common.add_argument("--authority",type=Path,required=True);common.add_argument("--report-dir",type=Path,required=True)
    common.add_argument("--frontend-root",type=Path,required=True);common.add_argument("--state-root",type=Path,required=True)
    common.add_argument("--evidence-dir",type=Path,required=True)
    q=sub.add_parser("prepare",parents=[common]);q.add_argument("--cache-root",type=Path,required=True)
    q.add_argument("--session-manifest",type=Path,required=True);q.add_argument("--mapping",type=Path,required=True)
    q.add_argument("--capture-plan",type=Path,required=True);q.add_argument("--execution-table",type=Path,required=True);q.add_argument("--facing",type=Path,required=True)
    sub.add_parser("synthetic",parents=[common]);sub.add_parser("fit",parents=[common])
    q=sub.add_parser("validate",parents=[common]);q.add_argument("--exact-candidate-sha",required=True);q.add_argument("--test-count",type=int,required=True)
    return p


def main(argv: list[str] | None=None) -> int:
    args=parser().parse_args(argv)
    return {"prepare":cmd_prepare,"synthetic":cmd_synthetic,"fit":cmd_fit,"validate":cmd_validate}[args.command](args)
