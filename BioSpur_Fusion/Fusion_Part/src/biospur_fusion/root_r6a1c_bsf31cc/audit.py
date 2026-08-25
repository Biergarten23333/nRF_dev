"""Deterministic BSF31CC PCB, donning, and band-attachment audit."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np


COMMON_NINE = (
    "BSFEC35", "BSFB165", "BSFAA61", "BSF1120", "BSFC2CC",
    "BSF44AD", "BSF3C79", "BSF6C53", "BSF8BC4",
)
EXPECTED_HASHES = {
    "common_gerber": "e9dc90b51ff62b32a77d0071bdae2f7f866a6ddd2d06af585bc1dfcbce6d853a",
    "common_step": "dbe2fa4a961c2b1ffd2c1074b3cc2267e8ac38ef951fc1cde97c67564085fc1b",
    "common_enclosure_base": "b44438592fc6a8b0eb3e723c6b7c5a45c4784c0b1a4febc536599bbab9fff84b",
    "bsf31cc_gerber": "b87c9d32a844f44e0e6da3618dcbe3664e50b3d37e6a393e4d54c82f68cc0c19",
    "bsf31cc_step": "526b7635dcac94e06a70997fd5e40eb9f0036b7703463d180f9aa95b32d1d2cf",
    "parent_sha256sums": "0480092da630b6f6c11167d6fb88d4f822a155d80f9c2f2252a46706b6e20ab4",
    "immutable_ledger": "b159043eb7da4518ac6349832ca3c50e0b3453b1e35d97bbac29223f3e85c4eb",
}
LEDGER_COUNTS = {
    "anatomical_point": 5, "anchor_delay": 8, "anchor_position": 8,
    "bone_length": 8, "imu_extrinsic": 10, "joint_child": 9,
    "joint_parent": 9, "joint_rest": 9, "tag_lever": 10,
    "time_relationship": 10, "world_model_gauge": 1,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _sources(fusion: Path) -> dict[str, Path]:
    parent = fusion / "logs/root_r6a1c_deferred_measurement_bridge_20260825T102823Z"
    return {
        "common_gerber": fusion / "PCB/V0.20/Gerber_PCB17_2026-08-25.zip",
        "common_step": fusion / "PCB/V0.20/3D_PCB17_2026-08-25.step",
        "common_enclosure_base": fusion / "PCB/V0.20/FusionPCB底座.step",
        "bsf31cc_gerber": fusion / "PCB/V0.20/BSF31CC.zip",
        "bsf31cc_step": fusion / "PCB/V0.20/3D_PCB_V0.20_2026-08-25_31CC.step",
        "parent_sha256sums": parent / "SHA256SUMS",
        "parent_final": parent / "FINAL_RESULT.json",
        "immutable_ledger": fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json",
    }


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256_file(path)}


def _flying_probe(path: Path) -> dict[str, Any]:
    with ZipFile(path) as archive:
        value = json.loads(archive.read("FlyingProbeTesting.json"))
    if value["lengthUnit"] != "mil":
        raise ValueError(f"unexpected coordinate units in {path}")
    return value


def _components(probe: dict[str, Any]) -> dict[str, list[Any]]:
    return {row[1]: row for row in probe["components"]["rows"]}


def _placement(row: list[Any]) -> dict[str, Any]:
    return {
        "side": "TOP" if row[2] == "T" else "BOTTOM",
        "reference_xy_m": [row[3] * 0.0254 / 1000.0, row[4] * 0.0254 / 1000.0],
        "rotation_deg": float(row[5]),
        "conversion": "mil * 0.0254 mm/mil / 1000 mm/m",
    }


def _pin_nets(probe: dict[str, Any], component: str) -> dict[int, str]:
    result: dict[int, str] = {}
    prefix = component + "_"
    for row in probe["pins"]["rows"]:
        name = str(row[1])
        if name.startswith(prefix):
            suffix = name[len(prefix):]
            if suffix.isdigit():
                result[int(suffix)] = row[6]
    return result


def _validate_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    slots = ledger["slots"]
    counts = dict(sorted(Counter(row["category"] for row in slots).items()))
    valid = (
        len(slots) == 87 and len({row["slot_id"] for row in slots}) == 87
        and counts == LEDGER_COUNTS
        and all(row["value"] is None and row["status"] == "FROZEN_UNCERTAIN" for row in slots)
        and ledger["fitted_from_c1_count"] == 0
    )
    return {
        "valid": valid, "total": len(slots),
        "value_null": sum(row["value"] is None for row in slots),
        "FROZEN_UNCERTAIN": sum(row["status"] == "FROZEN_UNCERTAIN" for row in slots),
        "fitted_from_real_data": ledger["fitted_from_c1_count"],
        "category_counts": counts,
    }


def build_addendum(fusion: Path) -> dict[str, Any]:
    fusion = Path(fusion).resolve()
    sources = _sources(fusion)
    for key, expected in EXPECTED_HASHES.items():
        actual = sha256_file(sources[key])
        if actual != expected:
            raise ValueError(f"source hash mismatch for {key}: {actual}")

    common_probe = _flying_probe(sources["common_gerber"])
    torso_probe = _flying_probe(sources["bsf31cc_gerber"])
    common = _components(common_probe)
    torso = _components(torso_probe)
    common_core = {name: _placement(common[name]) for name in ("U1", "U4", "U7")}
    torso_core = {name: _placement(torso[name]) for name in ("U1", "U4", "U7")}

    critical_u1_pins = {35: "UWB_RX1", 36: "UWB_TX1", 37: "UWB_RDY", 42: "SDA", 44: "SCL"}
    common_u1 = _pin_nets(common_probe, "U1")
    torso_u1 = _pin_nets(torso_probe, "U1")
    critical_interface = {
        str(pin): {"expected_net": net, "common_net": common_u1.get(pin), "BSF31CC_net": torso_u1.get(pin)}
        for pin, net in critical_u1_pins.items()
    }

    pogo_rows = {}
    for row in torso_probe["pins"]["rows"]:
        name = str(row[1])
        if name.startswith("Mag_"):
            pogo_rows[name] = {
                "xy_m": [row[2] * 0.0254 / 1000.0, row[3] * 0.0254 / 1000.0],
                "net": row[6], "through_hole_m": row[11] * 0.0254 / 1000.0,
            }
    pogo_rows = {name: pogo_rows[name] for name in sorted(pogo_rows)}

    u4_S_m = [0.018542037084074, 0.0, 0.001538354076708]
    u7_S_m = [0.0, 0.0, 0.0]
    r_B_from_Iref = [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]]
    t_B_from_Iref_m = [u7_S_m[index] - u4_S_m[index] for index in range(3)]
    ledger = json.loads(sources["immutable_ledger"].read_text(encoding="utf-8"))
    ledger_audit = _validate_ledger(ledger)

    audit = {
        "schema": "biospur-root-r6a1c-bsf31cc-hardware-attachment-addendum-v1",
        "parent_result": {
            "path": str(sources["parent_final"].parent),
            "sha256sums": _source(sources["parent_sha256sums"]),
            "modified": False,
        },
        "source_evidence": {key: _source(path) for key, path in sources.items()},
        "hardware_family_binding": {
            "COMMON_NINE_V0_20_PCB17": {
                "nodes": list(COMMON_NINE), "node_count": 9,
                "layout_authority": "OPERATOR_ATTESTED_IDENTICAL_NINE_UNITS",
                "cad_sources": ["common_gerber", "common_step", "common_enclosure_base"],
                "enclosure_authority": "OPERATOR_ATTESTED_IDENTICAL_3D_PRINTED_BOX_ACROSS_COMMON_NINE",
                "shared_PCB_and_box_transform_scope": "COMMON_NINE_ONLY",
            },
            "BSF31CC_V0_20_N5BL": {
                "nodes": ["BSF31CC"], "node_count": 1,
                "layout_authority": "OPERATOR_ATTESTED_DISTINCT_PCB_FORM_AND_HASH_BOUND_CAD",
                "cad_sources": ["bsf31cc_gerber", "bsf31cc_step"],
                "shared_transform_scope": "BSF31CC_ONLY",
                "transparent_box_CAD_available": False,
            },
            "cross_family_transform_reuse_authorized": False,
            "common_nine_box_reuse_for_BSF31CC_authorized": False,
            "all_ten_nodes_covered_once": True,
        },
        "fusion_core_equivalence": {
            "classification": "FUSION_CORE_INTERFACE_EQUIVALENT_NOT_WHOLE_BOARD_IDENTICAL",
            "operator_statement": "nine Fusion units are identical; BSF31CC is electrically equivalent for the Fusion function but has a different PCB form",
            "critical_u1_interface_pins": critical_interface,
            "same_core_component_footprints": ["U1/NINA-B306 functional module", "U4/DWM1001C", "U7/JY901S"],
            "whole_board_electrical_identity_claimed": False,
            "why_not_whole_board_identical": "BSF31CC CAD contains additional BMD101/ECG/electrode-pole circuitry; Gerber files are not a source schematic/netlist equivalence proof",
        },
        "layout_comparison": {
            "common_core_2d": common_core,
            "BSF31CC_core_2d": torso_core,
            "distinct_layout_proven": common_core != torso_core,
            "BSF31CC_board_outline_Gerber_m": {
                "x_min": -0.03371342, "x_max": 0.03726942,
                "y_min": -0.01500003, "y_max": 0.01499997,
                "width": 0.07098284, "height": 0.03,
                "shape": "rounded/pill outline",
            },
            "BSF31CC_board_vertex_bounds_STEP_m": {
                "minimum": [-0.03370078740157, -0.01498736093472, 0.0],
                "maximum": [0.037256794513589, 0.0149872999746, 0.001538354076708],
            },
        },
        "BSF31CC_frames": {
            "notation": "T_X_Y maps coordinates from source frame Y into target frame X",
            "runtime_units": "SI",
            "S31": "BSF31CC STEP assembly frame",
            "B31": "BSF31CC board frame at U4 CAD reference on PCB top plane; axes parallel to EasyEDA/STEP board axes; +z leaves top/component side",
            "Iref31": "U7 CAD component reference, not the IMU die origin",
            "Fbutton31": "band snap/button attachment frame; exact button reference designators and in-plane origin pending",
            "T_S31_B31": {
                "source_frame": "B31", "target_frame": "S31",
                "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "translation_m": u4_S_m,
            },
            "T_B31_Iref31": {
                "source_frame": "Iref31", "target_frame": "B31",
                "rotation": r_B_from_Iref,
                "translation_m": t_B_from_Iref_m,
                "status": "CAD_COMPONENT_REFERENCE_ONLY_NOT_IMU_DIE_EXTRINSIC",
            },
            "IMU_register_to_die_and_B31": {"rotation": None, "translation_m": None, "status": "PENDING_SIGNED_AXIS_AND_DIE_ORIGIN_QUALIFICATION"},
        },
        "BSF31CC_physical_orientation": {
            "PCB_top_component_side": "outward/away from body",
            "PCB_bottom_side": "toward body and Polar-style band",
            "B31_plus_z": "outward/away from body",
            "B31_minus_z": "toward body/band",
            "down_marker": "four-contact pogo row",
            "down_direction": "B31 -y toward ground/feet in neutral wearing",
            "up_direction": "B31 +y toward head in neutral wearing",
            "pogo_connector": {
                "component_reference": "Mag", "role": "ORIENTATION_MARKER_AND_ELECTRICAL_CONNECTOR_NOT_BAND_FIXATION",
                "pins": pogo_rows, "row_y_m": -0.013208,
                "negative_outline_edge_y_m": -0.01500003,
                "distance_from_negative_y_outline_edge_m": 0.00179203,
            },
            "authority": "OPERATOR_ATTESTED_AND_CAD_POSITION_CROSS_CHECKED",
            "motion_time_gravity_constraint": False,
        },
        "BSF31CC_band_attachment": {
            "mechanical_chain": ["Polar-style band", "PCB-mounted snap buttons", "BSF31CC PCB"],
            "fixation_authority": "PCB_BUTTONS_DIRECTLY_FIX_BAND_TO_BOARD",
            "BSF31CC_transparent_3d_printed_box_role": "PROTECTIVE_ONLY_NOT_FIXATION_OR_REGISTRATION_AUTHORITY",
            "common_nine_3d_printed_box_applies_to_BSF31CC": False,
            "missing_box_CAD_blocks_band_to_PCB_attachment": False,
            "band_surface_to_PCB_bottom_plane": {
                "value": 0.006, "unit": "m",
                "mathematical_type": "scalar perpendicular separation",
                "source_surface": "band surface facing PCB",
                "target_plane": "PCB bottom-layer reference plane",
                "direction": "along PCB normal; band lies on B31 -z side",
                "uncertainty": None,
                "provenance": "OPERATOR_MEASURED",
                "status": "MEASURED_VALUE_RECORDED_UNCERTAINTY_PENDING",
            },
            "PCB_bottom_plane_z_in_B31_m": -0.001538354076708,
            "band_surface_plane_z_in_B31_m_if_parallel": -0.007538354076708,
            "conditional_plane_formula": "-0.001538354076708 m PCB-bottom offset from B31 top-plane origin - 0.006 m measured separation",
            "parallelism_status": "NOT_INDEPENDENTLY_MEASURED",
            "exact_button_reference_designators": None,
            "Fbutton31_transform": None,
            "full_rigid_attachment_transform_status": "PENDING_BUTTON_REFERENCE_IDENTIFICATION_AND_UNCERTAINTY",
            "enclosure_geometric_centre_C_required_for_attachment": False,
        },
        "antenna_semantics": {
            "B306_and_DWM1001C_antenna_component_side": "TOP_OUTWARD",
            "UWB_reference_region": "large gold printed DWM1001C UWB antenna",
            "BLE_exclusion": "small ceramic antenna belongs to BLE and is never the UWB reference",
            "UWB_nominal_point_B31_m": None,
            "classification": "CAD_NOMINAL_ANTENNA_REFERENCE_REGION_ONLY",
            "phase_centre_status": "PHASE_CENTRE_OFFSET_UNQUALIFIED",
        },
        "real_lever_authority": {
            "IMU_origin_to_band": False,
            "IMU_origin_to_UWB_nominal_point": False,
            "reason": "6 mm closes one direct board-to-band normal distance, but IMU die origin, exact button frame, uncertainty, and unique UWB nominal point remain pending",
        },
        "immutable_registry": {**ledger_audit, "source": _source(sources["immutable_ledger"]), "modified": False},
        "execution_boundaries": {
            "real_fusion_run": False, "real_body_state_updated": False,
            "human_measurement_fabricated": False, "phase_centre_manufactured": False,
            "parent_result_modified": False,
        },
    }
    return audit


def _rigid_inverse(rotation: np.ndarray, translation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return rotation.T, -rotation.T @ translation


def run_gates(audit: dict[str, Any]) -> dict[str, Any]:
    rows = []
    def gate(letter: str, name: str, passed: bool, **metrics: Any) -> None:
        rows.append({"gate": letter, "name": name, "pass": bool(passed), "metrics": metrics})

    sources = audit["source_evidence"]
    gate("A", "all_source_hashes_exact", all(sources[key]["sha256"] == expected for key, expected in EXPECTED_HASHES.items()))
    families = audit["hardware_family_binding"]
    all_nodes = families["COMMON_NINE_V0_20_PCB17"]["nodes"] + families["BSF31CC_V0_20_N5BL"]["nodes"]
    gate("B", "ten_nodes_partitioned_into_two_hardware_families", len(all_nodes) == len(set(all_nodes)) == 10 and set(families["BSF31CC_V0_20_N5BL"]["nodes"]) == {"BSF31CC"})
    gate("C", "cross_family_transform_reuse_forbidden", not families["cross_family_transform_reuse_authorized"] and not families["common_nine_box_reuse_for_BSF31CC_authorized"] and families["COMMON_NINE_V0_20_PCB17"]["enclosure_authority"] == "OPERATOR_ATTESTED_IDENTICAL_3D_PRINTED_BOX_ACROSS_COMMON_NINE" and audit["layout_comparison"]["distinct_layout_proven"])
    interface = audit["fusion_core_equivalence"]["critical_u1_interface_pins"]
    gate("D", "fusion_core_interface_names_match", all(row["common_net"] == row["BSF31CC_net"] == row["expected_net"] for row in interface.values()))
    gate("E", "whole_board_identity_not_overclaimed", not audit["fusion_core_equivalence"]["whole_board_electrical_identity_claimed"] and "BMD101" in audit["fusion_core_equivalence"]["why_not_whole_board_identical"])
    orientation = audit["BSF31CC_physical_orientation"]
    gate("F", "component_side_outward_bottom_side_bandward", orientation["B31_plus_z"] == "outward/away from body" and orientation["B31_minus_z"] == "toward body/band")
    pogo = orientation["pogo_connector"]
    gate("G", "pogo_row_is_negative_y_down_marker_not_attachment", pogo["role"].endswith("NOT_BAND_FIXATION") and np.isclose(pogo["row_y_m"], -0.013208) and np.isclose(pogo["distance_from_negative_y_outline_edge_m"], 0.00179203) and len(pogo["pins"]) == 4)
    attachment = audit["BSF31CC_band_attachment"]
    measured = attachment["band_surface_to_PCB_bottom_plane"]
    gate("H", "six_mm_direct_attachment_measurement_recorded", measured["value"] == 0.006 and measured["provenance"] == "OPERATOR_MEASURED" and measured["uncertainty"] is None and np.isclose(attachment["band_surface_plane_z_in_B31_m_if_parallel"], attachment["PCB_bottom_plane_z_in_B31_m"] - measured["value"]))
    gate("I", "enclosure_not_attachment_authority", attachment["BSF31CC_transparent_3d_printed_box_role"].endswith("NOT_FIXATION_OR_REGISTRATION_AUTHORITY") and not attachment["common_nine_3d_printed_box_applies_to_BSF31CC"] and not attachment["missing_box_CAD_blocks_band_to_PCB_attachment"] and not attachment["enclosure_geometric_centre_C_required_for_attachment"])
    gate("J", "attachment_not_overpromoted_to_full_transform", attachment["Fbutton31_transform"] is None and attachment["exact_button_reference_designators"] is None and attachment["full_rigid_attachment_transform_status"].startswith("PENDING_"))
    antenna = audit["antenna_semantics"]
    gate("K", "uwb_region_not_phase_centre", antenna["UWB_nominal_point_B31_m"] is None and antenna["phase_centre_status"] == "PHASE_CENTRE_OFFSET_UNQUALIFIED" and "ceramic" in antenna["BLE_exclusion"])
    transform = audit["BSF31CC_frames"]["T_B31_Iref31"]
    r = np.asarray(transform["rotation"]); t = np.asarray(transform["translation_m"])
    ri, ti = _rigid_inverse(r, t); rc = r @ ri; tc = t + r @ ti
    gate("L", "cad_reference_transform_inverse_consistent", np.allclose(rc, np.eye(3), atol=1e-15) and np.allclose(tc, np.zeros(3), atol=1e-15) and np.isclose(np.linalg.det(r), 1.0))
    registry = audit["immutable_registry"]
    gate("M", "immutable_87_slot_registry_unchanged", registry["valid"] and registry["total"] == registry["value_null"] == registry["FROZEN_UNCERTAIN"] == 87 and registry["fitted_from_real_data"] == 0 and not registry["modified"])
    digest_a = hashlib.sha256(canonical_bytes(audit)).hexdigest()
    digest_b = hashlib.sha256(canonical_bytes(json.loads(canonical_bytes(audit)))).hexdigest()
    gate("N", "deterministic_serialization", digest_a == digest_b, audit_sha256=digest_a)
    return {"schema": "biospur-root-r6a1c-bsf31cc-addendum-gates-v1", "gate_count": len(rows), "all_pass": all(row["pass"] for row in rows), "gates": rows}
