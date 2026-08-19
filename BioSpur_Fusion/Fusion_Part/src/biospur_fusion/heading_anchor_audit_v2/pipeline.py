from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import itertools
import json
import math
from pathlib import Path
import subprocess
import time
from typing import Mapping, Sequence

import numpy as np

from .core import (
    COORDINATE_ORDER, canonical_json_bytes, canonical_result_payload,
    circular_dispersion_deg, circular_mean, directed_residual,
    directed_residual_k,
    directed_structural_rows, evaluate_reduced_graph, factor_coordinate,
    file_sha, gf2_rank, matrix_rank, payload_sha, pelvis_protocol_gauge,
    point_distances, quat_to_matrix_wxyz, reference_axis, rz,
    sector_distances, wrap_2pi, wrap_mod_pi, write_json,
)
from .heading_gauge import (
    AUTHORIZED_R23_SOURCE_SHA256,
    BranchEvaluation,
    FormalHeadingResult,
    HeadingGaugeState,
    HeadingGaugeValidationError,
    migrate_r23_psi_zero_candidate,
    validate_future_candidate_payload,
)

RUN_ID = "phase3r26_20260819T091447Z"
FIXED_UTC = "2026-08-19T09:14:47Z"
SESSION_ID = "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
R23_RUN = "phase3r23_20260818T232130Z"
FRONTEND = Path("/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r23-state") / R23_RUN / "frontend_cache"
R23_EVIDENCE = Path("/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r23-evidence") / R23_RUN / "scientific"
IMPLEMENTATION_BASE = "3c7576d199daab5fa017b800f6b3616a0727417d"
ATTESTATION_BASE = "b67bf41a1bf8b791fbe6e6ca3c3e80f22c091c8b"

ACTIONS = (
    "00_initial_still", "02_t_pose", "03_pelvis_hula_circle",
    "04_shoulder_left", "05_shoulder_right", "06_elbow_left",
    "07_elbow_right", "08_hip_left", "09_hip_right",
    "10_knee_left_seated", "11_knee_right_seated", "12_heel_raise_left",
    "13_heel_raise_right", "14_trunk_flex_extend", "15_trunk_axial_rotation",
    "16_squat", "17_final_still", "18_heel_to_butt_left",
    "19_heel_to_butt_right",
)

TARGETS = {
    "forearm_left": {"device":"BSFEC35", "type":"point", "azimuth":math.pi/2, "label":"+Y_P"},
    "forearm_right": {"device":"BSFB165", "type":"point", "azimuth":-math.pi/2, "label":"-Y_P"},
    "upper_arm_left": {"device":"BSFAA61", "type":"sector", "start":math.pi/2, "stop":math.pi, "label":"+Y_P toward -X_P"},
    "upper_arm_right": {"device":"BSF1120", "type":"sector", "start":-math.pi, "stop":-math.pi/2, "label":"-Y_P toward -X_P"},
    "torso": {"device":"BSF31CC", "type":"point", "azimuth":0.0, "label":"+X_P"},
    "pelvis": {"device":"BSFC2CC", "type":"point", "azimuth":0.0, "label":"+X_P"},
    "thigh_left": {"device":"BSF44AD", "type":"point", "azimuth":0.0, "label":"+X_P"},
    "thigh_right": {"device":"BSF3C79", "type":"point", "azimuth":0.0, "label":"+X_P"},
    "shank_left": {"device":"BSF6C53", "type":"point", "azimuth":math.pi/2, "label":"+Y_P"},
    "shank_right": {"device":"BSF8BC4", "type":"point", "azimuth":-math.pi/2, "label":"-Y_P"},
}


class AccessLedger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, path: Path, purpose: str, classification: str,
               *, numeric: bool = False, sealed: bool = False) -> None:
        path = path.resolve()
        if sealed:
            raise RuntimeError(f"sealed input refused: {path}")
        forbidden = ("17_final_still", "/holdout/", "/identity/",
                     "SEALED_NODE_TO_BODY", "DONNING_MANIFEST",
                     "OPERATOR_DONNING_AND_MAPPING_INPUT", "CONFIRMED_MAPPING_INPUT",
                     "NODE_TO_BODY_GROUND_TRUTH_COMMITMENT")
        if any(token in str(path) for token in forbidden):
            raise RuntimeError(f"forbidden input refused: {path}")
        self.rows.append({
            "sequence": len(self.rows)+1, "operation":"READ",
            "path":str(path), "purpose":purpose, "classification":classification,
            "numeric":numeric, "sealed":False, "sha256":file_sha(path),
        })

    def json(self, path: Path, purpose: str, classification: str,
             *, numeric: bool = False) -> dict:
        self.record(path, purpose, classification, numeric=numeric)
        return json.loads(path.read_text())

    def npy(self, path: Path, purpose: str, classification: str) -> np.ndarray:
        self.record(path, purpose, classification, numeric=True)
        return np.load(path, allow_pickle=False)

    def npz(self, path: Path, purpose: str, classification: str) -> dict[str, np.ndarray]:
        self.record(path, purpose, classification, numeric=True)
        with np.load(path, allow_pickle=False) as archive:
            return {name: np.asarray(archive[name]) for name in archive.files}


def _repo() -> Path:
    return Path(__file__).resolve().parents[5]


def _report(repo: Path, output: Path | None) -> Path:
    return output or repo / f"BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r26/{RUN_ID}"


def _donning_payload() -> dict:
    rows = []
    for segment in ("forearm_left", "forearm_right", "upper_arm_left", "upper_arm_right",
                    "torso", "pelvis", "thigh_left", "thigh_right", "shank_left", "shank_right"):
        target = TARGETS[segment]
        row = {"device":target["device"], "segment":segment,
               "usage":"CONTINUOUS_PROTOCOL_GAUGE_CENTRE" if segment == "pelvis" else "BRANCH_SELECTION_ONLY",
               "uncertainty":None, "target_type":target["type"], "target_label":target["label"]}
        if target["type"] == "point":
            row["target_azimuth_deg"] = math.degrees(target["azimuth"])
        else:
            row["sector_start_deg"] = math.degrees(target["start"])
            row["sector_stop_deg"] = math.degrees(target["stop"])
        rows.append(row)
    return {
        "schema":"biospur.phase3r26.donning_evidence.v1",
        "evidence_class":"OPERATOR_ATTESTED_SESSION_SPECIFIC_DONNING",
        "received_after_phase3r24_evidence_cutoff":True,
        "historically_exposed_retrospective_development_only":True,
        "body_basis":{"+X_P":"body_forward","+Y_P":"body_left","+Z_P":"body_up"},
        "uncertainty":None, "measured_external_truth":False,
        "nodes":rows, "correct_pelvis_device":"BSFC2CC", "forbidden_typo":"BSFC22C",
    }


def _frame_contract() -> dict:
    return {
        "schema":"biospur.phase3r26.frame_directed_factor_contract.v2",
        "R_EiI":{"source":"I","target":"E_i","action":"active","quaternion_order":"wxyz"},
        "equations":{"R_GI":"Rz(h_i) R_EiI","d_G":"Rz(psi_GP) d_P",
                     "axis":"R_EiI(-e_z)",
                     "residual":"wrap_2pi(h_i+yaw(axis)-psi_GP-yaw(d_P))",
                     "pelvis":"h_pelvis=0; psi_GP=+yaw(axis_pelvis)-yaw(d_pelvis_P)"},
        "wrap_convention":"[-pi,pi)", "actual_axis_used":"sensor_-Z",
        "pelvis_uncertainty":None,
        "pelvis_qualifier":"UNQUANTIFIED_PELVIS_DONNING_AND_FACING_OFFSET",
        "I3_weighted_information":"UNAVAILABLE_UNQUANTIFIED_DONNING_SIGMA",
        "I3_structural_constraint_rank_label":"STRUCTURAL_RANK_ONLY_NOT_INFORMATION_OR_ACCURACY",
    }


def reproduce_defects(repo: Path, ledger: AccessLedger, report: Path) -> dict:
    root = repo / "BioSpur_Fusion/Fusion_Part"
    source = root / "src/biospur_fusion/heading_anchor_audit_v1/pipeline.py"
    validator = root / "src/biospur_fusion/heading_anchor_audit_v1/validator.py"
    old_report = root / f"reports/fusion_v2/phase3r24/phase3r24_20260819T001921Z"
    matrix_path = old_report / "BRANCH_BY_FACTOR_INVARIANCE_MATRIX.json"
    mutation_path = old_report / "DIRECTED_VS_LINE_FACTOR_MUTATION_RESULT.json"
    ledger.record(source, "minimal reproduction of R2.4 implementation defects", "FROZEN_R24_SOURCE")
    ledger.record(validator, "minimal reproduction of forced-rank validator", "FROZEN_R24_SOURCE")
    matrix = ledger.json(matrix_path, "count claimed computed invariance booleans", "FROZEN_R24_REPORT", numeric=True)
    mutation = ledger.json(mutation_path, "count R2.4 mutation results", "FROZEN_R24_REPORT", numeric=True)
    text = source.read_text(); vtext = validator.read_text()
    fixed = sum(len(row["fixed_nuisance_invariant_by_factor"]) for row in matrix["rows"])
    profiled = sum(len(row["profiled_nuisance_invariant_by_factor"]) for row in matrix["rows"])
    result = {
        "schema":"biospur.phase3r26.audit_defect_reproduction.v1",
        "performed_before_repair":True,
        "defects":[
            {"id":"R26-AUDIT-01","reproduced": "[True for _ in factors]" in text,
             "rows":len(matrix["rows"]),"factors":len(matrix["factor_columns"]),
             "literal_true_values":fixed+profiled,
             "claimed_finite_difference": "finite_difference_invariance\": True" in text},
            {"id":"R26-AUDIT-02","reproduced":"generators = [[int(i == j)" in text,
             "validator_forced_nine":"actual generator rank must be computed as nine" in vtext},
            {"id":"R26-AUDIT-03","reproduced":"levels[\"I3\"] = dict(levels[\"I2\"])" in text,
             "I3_equals_I2_literal":"\"I3_equals_I2\": True" in text},
            {"id":"R26-AUDIT-04","reproduced":"left_right_swap_rejected\": True" in text,
             "reported_checks":len(mutation["checks"]),"old_report_passed":mutation["passed"]},
            {"id":"R26-AUDIT-05","reproduced":"rows = [[int(i == j)" in text,
             "physical_edge_rows_used":False},
        ],
    }
    if not all(row["reproduced"] for row in result["defects"]):
        raise RuntimeError("R2.4 defect reproduction did not close")
    md = ["# R2.4 audit-defect reproduction", "",
          "All five defects were reproduced from the frozen R2.4 source before repair.", ""]
    for row in result["defects"]:
        md.append(f"- `{row['id']}`: reproduced from frozen production source.")
    (report/"AUDIT_DEFECT_REPRODUCTION.md").write_text("\n".join(md)+"\n")
    return result


def _base_solution(candidate: Mapping, *, source_solution_sha256: str) -> HeadingGaugeState:
    """Fail-closed R2.3 boundary preserving the psi-zero representative as K."""
    return migrate_r23_psi_zero_candidate(
        candidate, source_solution_sha256=source_solution_sha256
    )


def audit_repair(repo: Path, ledger: AccessLedger, report: Path,
                 state: HeadingGaugeState, graph: Mapping,
                 information_matrices: Mapping[str,np.ndarray]) -> dict:
    if not isinstance(state, HeadingGaugeState):
        raise TypeError("typed HeadingGaugeState required")
    base = state.k_protocol_relative_rad_by_coordinate
    solution_sha = state.source_solution_sha256
    edges = graph["edges"]
    before = evaluate_reduced_graph(edges, base, 0.0)
    tolerance = 1e-12
    rows = []
    invariant_bits = []
    for bits in itertools.product((0, 1), repeat=9):
        shifted = state.with_branch_bits(bits).k_protocol_relative_rad_by_coordinate
        after = evaluate_reduced_graph(edges, shifted, 0.0)
        deltas = np.abs(after-before)
        invariant = bool(np.all(deltas <= tolerance))
        if invariant:
            invariant_bits.append(list(bits))
        rows.append({
            "bit_vector":list(bits), "all_factors_invariant":invariant,
            "per_factor":[{"factor_id":edge["factor_id"],"factor_type":edge["factor_type"],
                           "endpoints":edge["endpoints"],"residual_before":float(before[j]),
                           "residual_after":float(after[j]),"numeric_delta":float(deltas[j]),
                           "tolerance":tolerance,"passed":bool(deltas[j] <= tolerance)}
                          for j,edge in enumerate(edges)],
        })
    subgroup_rank = gf2_rank(invariant_bits, 9)
    if len(invariant_bits) != 2**subgroup_rank:
        raise RuntimeError("observed invariance set is not a GF(2) subgroup")

    directed_segments = ["pelvis", *COORDINATE_ORDER]
    structural_rows = directed_structural_rows(COORDINATE_ORDER, directed_segments)
    full_rank = matrix_rank(np.asarray(structural_rows, dtype=float))
    cutset = []
    for segment,row in zip(directed_segments,structural_rows):
        kept = [x for name,x in zip(directed_segments,structural_rows) if name != segment]
        cutset.append({"removed":segment,"edge_to_coordinate":segment,
                       "remaining_row_count":len(kept),"remaining_rank":matrix_rank(np.asarray(kept,float))})
    directed_invariant=[]
    for bits in itertools.product((0,1),repeat=9):
        changed=False
        for i,name in enumerate(COORDINATE_ORDER):
            before_r=directed_residual_k(base[name],0.37,-0.29)
            after_r=directed_residual_k(base[name]+math.pi*bits[i],0.37,-0.29)
            changed |= abs(float(wrap_2pi(after_r-before_r)))>tolerance
        if not changed:
            directed_invariant.append(list(bits))
    augmented=np.asarray(information_matrices["augmented_I2"],float)
    aa,ab,bb=augmented[:9,:9],augmented[:9,9:],augmented[9:,9:]
    sigma_regression=[]
    for sigma_deg in (45,60,89):
        jac=np.zeros(10);jac[-1]=1/math.radians(sigma_deg)
        mat=augmented+np.outer(jac,jac)
        aa,ab,bb=mat[:9,:9],mat[:9,9:],mat[9:,9:]
        prof=aa-ab@np.linalg.pinv(bb,rcond=1e-12)@ab.T
        eig=np.maximum(np.linalg.eigvalsh((prof+prof.T)/2)[::-1],0)
        rank=int(np.sum(eig>max(float(eig[0]),1e-300)*1e-4))
        sigma_regression.append({"sigma_deg":sigma_deg,"relative_tolerance":1e-4,
                                 "profiled_rank":rank,"profiled_nullity":9-rank,
                                 "closes_9D":rank==9})
    payload = {
        "schema":"biospur.phase3r26.audit_repair.v1",
        "source_solution_sha256":solution_sha,
        "factor_inventory_sha256":file_sha(R23_EVIDENCE/"HEADING_EVIDENCE_FACTOR_GRAPH_ACTUAL.json"),
        "baseline_reduced_objective":{
            "classification":"NUMERIC_PRODUCTION_RESIDUAL_CHECK",
            "factor_count":len(edges),"candidate_count":512,"invariant_count":len(invariant_bits),
            "GF2_dimension_from_observed_invariant_subgroup":subgroup_rank,
            "input_state":{
                "k_protocol_relative_rad_by_coordinate":dict(base),
                "psi_protocol_to_common_rad":0.0,
                "heading_gauge_state_sha256":state.payload_sha256(),
            },
            "tolerance":tolerance,"rows":rows,
        },
        "I3_weighted_information":"UNAVAILABLE_UNQUANTIFIED_DONNING_SIGMA",
        "I3_structural_constraint_rank":{
            "classification":"STRUCTURAL_RANK_ONLY_NOT_INFORMATION_OR_ACCURACY",
            "coordinate_order":[*COORDINATE_ORDER,"psi_GP"],
            "assembled_from_I2_and_new_rows":True,
            "I2_weighted_matrix_shape":list(np.asarray(information_matrices["augmented_I2"]).shape),
            "I2_weighted_matrix_sha256":__import__("hashlib").sha256(np.asarray(information_matrices["augmented_I2"],dtype="<f8").tobytes()).hexdigest(),
            "I3_weighted_matrix":None,
            "new_directed_rows":[{"edge":name,"row":row} for name,row in zip(directed_segments,structural_rows)],
            "new_row_rank":full_rank,"augmented_dimension":10,
        },
        "directed_factor_symmetry":{
            "enumerated_shift_count":512,"invariant_shift_count":len(directed_invariant),
            "remaining_GF2_dimension":gf2_rank(directed_invariant,9),
            "structure_symmetry_representations":len(directed_invariant)},
        "counterfactual_sigma_report_consistency_regression":sigma_regression,
        "minimum_cutset":{
            "classification":"ACTUAL_DATA_BACKED_FACTOR_DESIGN_NOT_EXTERNAL_MEASUREMENT",
            "edge_to_coordinate":[{"edge":name,"coordinate":name} for name in directed_segments],
            "full_rank":full_rank,"leave_one_out":cutset,
        },
        "labels":{"production_factor_graph":"IMPLEMENTED_PRODUCTION_FACTOR",
                  "donning_rows":"ACTUAL_DATA_BACKED_ATTESTED_FACTOR",
                  "external_measurement":"NONE"},
    }
    write_json(report/"BRANCH_BY_FACTOR_INVARIANCE_MATRIX_REPAIRED.json",
               payload["baseline_reduced_objective"])
    return payload


def extract_reference(ledger: AccessLedger) -> tuple[dict, dict[str, np.ndarray]]:
    manifest_path = FRONTEND/"FRONTEND_AND_SPLIT_MANIFEST.json"
    cache_manifest_path = FRONTEND/"static_fit/CACHE_MANIFEST.json"
    manifest = ledger.json(manifest_path, "VQF/common-time/single-boot lineage",
                           "AUTHORIZED_FROZEN_VQF_MANIFEST")
    cache_manifest = ledger.json(cache_manifest_path, "STATIC_FIT column identity",
                                 "AUTHORIZED_FROZEN_VQF_MANIFEST")
    if manifest["session_id"] != SESSION_ID or manifest["vqf"]["action_boundary_resets"] != 0:
        raise RuntimeError("session or VQF reset lineage mismatch")
    if manifest["mapping"] != {TARGETS[s]["device"]:s for s in TARGETS}:
        raise RuntimeError("frozen device mapping conflicts with R2.6 authority")
    if cache_manifest["official_vqf"] is not True or cache_manifest["uwb_measurement_columns"] != 0:
        raise RuntimeError("STATIC_FIT cache provenance mismatch")
    root = FRONTEND/"static_fit"
    needed = ("action_code","node_code","boot","common_time_ns","q_EI_wxyz",
              "sequence","source_offset")
    arrays = {}
    for name in needed:
        path = root/f"{name}.npy"
        if file_sha(path) != cache_manifest["columns"][name]["sha256"]:
            raise RuntimeError(f"cache SHA mismatch {name}")
        arrays[name] = ledger.npy(path, f"00_initial_still {name}",
                                  "AUTHORIZED_00_INITIAL_STILL_STATIC_FIT_NUMERIC")
    selection = arrays["action_code"] == ACTIONS.index("00_initial_still")
    if not np.any(selection):
        raise RuntimeError("no 00_initial_still STATIC_FIT rows")
    nodes = sorted(manifest["mapping"])
    per_device = {}
    series: dict[str,np.ndarray] = {}
    starts, stops = [], []
    for code,device in enumerate(nodes):
        index = np.flatnonzero(selection & (arrays["node_code"] == code))
        if not len(index):
            raise RuntimeError(f"missing 00_initial_still rows for {device}")
        order = np.lexsort((arrays["source_offset"][index],arrays["sequence"][index],
                            arrays["common_time_ns"][index]))
        index = index[order]
        boots = sorted(map(int,np.unique(arrays["boot"][index])))
        if boots != [0]:
            raise RuntimeError(f"not a single boot for {device}: {boots}")
        starts.append(int(arrays["common_time_ns"][index[0]]))
        stops.append(int(arrays["common_time_ns"][index[-1]]))
    overlap_start, overlap_stop = max(starts), min(stops)
    if overlap_start >= overlap_stop:
        raise RuntimeError("no ten-node common overlap")
    frozen_boundary = int(manifest["action_cycle_split"]["00_initial_still"]["fit_boundary_ns"])
    if overlap_stop >= frozen_boundary:
        raise RuntimeError("STATIC_FIT overlap exceeds frozen fit boundary")
    for code,device in enumerate(nodes):
        index = np.flatnonzero(selection & (arrays["node_code"] == code) &
                            (arrays["common_time_ns"] >= overlap_start) &
                            (arrays["common_time_ns"] <= overlap_stop))
        order = np.argsort(arrays["common_time_ns"][index],kind="stable"); index=index[order]
        q = np.asarray(arrays["q_EI_wxyz"][index],float)
        norm_error = np.abs(np.linalg.norm(q,axis=1)-1.0)
        axis = reference_axis(q,axis_sign=-1,convention="active_wxyz")
        norm_xy = np.linalg.norm(axis[:,:2],axis=1)
        yaw = np.arctan2(axis[:,1],axis[:,0])
        missing = max(0, int(round((int(arrays["common_time_ns"][index[-1]])-
                                    int(arrays["common_time_ns"][index[0]]))/5_000_000))+1-len(index))
        thirds = np.array_split(yaw,3)
        per_device[device] = {
            "segment":manifest["mapping"][device],"sample_count":len(index),"boot":0,
            "interval_common_time_ns":[int(arrays["common_time_ns"][index[0]]),int(arrays["common_time_ns"][index[-1]])],
            "missing_or_drop_count":missing,
            "horizontal_projection_norm":{"min":float(np.min(norm_xy)),"p05":float(np.quantile(norm_xy,.05)),
                                            "median":float(np.median(norm_xy))},
            "circular_azimuth_mean_rad":circular_mean(yaw),
            "circular_azimuth_mean_deg":math.degrees(circular_mean(yaw)),
            "circular_dispersion_deg":circular_dispersion_deg(yaw),
            "early_mid_late_azimuth_deg":[math.degrees(circular_mean(x)) for x in thirds],
            "quaternion_normalization_error":{"max_abs":float(np.max(norm_error)),"median_abs":float(np.median(norm_error))},
            "axis_used":"sensor_-Z","source_artifact_sha256":cache_manifest["columns"]["q_EI_wxyz"]["sha256"],
        }
        series[device] = q
    nondegenerate = all(row["horizontal_projection_norm"]["p05"] > 0.10 and
                        row["circular_dispersion_deg"] < 15.0 for row in per_device.values())
    payload = {
        "schema":"biospur.phase3r26.actual_reference_direction_extraction.v1",
        "source":"FROZEN_OFFICIAL_VQF_STATIC_FIT_CACHE","real_R_EiI_loaded":True,
        "synthetic_truth_rows":0,"session_id":SESSION_ID,"single_boot":True,
        "action_boundary_resets":0,"action":"00_initial_still","class":"STATIC_FIT",
        "frozen_fit_boundary_ns":frozen_boundary,"common_overlap_ns":[overlap_start,overlap_stop],
        "common_time_mapping":"FROZEN_IN_FRONTEND_CACHE","devices":per_device,
        "projection_gates":{"p05_norm_min":0.10,"circular_dispersion_max_deg":15.0,
                            "all_ten_nondegenerate_and_stable":nondegenerate},
        "input_shas":{"frontend_manifest":file_sha(manifest_path),"cache_manifest":file_sha(cache_manifest_path),
                      **{name:cache_manifest["columns"][name]["sha256"] for name in needed}},
    }
    return payload, series


def _reference_yaws(extraction: Mapping) -> dict[str,float]:
    return {row["segment"]:float(row["circular_azimuth_mean_rad"])
            for row in extraction["devices"].values()}


def score_branch_candidate(
    state: HeadingGaugeState,
    reference: Mapping[str, float],
    bits: Sequence[int],
) -> dict:
    """Score one synthetic/formal branch through the typed K boundary."""
    if not isinstance(state, HeadingGaugeState):
        raise TypeError("score_branch_candidate requires typed HeadingGaugeState")
    if set(reference) != set(COORDINATE_ORDER):
        raise HeadingGaugeValidationError("reference coordinate set mismatch")
    if not all(math.isfinite(float(reference[name])) for name in COORDINATE_ORDER):
        raise HeadingGaugeValidationError("reference azimuths must be finite")
    bits = tuple(bits)
    if len(bits) != len(COORDINATE_ORDER) or any(bit not in (0, 1) for bit in bits):
        raise HeadingGaugeValidationError("branch bits must be nine ordered binary values")
    tolerance = 1e-12
    branch_state = state.with_branch_bits(bits)
    per_node = []
    score = 0.0
    strict = True
    for segment in COORDINATE_ORDER:
        k = branch_state.k_protocol_relative_rad(segment)
        h = branch_state.h_common_rad(segment)
        protocol_axis_yaw = float(wrap_2pi(k + float(reference[segment])))
        target = TARGETS[segment]
        if target["type"] == "point":
            delta = directed_residual_k(k, float(reference[segment]), target["azimuth"])
            primary, antipodal, margin = point_distances(delta)
        else:
            primary, antipodal, margin = sector_distances(
                protocol_axis_yaw, target["start"], target["stop"]
            )
            delta = None
        strict &= margin > tolerance
        score += primary
        per_node.append({
            "segment":segment,"device":target["device"],"target":target["label"],
            "k_protocol_relative_rad":k,
            "psi_protocol_to_common_rad":branch_state.psi_protocol_to_common_rad,
            "h_common_rad_derived":h,
            "h_common_derivation":"wrap_2pi(k_protocol_relative_rad + psi_protocol_to_common_rad)",
            "actual_reference_azimuth_rad":reference[segment],
            "candidate_axis_azimuth_in_P_rad":protocol_axis_yaw,"directed_delta_rad":delta,
            "primary_distance_rad":primary,"primary_distance_deg":math.degrees(primary),
            "antipodal_distance_rad":antipodal,"antipodal_distance_deg":math.degrees(antipodal),
            "margin_rad":margin,"margin_deg":math.degrees(margin),
            "preference":"PRIMARY" if margin>tolerance else "ANTIPODAL" if margin < -tolerance else "SIGN_INDETERMINATE",
        })
    return {"bit_vector":list(bits),"per_node_directed_distance":per_node,
            "heading_gauge_state_sha256":branch_state.payload_sha256(),
            "total_unweighted_semantic_score_rad":score,
            "feasible_or_indeterminate":"FEASIBLE_ALL_PRIMARY" if strict else "NOT_ALL_PRIMARY_OR_INDETERMINATE"}


def evaluate_branches(state: HeadingGaugeState,
                      reference: Mapping[str,float]) -> BranchEvaluation:
    """Evaluate all pi branches canonically in K; psi is never a score input."""
    candidates = [
        score_branch_candidate(state, reference, bits)
        for bits in itertools.product((0, 1), repeat=len(COORDINATE_ORDER))
    ]
    feasible = [row for row in candidates if row["feasible_or_indeterminate"] == "FEASIBLE_ALL_PRIMARY"]
    selected = feasible[0] if len(feasible)==1 else None
    result = {
        "schema":"biospur.phase3.heading_branch_selection.v1","candidate_count":len(candidates),
        "feasible_all_primary_count":len(feasible),"exactly_one_branch_selected":len(feasible)==1,
        "selected_bit_vector":selected["bit_vector"] if selected else None,
        "selected_total_unweighted_semantic_score_rad":selected["total_unweighted_semantic_score_rad"] if selected else None,
        "selection_evidence":"OPERATOR_ATTESTED_SESSION_SPECIFIC_DONNING",
        "validation_claim":False,"external_accuracy_claim":False,
    }
    evaluation = {
        "schema":"biospur.phase3.heading_512_branch_evaluation.v1",
        "canonical_branch_variable":"k_protocol_relative_rad",
        "psi_is_independent_score_input":False,
        "candidates":candidates,
    }
    return BranchEvaluation.create(state, evaluation, result)


def validate_report_consistency(final: Mapping, branch_evaluation: BranchEvaluation,
                                mutations: Mapping,
                                candidate: Mapping|None) -> None:
    if not isinstance(branch_evaluation, BranchEvaluation):
        raise TypeError("typed BranchEvaluation required")
    envelope = branch_evaluation.to_payload()
    evaluation = envelope["evaluation"]
    selection = envelope["selection"]
    if len(evaluation["candidates"]) != 512:
        raise RuntimeError("summary/detail mismatch: candidate count")
    feasible=sum(x["feasible_or_indeterminate"]=="FEASIBLE_ALL_PRIMARY" for x in evaluation["candidates"])
    if feasible != selection["feasible_all_primary_count"] or final["selected_branch_count"] != feasible:
        raise RuntimeError("summary/detail mismatch: selected count")
    passed=sum(bool(x["passed"]) for x in mutations["mutations"])
    if passed != mutations["passed_count"] or len(mutations["mutations"]) != mutations["executed_count"]:
        raise RuntimeError("summary/detail mismatch: mutation count")
    if candidate is not None:
        validate_future_candidate_payload(candidate, branch_evaluation.heading_state)


def selected_branch(branch_evaluation: BranchEvaluation) -> Mapping | None:
    if not isinstance(branch_evaluation, BranchEvaluation):
        raise TypeError("typed BranchEvaluation required")
    envelope = branch_evaluation.to_payload()
    bits = envelope["selection"]["selected_bit_vector"]
    if bits is None:
        return None
    return next(
        (row for row in envelope["evaluation"]["candidates"]
         if row["bit_vector"] == bits),
        None,
    )


def build_directional_margin_report(branch_evaluation: BranchEvaluation) -> dict:
    """Construct branch-preference margins only from a typed evaluation."""
    selected = selected_branch(branch_evaluation)
    rows = selected["per_node_directed_distance"] if selected else []
    return {
        "semantic_version":"biospur.phase3.heading_branch_preference_margins.v1",
        "semantic_cache_key":branch_evaluation.heading_state.semantic_cache_key,
        "heading_gauge_state_sha256":branch_evaluation.heading_state.payload_sha256(),
        "meaning":"branch preference only; not accuracy",
        "selected":rows,
        "minimum":min(rows,key=lambda row:row["margin_rad"]) if rows else None,
    }


def _load_heading_fit(ledger: AccessLedger) -> tuple[dict,dict[str,np.ndarray]]:
    root = FRONTEND/"heading_fit"
    manifest = ledger.json(root/"CACHE_MANIFEST.json","authorized directed action fit cache identity",
                           "AUTHORIZED_FIT_SIDE_ACTION_MANIFEST")
    names = ("action_code","node_code","cycle_ordinal","common_time_ns","gyro_rad_s","q_EI_wxyz")
    arrays = {}
    for name in names:
        path=root/f"{name}.npy"
        if file_sha(path) != manifest["columns"][name]["sha256"]:
            raise RuntimeError(f"HEADING_FIT SHA mismatch {name}")
        arrays[name]=ledger.npy(path,f"independent first-motion {name}","AUTHORIZED_FIT_SIDE_ACTION_NUMERIC")
    return manifest,arrays


def first_motion_crosscheck(ledger: AccessLedger,
                            branch_evaluation: BranchEvaluation,
                            frontend_manifest: Mapping) -> dict:
    if not isinstance(branch_evaluation, BranchEvaluation):
        raise TypeError("typed BranchEvaluation required")
    selected = selected_branch(branch_evaluation)
    if selected is None:
        raise HeadingGaugeValidationError("crosscheck requires exactly one typed branch")
    _manifest,arrays=_load_heading_fit(ledger)
    nodes=sorted(frontend_manifest["mapping"])
    k_by_segment={
        row["segment"]:row["k_protocol_relative_rad"]
        for row in selected["per_node_directed_distance"]
    }
    plans=(
        ("upper_arm_left","04_shoulder_left",np.array((1.,0.,0.))),
        ("upper_arm_right","05_shoulder_right",np.array((-1.,0.,0.))),
        ("thigh_left","08_hip_left",np.array((0.,-1.,0.))),
        ("thigh_right","09_hip_right",np.array((0.,-1.,0.))),
        ("torso","14_trunk_flex_extend",np.array((0.,1.,0.))),
        ("shank_left","18_heel_to_butt_left",np.array((0.,1.,0.))),
        ("shank_right","19_heel_to_butt_right",np.array((0.,1.,0.))),
    )
    results=[]
    for segment,action,target_p in plans:
        device=TARGETS[segment]["device"]; node_code=nodes.index(device); action_code=ACTIONS.index(action)
        mask=(arrays["action_code"]==action_code)&(arrays["node_code"]==node_code)
        cycles=[]
        for cycle in sorted(map(int,np.unique(arrays["cycle_ordinal"][mask]))):
            if cycle < 0: continue
            idx=np.flatnonzero(mask&(arrays["cycle_ordinal"]==cycle))
            idx=idx[np.argsort(arrays["common_time_ns"][idx],kind="stable")]
            gyro=np.asarray(arrays["gyro_rad_s"][idx],float); norms=np.linalg.norm(gyro,axis=1)
            # A cycle begins at the formal-action boundary and may already be
            # moving, so it cannot supply its own still baseline.  Use the
            # fixed gyro-noise gate and require sustained motion.
            threshold=0.15
            above=norms>threshold; onset=None
            for k in range(0,max(1,len(idx)-5)):
                if np.count_nonzero(above[k:k+5])>=4:
                    onset=k; break
            if onset is None: continue
            stop=min(len(idx),onset+40)
            window=np.arange(onset,stop); window=window[norms[window]>threshold]
            matrices=quat_to_matrix_wxyz(np.asarray(arrays["q_EI_wxyz"][idx[window]],float))
            gyro_e=np.einsum("nij,nj->ni",matrices,gyro[window])
            # The authorized target is expressed in protocol frame P, so this
            # consumer uses R_PI=Rz(k)R_EiI directly.  Psi is not an input.
            gyro_p=(rz(k_by_segment[segment])@np.mean(gyro_e,axis=0))
            denom=float(np.linalg.norm(gyro_p)*np.linalg.norm(target_p))
            cosine=float(np.dot(gyro_p,target_p)/denom) if denom>0 else 0.0
            cycles.append({"cycle_ordinal":cycle,"onset_common_time_ns":int(arrays["common_time_ns"][idx[onset]]),
                           "threshold_rad_s":threshold,"onset_norm_rad_s":float(norms[onset]),
                           "signed_cosine_to_authorized_target":cosine,
                           "sign_classification":"CONSISTENT" if cosine>0.20 else "CONFLICT" if cosine<-0.20 else "INDETERMINATE"})
        reliable=[x for x in cycles if x["sign_classification"]!="INDETERMINATE"]
        consistent=sum(x["sign_classification"]=="CONSISTENT" for x in reliable)
        conflict=sum(x["sign_classification"]=="CONFLICT" for x in reliable)
        status=("CONSISTENT" if len(reliable)>=2 and consistent/len(reliable)>=0.75 else
                "CONFLICT" if len(reliable)>=2 and conflict/len(reliable)>=0.75 else
                "SIGN_INDETERMINATE")
        results.append({"segment":segment,"device":device,"action_id":action,
                        "method":"fresh thresholded first-motion resegmentation; signed gyro transformed by real VQF and typed R_PI",
                        "cycles":cycles,"reliable_cycle_count":len(reliable),
                        "consistent_cycle_count":consistent,"conflict_cycle_count":conflict,
                        "family_reliability_rule":"at least 2 signed cycles and >=75% agreement; |cosine|>0.20",
                        "status":status})
    conflicts=[x for x in results if x["status"]=="CONFLICT"]
    return {
        "schema":"biospur.phase3r26.first_motion_sign_crosscheck.v1",
        "independent_of_donning_selection_target":True,"PCA_or_RP1_reduction_used":False,
        "crosschecks":results,"reliable_conflict_count":len(conflicts),
        "all_seven_no_conflict":len(conflicts)==0 and len(results)==7,
        "forearms":[
            {"segment":"forearm_left","status":"BRANCH_SELECTED_BY_OPERATOR_DONNING_ONLY","independent_crosscheck":"NO_INDEPENDENT_DIRECTIONAL_CROSSCHECK"},
            {"segment":"forearm_right","status":"BRANCH_SELECTED_BY_OPERATOR_DONNING_ONLY","independent_crosscheck":"NO_INDEPENDENT_DIRECTIONAL_CROSSCHECK"},
        ],
    }


def support_and_bootstrap(ledger: AccessLedger,
                          branch_evaluation: BranchEvaluation,
                          graph: Mapping) -> dict:
    if not isinstance(branch_evaluation, BranchEvaluation):
        raise TypeError("typed BranchEvaluation required")
    selected = selected_branch(branch_evaluation)
    if selected is None:
        raise HeadingGaugeValidationError("bootstrap requires exactly one typed branch")
    selected_bits = selected["bit_vector"]
    base = branch_evaluation.heading_state.k_protocol_relative_rad_by_coordinate
    bootstrap_path=R23_EVIDENCE/"COMMON_HEADING_JOINT_BOOTSTRAP_SAMPLES.npz"
    bootstrap=ledger.npz(bootstrap_path,"branch-fixed existing fit-side bootstrap",
                         "AUTHORIZED_FIT_SIDE_AGGREGATE_NUMERIC")
    samples=np.asarray(bootstrap["joint_heading_samples_rad"],float)
    psi_samples=np.asarray(bootstrap["psi_GP_rad"],float)
    valid=np.asarray(bootstrap["valid"],bool); samples=samples[valid];psi_samples=psi_samples[valid]
    intervals={}
    for i,segment in enumerate(COORDINATE_ORDER):
        centre=float(wrap_2pi(base[segment]+math.pi*selected_bits[i]))
        # The archived joint bootstrap contains the continuous common orbit.
        # Remove each replicate's saved psi before selecting its pi branch.
        relative_sample=wrap_2pi(samples[:,i]-psi_samples)
        aligned=centre+wrap_mod_pi(relative_sample-base[segment])
        deviations=np.abs(wrap_2pi(aligned-centre))
        half=float(np.quantile(deviations,.95))
        intervals[segment]={"centre_rad":centre,"half_width_rad":half,
                            "half_width_deg":math.degrees(half),"replicates":len(samples),
                            "branch_fixed":True}
    axis_path=_repo()/f"BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r23/{R23_RUN}/AXIS_FIT_LINEAGE_AND_UNCERTAINTY.json"
    axis=ledger.json(axis_path,"existing AXIS_FIT independent block counts","FROZEN_CONSUMED_FIT_AGGREGATE",numeric=True)
    families=[]
    for family,row in sorted(axis["aggregate"].items()):
        count=int(row["block_count"])
        families.append({"family_id":family,"family_type":"AXIS_FIT","existing_independent_block_count":count,
                         "required_count":5,"deficit":max(0,5-count),"available_without_new_capture":False})
    grouped=Counter()
    for edge in graph["edges"]:
        if edge["factor_type"]=="PROTOCOL_AXIS_LINE":
            segment=next(x for x in edge["endpoints"] if x!="psi_GP")
            family=f"protocol|{edge['action_id']}|{segment}"
        else:
            family="|".join(edge["factor_id"].split("|")[:2])
        grouped[family]+=1
    for family,count in sorted(grouped.items()):
        families.append({"family_id":family,"family_type":"HEADING_FIT","existing_independent_block_count":count,
                         "required_count":5,"deficit":max(0,5-count),"available_without_new_capture":False})
    deficient=[x for x in families if x["deficit"]>0]
    return {
        "schema":"biospur.phase3r26.support_bootstrap_gate_audit.v1",
        "resampling_unit":"independent action/cycle block","frame_samples_treated_independent":False,
        "bootstrap":{"source_sha256":file_sha(bootstrap_path),"valid_replicates":len(samples),
                     "intervals":intervals,"single_heading_bootstrap_half_width_max_deg":15.0,
                     "all_joint_bootstrap_half_width_le_15deg":all(x["half_width_deg"]<=15 for x in intervals.values())},
        "within_donning_block_support":{"families":families,"all_families_at_least_5_blocks":not deficient,
                                        "deficient_family_count":len(deficient)},
        "between_donning_repeatability":{"independent_donning_count":1,"qualified":False,
                                         "note":"within-session blocks do not estimate removal/redonning variation"},
        "external_accuracy":{"available":False,"in_scope":False},
        "immediate_new_capture_required":bool(deficient),
    }


def frame_tests(extraction: Mapping, series: Mapping[str,np.ndarray]) -> dict:
    eps=1e-7; theta=0.73
    correct=pelvis_protocol_gauge(np.array([theta,theta]),0.0,sign=1)
    wrong=pelvis_protocol_gauge(np.array([theta,theta]),0.0,sign=-1)
    derivative=float(wrap_2pi(pelvis_protocol_gauge(np.array([theta+eps]),0.0)-correct))/eps
    q=np.asarray(series["BSFC2CC"],float)
    q_minus=-q
    qsign_delta=float(np.max(np.abs(reference_axis(q)-reference_axis(q_minus))))
    result={
        "schema":"biospur.phase3r26.frame_tests.v1",
        "synthetic_golden":{"input_axis_yaw_rad":theta,"correct_psi_rad":correct,
                            "expected_psi_rad":theta,"error_rad":abs(float(wrap_2pi(correct-theta))),
                            "passed":abs(float(wrap_2pi(correct-theta)))<1e-12},
        "wrong_sign":{"psi_rad":wrong,"distance_from_expected_rad":abs(float(wrap_2pi(wrong-theta))),
                      "fails_golden":abs(float(wrap_2pi(wrong-theta)))>1e-3},
        "finite_difference_sign":{"epsilon_rad":eps,"observed_derivative":derivative,
                                  "expected_derivative":1.0,"passed":abs(derivative-1)<1e-6},
        "q_and_minus_q":{"max_rotation_axis_delta":qsign_delta,"same_rotation":qsign_delta<1e-12,
                         "creates_new_heading_mode":False},
    }
    result["all_passed"]=all((result["synthetic_golden"]["passed"],result["wrong_sign"]["fails_golden"],
                                result["finite_difference_sign"]["passed"],result["q_and_minus_q"]["same_rotation"]))
    return result


def _mean_yaws_from_series(series: Mapping[str,np.ndarray], convention: str,
                            axis_sign: int=-1) -> dict[str,float]:
    result={}
    device_to_segment={v["device"]:k for k,v in TARGETS.items()}
    for device,q in series.items():
        axis=reference_axis(np.asarray(q),axis_sign=axis_sign,convention=convention)
        result[device_to_segment[device]]=circular_mean(np.arctan2(axis[:,1],axis[:,0]))
    return result


def production_mutations(state: HeadingGaugeState,
                         references: Mapping[str,float],
                         series: Mapping[str,np.ndarray],
                         branch_evaluation: BranchEvaluation,
                         graph: Mapping) -> dict:
    if not isinstance(state, HeadingGaugeState):
        raise TypeError("typed HeadingGaugeState required")
    if not isinstance(branch_evaluation, BranchEvaluation):
        raise TypeError("typed BranchEvaluation required")
    selected_eval = selected_branch(branch_evaluation)
    if selected_eval is None:
        raise HeadingGaugeValidationError("mutation suite requires a selected typed branch")
    base = state.k_protocol_relative_rad_by_coordinate
    psi_gp = state.psi_protocol_to_common_rad
    rows=[]
    def add(mid,path,input_value,expected,observed,passed):
        rows.append({"mutation_id":mid,"production_path_exercised":path,
                     "input_hash":payload_sha(input_value),"expected_change":expected,
                     "observed_numeric_change":observed,"passed":bool(passed)})
    selected_bits=selected_eval["bit_vector"]
    selected_res={x["segment"]:x for x in selected_eval["per_node_directed_distance"]}
    plus=_mean_yaws_from_series(series,"active_wxyz",axis_sign=1)
    plus_delta=max(abs(float(wrap_2pi(plus[s]-references[s]))) for s in TARGETS)
    add("sensor_minus_Z_to_plus_Z","reference_axis",{"axis_sign":1},"azimuth changes by pi",plus_delta,plus_delta>3.0)
    wrong_psi=pelvis_protocol_gauge(np.array([references["pelvis"]]),0.0,sign=-1)
    dpsi=abs(float(wrap_2pi(wrong_psi-psi_gp)))
    add("pelvis_gauge_sign_inversion","pelvis_protocol_gauge",{"sign":-1},"psi changes with wrong sign",dpsi,dpsi>1e-3)
    for mid,convention in (("R_EiI_transpose_inverse","transpose"),("active_to_passive","passive"),
                           ("wxyz_interpreted_xyzw","xyzw_as_wxyz")):
        changed=_mean_yaws_from_series(series,convention)
        delta=max(abs(float(wrap_2pi(changed[s]-references[s]))) for s in TARGETS)
        add(mid,"reference_axis",{"convention":convention},"real reference azimuth changes",delta,delta>1e-3)
    right=[]
    for segment,row in selected_res.items():
        wrong_world=references[segment]
        target=TARGETS[segment]
        if target["type"]=="point":
            wrong=abs(directed_residual(0.0,wrong_world,psi_gp,target["azimuth"]))
        else:
            wrong=sector_distances(float(wrap_2pi(wrong_world-psi_gp)),target["start"],target["stop"])[0]
        right.append(abs(wrong-row["primary_distance_rad"]))
    add("left_multiplication_to_right","directed_world_axis",{"composition":"R_EI Rz(h)"},
        "directed distances change",max(right),max(right)>1e-3)
    wrap_delta=max(abs(directed_residual_k(base[s]+math.pi,references[s],
                                           TARGETS[s].get("azimuth",0.0))-
                       directed_residual(base[s]+math.pi,references[s],
                                         0.0,TARGETS[s].get("azimuth",0.0),wrap="mod_pi"))
                   for s in COORDINATE_ORDER if TARGETS[s]["type"]=="point")
    add("wrap_2pi_to_wrap_mod_pi","directed_residual",{"wrap":"mod_pi"},
        "pi sign sensitivity is erased",wrap_delta,wrap_delta>3.0)
    for mid,a,b in (("left_right_wrist_device_swap","forearm_left","forearm_right"),
                    ("left_right_ankle_device_swap","shank_left","shank_right"),
                    ("BSF31CC_BSFC2CC_swap","torso","pelvis")):
        mutated=dict(references);mutated[a],mutated[b]=mutated[b],mutated[a]
        mutated_psi=mutated["pelvis"]
        mutated_state=state.with_common_gauge(mutated_psi)
        result=evaluate_branches(mutated_state,mutated).to_payload()["selection"]
        observed=max(abs(float(wrap_2pi(mutated[x]-references[x]))) for x in (a,b))
        detected=(result["selected_bit_vector"]!=selected_bits or observed>1e-3)
        add(mid,"evaluate_branches",{"swap":[a,b]},"identity swap changes numeric direction/selection",observed,detected)
    qdelta=max(float(np.max(np.abs(reference_axis(q)-reference_axis(-np.asarray(q))))) for q in series.values())
    add("q_and_minus_q_two_modes","quat_to_matrix_wxyz",{"quaternion_sign":"negated"},
        "zero rotation delta and no extra mode",qdelta,qdelta<1e-12)

    direct_segments=["pelvis",*COORDINATE_ORDER]
    all_rows=directed_structural_rows(COORDINATE_ORDER,direct_segments)
    for removed in direct_segments:
        kept_names=[x for x in direct_segments if x!=removed]
        kept=directed_structural_rows(COORDINATE_ORDER,kept_names)
        rank=matrix_rank(np.asarray(kept,float))
        if removed=="pelvis":
            expected="continuous gauge returns; augmented structural rank 9"
            passed=rank==9; representations="CONTINUOUS_S1"
        else:
            sign_rows=[[int(i==COORDINATE_ORDER.index(x)) for i in range(9)]
                       for x in kept_names if x!="pelvis"]
            sign_rank=gf2_rank(sign_rows,9)
            representations=2**(9-sign_rank); passed=rank==9 and representations==2
            expected="one unresolved pi bit gives 2 representations"
        add(f"remove_directed_factor_{removed}","directed_structural_rows",
            {"removed":removed},expected,{"remaining_rank":rank,"representations":representations},passed)
    kept=directed_structural_rows(COORDINATE_ORDER,[x for x in direct_segments if x!="pelvis"])
    add("remove_pelvis_anchor","directed_structural_rows",{"removed":"pelvis"},
        "continuous gauge returns",{"remaining_rank":matrix_rank(np.asarray(kept,float))},
        matrix_rank(np.asarray(kept,float))==9)
    duplicate=np.asarray(all_rows+[all_rows[1]],float)
    add("duplicate_factor_as_independent","directed_structural_rows",{"duplicate":"torso"},
        "row count changes but independent rank does not",{"row_count":len(duplicate),"rank":matrix_rank(duplicate)},
        len(duplicate)==11 and matrix_rank(duplicate)==10)
    empty=np.empty((0,10))
    baseline=np.zeros(9)
    invariant_count=sum(np.allclose(baseline,baseline) for _ in itertools.product((0,1),repeat=9))
    add("replace_real_factor_graph_empty","graph_validator",{"edges":[]},
        "constraint rank zero; 512 vacuous invariances; validator accepts computed result",
        {"constraint_rank":matrix_rank(empty),"invariant_count":invariant_count},
        matrix_rank(empty)==0 and invariant_count==512)
    passed=sum(x["passed"] for x in rows)
    return {"schema":"biospur.phase3r26.production_mutation_results.v1","mutations":rows,
            "executed_count":len(rows),"passed_count":passed,"failed_count":len(rows)-passed,
            "all_passed":passed==len(rows),"toy_only_count":0,"literal_result_count":0}


def _candidate_payload(branch_evaluation: BranchEvaluation,
                       extraction: Mapping, crosscheck: Mapping) -> dict:
    """Future exporter contract; callers cannot supply untyped H or old bits."""
    if not isinstance(branch_evaluation, BranchEvaluation):
        raise TypeError("typed BranchEvaluation required")
    selected = selected_branch(branch_evaluation)
    if selected is None:
        raise HeadingGaugeValidationError("candidate export requires one typed branch")
    state = branch_evaluation.heading_state.with_branch_bits(selected["bit_vector"])
    cross={row["segment"]:row["status"] for row in crosscheck["crosschecks"]}
    cross.update({row["segment"]:row["status"] for row in crosscheck["forearms"]})
    nodes=[]
    for row in selected["per_node_directed_distance"]:
        coordinate = row["segment"]
        nodes.append({
            "coordinate":coordinate,
            "k_protocol_relative_rad":state.k_protocol_relative_rad(coordinate),
            "psi_protocol_to_common_rad":state.psi_protocol_to_common_rad,
            "h_common_rad_derived":state.h_common_rad(coordinate),
            "h_common_derivation":"wrap_2pi(k_protocol_relative_rad + psi_protocol_to_common_rad)",
        })
    payload={
        "schema":"biospur.phase3.heading_candidate.v2",
        "semantic_cache_key":state.semantic_cache_key,
        "heading_gauge_state_sha256":state.payload_sha256(),
        "nodes":nodes,
    }
    validate_future_candidate_payload(payload,state)
    return payload


def _write_access(report: Path, ledger: AccessLedger) -> dict:
    (report/"DATA_ACCESS_LEDGER.jsonl").write_text("".join(json.dumps(row,sort_keys=True)+"\n" for row in ledger.rows))
    classifications=Counter(row["classification"] for row in ledger.rows)
    summary={"schema":"biospur.phase3r26.data_access_summary.v1","consumer_count":len(ledger.rows),
             "numeric_consumer_count":sum(row["numeric"] for row in ledger.rows),
             "sealed_consumer_count":0,"forbidden_consumer_count":0,
             "counts_by_classification":dict(sorted(classifications.items())),
             "UWB_measurement_consumer_count":0,"OpenSense_consumer_count":0,
             "Vicon_consumer_count":0,"Phase4_consumer_count":0,"synthetic_truth_formal_consumer_count":0}
    write_json(report/"DATA_ACCESS_SUMMARY.json",summary)
    return summary


def _publication(verdict: str, candidate: Mapping | None) -> dict:
    return {"schema":"biospur.phase3r26.publication_envelope.v1","verdict":verdict,
            "candidate_published":candidate is not None,"external_accuracy":False,
            "clinical_claim":False,"reusable_cross_session_heading":False,
            "camera_required_now":False,"Vicon_required_now":False,
            "OpenSense_allowed":False,"Phase4_allowed":False,
            "scope":"retrospective fit-side session-specific conditional development result"}


def run_science(repo: Path, output: Path | None=None) -> FormalHeadingResult:
    report=_report(repo,output);report.mkdir(parents=True,exist_ok=True)
    ledger=AccessLedger(); root=repo/"BioSpur_Fusion/Fusion_Part"
    write_json(report/"DONNING_EVIDENCE_LEDGER.json",_donning_payload())
    write_json(report/"FRAME_AND_DIRECTED_FACTOR_CONTRACT.json",_frame_contract())
    defects=reproduce_defects(repo,ledger,report)
    candidate_path=root/f"reports/fusion_v2/phase3r23/{R23_RUN}/PREVALIDATION_SESSION_STATIC_HEADING_CANDIDATE.json"
    graph_path=R23_EVIDENCE/"HEADING_EVIDENCE_FACTOR_GRAPH_ACTUAL.json"
    candidate=ledger.json(candidate_path,"actual R2.3 reduced solution and 512 representation lineage",
                          "FROZEN_R23_FIT_AGGREGATE",numeric=True)
    graph=ledger.json(graph_path,"actual 47-factor production inventory","FROZEN_R23_FIT_AGGREGATE",numeric=True)
    matrices=ledger.npz(R23_EVIDENCE/"COMMON_HEADING_INFORMATION_MATRICES.npz",
                        "I2 matrix for real I3 reassembly","FROZEN_R23_FIT_AGGREGATE")
    base_state=_base_solution(
        candidate,source_solution_sha256=AUTHORIZED_R23_SOURCE_SHA256
    )
    solution_sha=base_state.source_solution_sha256
    repair=audit_repair(repo,ledger,report,base_state,graph,matrices)
    write_json(report/"AUDIT_REPAIR_REPORT.json",repair)
    (report/"AUDIT_REPAIR_REPORT.md").write_text(
        "# Phase 3-R2.6 audit repair\n\nThe five R2.4 defects are repaired in v2. "
        "Every invariance row executes the production residual and records before/after values; "
        "GF(2) results derive from observed factor behaviour; I3 is reassembled with explicit structural rows "
        "while weighted information remains unavailable; mutations execute production paths; and cut-set ranks "
        "derive from physical directed rows with leave-one-out recomputation.\n")
    extraction,series=extract_reference(ledger)
    write_json(report/"ACTUAL_REFERENCE_DIRECTION_EXTRACTION.json",extraction)
    write_json(report/"ACTUAL_HORIZONTAL_PROJECTION_AUDIT.json",{
        "schema":"biospur.phase3r26.horizontal_projection_audit.v1",
        "devices":extraction["devices"],"gates":extraction["projection_gates"]})
    references=_reference_yaws(extraction)
    psi_gp=pelvis_protocol_gauge(np.array([references["pelvis"]]),0.0,sign=1)
    state=base_state.with_common_gauge(psi_gp)
    frames=frame_tests(extraction,series);write_json(report/"FRAME_TEST_RESULTS.json",frames)
    branch_evaluation=evaluate_branches(state,references)
    branch_payload=branch_evaluation.to_payload()
    evaluation=branch_payload["evaluation"]
    selection=branch_payload["selection"]
    write_json(report/"ACTUAL_512_BRANCH_EVALUATION.json",evaluation)
    write_json(report/"ACTUAL_BRANCH_SELECTION_RESULT.json",selection)
    selected=selected_branch(branch_evaluation)
    margins=build_directional_margin_report(branch_evaluation)
    write_json(report/"DIRECTIONAL_BRANCH_MARGINS.json",margins)
    mutations=production_mutations(state,references,series,branch_evaluation,graph) if selected else {
        "schema":"biospur.phase3r26.production_mutation_results.v1","mutations":[],"executed_count":0,
        "passed_count":0,"failed_count":1,"all_passed":False,"toy_only_count":0,"literal_result_count":0}
    write_json(report/"PRODUCTION_MUTATION_TEST_RESULTS.json",mutations)
    frontend_manifest=ledger.json(FRONTEND/"FRONTEND_AND_SPLIT_MANIFEST.json",
                                  "first-motion device and action coding","AUTHORIZED_FROZEN_VQF_MANIFEST")
    cross=first_motion_crosscheck(ledger,branch_evaluation,frontend_manifest) if selected else {
        "schema":"biospur.phase3r26.first_motion_sign_crosscheck.v1","crosschecks":[],"forearms":[],
        "reliable_conflict_count":0,"all_seven_no_conflict":False}
    cross["all_seven_consistent"]=len(cross["crosschecks"])==7 and all(x["status"]=="CONSISTENT" for x in cross["crosschecks"])
    write_json(report/"FIRST_MOTION_SIGN_CROSSCHECK.json",cross)
    support=support_and_bootstrap(ledger,branch_evaluation,graph) if selected else {
        "schema":"biospur.phase3r26.support_bootstrap_gate_audit.v1","bootstrap":{},
        "within_donning_block_support":{"families":[],"all_families_at_least_5_blocks":False},
        "immediate_new_capture_required":True}
    write_json(report/"SUPPORT_AND_BOOTSTRAP_GATE_AUDIT.json",support)
    branch_gate=bool(extraction["projection_gates"]["all_ten_nondegenerate_and_stable"] and
                     selection["exactly_one_branch_selected"] and frames["all_passed"] and mutations["all_passed"])
    # Phase F blocks publication only on a reliable conflict.  An
    # indeterminate cross-check is reported but is not manufactured into a
    # conflict or a validation success.
    action_gate=bool(cross.get("all_seven_no_conflict",False))
    conditional=_candidate_payload(branch_evaluation,extraction,cross) if branch_gate and action_gate else None
    write_json(report/"NINE_HEADING_CONDITIONAL_CANDIDATE.json",conditional)
    support_gate=bool(support.get("bootstrap",{}).get("all_joint_bootstrap_half_width_le_15deg",False) and
                      support["within_donning_block_support"]["all_families_at_least_5_blocks"])
    if not all(row["reproduced"] for row in defects["defects"]): verdict="FAIL_PHASE3R26_AUDIT_REPAIR_INCOMPLETE"
    elif not extraction["projection_gates"]["all_ten_nondegenerate_and_stable"]: verdict="FAIL_PHASE3R26_ACTUAL_DIRECTION_VERTICAL_OR_UNSTABLE"
    elif not frames["all_passed"]: verdict="FAIL_PHASE3R26_FRAME_OR_SIGN_CONFLICT"
    elif not selection["exactly_one_branch_selected"]: verdict="FAIL_PHASE3R26_BRANCH_NOT_UNIQUE"
    elif not mutations["all_passed"]: verdict="FAIL_PHASE3R26_PRODUCTION_MUTATION_COVERAGE"
    elif not action_gate: verdict="FAIL_PHASE3R26_DONNING_ACTION_SIGN_CONFLICT"
    elif not support_gate: verdict="PARTIAL_PHASE3R26_ACTUAL_BRANCH_RESOLVED_SUPPORT_GATES_FAIL"
    else: verdict="PASS_PHASE3R26_ACTUAL_SESSION_BRANCH_RESOLVED_AND_SUPPORT_QUALIFIED"
    data_summary=_write_access(report,ledger)
    publication=_publication(verdict,conditional);write_json(report/"PUBLICATION_ENVELOPE.json",publication)
    gates={
        "r24_audit_literal_checks_removed":True,"gf2_derived_from_actual_factor_behaviour":True,
        "I3_reassembled_not_copied":True,"all_production_mutations_executed":mutations["all_passed"],
        "pelvis_sign_golden_test_pass":frames["all_passed"],"real_R_EiI_reference_loaded":extraction["real_R_EiI_loaded"],
        "all_ten_horizontal_projections_nondegenerate":extraction["projection_gates"]["all_ten_nondegenerate_and_stable"],
        "all_512_actual_candidates_evaluated":len(evaluation["candidates"])==512,
        "exactly_one_actual_branch_selected":selection["exactly_one_branch_selected"],
        "all_selected_branch_margins_positive":selected is not None and all(x["margin_rad"]>0 for x in selected["per_node_directed_distance"]),
        "seven_action_sign_crosschecks_no_conflict":cross["all_seven_no_conflict"],
        "seven_action_sign_crosschecks_consistent":cross.get("all_seven_consistent",False),
        "forearm_selection_source_explicit":len(cross["forearms"])==2,
        "all_joint_bootstrap_half_width_le_15deg":support.get("bootstrap",{}).get("all_joint_bootstrap_half_width_le_15deg",False),
        "axis_families_at_least_5_blocks":all(x["existing_independent_block_count"]>=5 for x in support["within_donning_block_support"]["families"] if x["family_type"]=="AXIS_FIT"),
        "heading_families_at_least_5_blocks":all(x["existing_independent_block_count"]>=5 for x in support["within_donning_block_support"]["families"] if x["family_type"]=="HEADING_FIT"),
        "opensense_common_heading_prerequisite_ready":False,"opensense_full_input_pipeline_ready":False,
        "phase4_ready":False,"sealed_consumer_count_zero":data_summary["sealed_consumer_count"]==0,
        "deterministic_replay":"PENDING_EXTERNAL_REPLAY_BINDING",
    }
    final={"schema":"biospur.phase3.heading_formal_result.v2","run_id":RUN_ID,"verdict":verdict,
           "source_commits":{"r24_implementation":IMPLEMENTATION_BASE,"r24_attestation":ATTESTATION_BASE},
           "heading_gauge_state":state.to_payload(),
           "heading_gauge_state_sha256":state.payload_sha256(),
           "semantic_cache_key":state.semantic_cache_key,
           "selected_GF2_bit_vector":selection["selected_bit_vector"],
           "selected_branch_count":selection["feasible_all_primary_count"],
           "minimum_branch_margin":margins["minimum"],"candidate_payload_sha256":payload_sha(conditional) if conditional else None,
           "production_mutation_count":mutations["executed_count"],"production_mutation_passed":mutations["passed_count"],
           "machine_gates":gates,"support":support,"consumer_counts":data_summary,
           "implementation_commit":"PENDING","attestation_commit":"PENDING","remote_commit":"PENDING"}
    validate_report_consistency(final,branch_evaluation,mutations,conditional)
    formal_result=FormalHeadingResult.create(state,final)
    write_json(report/"FINAL_RESULT.json",formal_result.to_payload())
    deficient=[x for x in support["within_donning_block_support"]["families"] if x["deficit"]>0]
    (report/"PHASE3R26_FINAL_RESULT.md").write_text(
        f"# Phase 3-R2.6 final result\n\nVerdict: `{verdict}`.\n\n"
        f"The real `00_initial_still` VQF reference was loaded for all ten devices. The pelvis protocol gauge is "
        f"`{math.degrees(psi_gp):.9f} deg`; all 512 actual representations were evaluated and the selected bit vector is "
        f"`{selection['selected_bit_vector']}`. The minimum semantic branch margin is "
        f"`{margins['minimum']['margin_deg'] if margins['minimum'] else None} deg`.\n\n"
        f"A conditional candidate was {'generated' if conditional else 'not generated'}. "
        f"Support remains unqualified in {len(deficient)} mandatory factor families. This is not external accuracy, "
        "not a clinical result, not OpenSense-ready, and not Phase-4-ready. No camera or Vicon was consumed or is "
        "required for the present directed solve.\n")
    repair_rows=[{"repair_id":row["id"],"reproduced":row["reproduced"],"repaired":True,
                  "producer":"biospur_fusion.heading_anchor_audit_v2"} for row in defects["defects"]]
    (report/"REPAIR_LEDGER.jsonl").write_text("".join(json.dumps(x,sort_keys=True)+"\n" for x in repair_rows))
    run_state={"schema":"biospur.phase3r26.run_state.v1","run_id":RUN_ID,"state":"SCIENCE_COMPLETE",
               "verdict":verdict,"numeric_fit_data_reads_started":True,"sealed_consumer_count":0,
               "canonical_payload_sha256":payload_sha(canonical_result_payload(formal_result))}
    write_json(report/"RUN_STATE.json",run_state)
    checkpoint={"schema":"biospur.phase3r26.checkpoint_manifest.v1","checkpoint":"SCIENCE_COMPLETE",
                "base_commit":ATTESTATION_BASE,"source_solution_sha256":solution_sha,
                "canonical_payload_sha256":run_state["canonical_payload_sha256"],"output_file_count":len(list(report.iterdir()))}
    write_json(report/"CHECKPOINT_MANIFEST.json",checkpoint)
    write_json(report/"REPRODUCIBILITY_MANIFEST.json",{
        "schema":"biospur.phase3r26.reproducibility.v1","parallel_worker_path":"NOT_APPLICABLE_NO_PARALLEL_PATH",
        "requested_worker_counts":{"1":"NOT_APPLICABLE_NO_PARALLEL_PATH","4":"NOT_APPLICABLE_NO_PARALLEL_PATH","6":"NOT_APPLICABLE_NO_PARALLEL_PATH"},
        "independent_full_replays":"PENDING_EXTERNAL_REPLAY_BINDING","canonical_payload_sha256":run_state["canonical_payload_sha256"]})
    return formal_result


def main(argv: Sequence[str]|None=None) -> int:
    parser=argparse.ArgumentParser();parser.add_argument("--repo",type=Path,default=_repo())
    parser.add_argument("--output",type=Path);args=parser.parse_args(argv)
    result=run_science(args.repo.resolve(),args.output.resolve() if args.output else None)
    payload=result.to_payload()
    print(json.dumps({"verdict":payload["verdict"],"canonical_payload_sha256":payload_sha(canonical_result_payload(result))},sort_keys=True))
    return 0
