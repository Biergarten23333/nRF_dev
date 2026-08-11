#!/usr/bin/env python3
"""Pure-UWB replay of the v47 ten-node capture through UWB_TAG_T4/U5.

No IMU field is imported into a positioning frame.  The two solver revisions
receive identical geometry, timestamps, validity masks, ranges, and quality.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np


REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "B306_Part/tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from v47_real_data_adapter import NODES, load_capture  # noqa: E402

T0_MASTER_MS = 77_860_264
T0_EPOCH_S = datetime.fromisoformat("2026-08-11T13:09:59.019+02:00").timestamp()
SOLVER_BASE = REPO / "UWB_Part/2026-07-15-FREEZE/scripts/solvers/erlangen_deployment_v4io_t4"
SOLVER_PATHS = {
    "UWB_TAG_T4": SOLVER_BASE / "stage2_position_T4_pristine",
    "UWB_TAG_U5": SOLVER_BASE / "stage2_position",
}
POSITION_FIELDS = [
    "node", "solver", "t0_s", "master_ms", "node_ms", "sweep", "valid_mask",
    "anchors_input", "anchors_used", "accepted_anchor_ids", "rejected_anchor_ids",
    "x_mm", "y_mm", "z_mm", "residual_rms_mm", "residual_p95_abs_mm",
    "max_abs_residual_mm", "condition", "gdop", "vdop", "covariance",
    "solver_status", "failure_reason",
]
EXPECTED_ANCHOR_SLOT_IDS = tuple(range(8))


def validate_anchor_slot_identity(anchor_ids) -> None:
    """Require the capture's eight serializer slots to be A..H / IDs 0..7."""
    observed = tuple(int(value) for value in anchor_ids)
    if observed != EXPECTED_ANCHOR_SLOT_IDS:
        raise ValueError(
            f"anchor slot identity mismatch: expected={EXPECTED_ANCHOR_SLOT_IDS} observed={observed}"
        )


def validate_delay_ownership(*, transport_applies_v4_delay: bool, solver_applies_v4_delay: bool) -> None:
    """Fail closed if the V4 residual delay would be applied by two owners."""
    if transport_applies_v4_delay and solver_applies_v4_delay:
        raise ValueError("V4 anchor delay would be double-applied")
    if not solver_applies_v4_delay:
        raise ValueError("frozen UWB Tag solver must own the V4 anchor delay")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 << 20):
            h.update(chunk)
    return h.hexdigest()


def load_solver(label: str):
    for name in list(sys.modules):
        if name == "biospur_tag_positioning_offline_solver" or name.startswith(
            "biospur_tag_positioning_offline_solver."
        ):
            del sys.modules[name]
    for path in SOLVER_PATHS.values():
        try:
            sys.path.remove(str(path))
        except ValueError:
            pass
    sys.path.insert(0, str(SOLVER_PATHS[label]))
    models = importlib.import_module("biospur_tag_positioning_offline_solver.models")
    layout_io = importlib.import_module("biospur_tag_positioning_offline_solver.layout_io")
    c_solver = importlib.import_module("biospur_tag_positioning_offline_solver.c_solver")
    return models, layout_io, c_solver


def replay_variant(label: str, layout_path: Path, uwb: dict[str, np.ndarray]) -> list[dict]:
    models, layout_io, c_solver = load_solver(label)
    layout = layout_io.load_layout_json(layout_path)
    rows: list[dict] = []
    for node in NODES:
        solver = c_solver.TagPositionSolver(layout, models.SolverConfig(method="T4"))
        for record in uwb[node]:
            validate_anchor_slot_identity(record["anchor_id"])
            observations = []
            seen: set[int] = set()
            failure = ""
            valid_mask = int(record["valid_mask"])
            for slot in range(8):
                if not (valid_mask & (1 << slot)):
                    continue
                aid = int(record["anchor_id"][slot])
                value = int(record["range_mm"][slot])
                if aid not in range(8):
                    failure = "ANCHOR_ID_OUT_OF_RANGE"
                    continue
                if aid in seen:
                    failure = "DUPLICATE_ANCHOR_ID"
                    continue
                if value <= 0 or value == 0xFFFF:
                    failure = "VALID_MASK_RANGE_INVALID"
                    continue
                seen.add(aid)
                observations.append(
                    models.Observation(
                        anchor_id=aid,
                        range_mm=float(value),
                        quality_percent=float(record["quality"][slot]),
                        status="O",
                    )
                )
            t0_s = (int(record["master_ms"]) - T0_MASTER_MS) / 1000.0
            frame = models.Frame(
                tag=node,
                sweep=int(record["sweep"]),
                host_elapsed_s=t0_s,
                host_epoch_s=T0_EPOCH_S + t0_s,
                observations=tuple(observations),
                imu=None,
            )
            result = None if failure else solver.solve_frame(frame)
            if result is None and not failure:
                failure = "TOO_FEW_ANCHORS_OR_SOLVER_FAILURE"
            accepted = []
            rejected = []
            if result is not None:
                accepted = sorted(aid for aid, used in result.used_by_anchor.items() if used)
                rejected = sorted(aid for aid, used in result.used_by_anchor.items() if not used)
            rows.append(
                {
                    "node": node,
                    "solver": label,
                    "t0_s": t0_s,
                    "master_ms": int(record["master_ms"]),
                    "node_ms": int(record["node_ms"]),
                    "sweep": int(record["sweep"]),
                    "valid_mask": f"0x{valid_mask:02X}",
                    "anchors_input": len(observations) if result is None else result.anchors_input,
                    "anchors_used": 0 if result is None else result.anchors_used,
                    "accepted_anchor_ids": ";".join(map(str, accepted)),
                    "rejected_anchor_ids": ";".join(map(str, rejected)),
                    "x_mm": "" if result is None else result.x_mm,
                    "y_mm": "" if result is None else result.y_mm,
                    "z_mm": "" if result is None else result.z_mm,
                    "residual_rms_mm": "" if result is None else result.residual_rms_mm,
                    "residual_p95_abs_mm": "" if result is None else result.residual_p95_abs_mm,
                    "max_abs_residual_mm": "" if result is None else result.max_abs_residual_mm,
                    # The frozen T4/U5 API does not define these quantities.
                    "condition": "",
                    "gdop": "",
                    "vdop": "",
                    "covariance": "",
                    "solver_status": "FAIL" if result is None else result.status,
                    "failure_reason": failure,
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def solved(rows: list[dict], node: str, start: float, end: float) -> list[dict]:
    return [
        row for row in rows
        if row["node"] == node and start <= float(row["t0_s"]) < end
        and row["solver_status"] == "ok"
    ]


def platform_row(rows: list[dict], node: str, name: str, start: float, end: float) -> dict:
    all_rows = [row for row in rows if row["node"] == node and start <= float(row["t0_s"]) < end]
    ok = [row for row in all_rows if row["solver_status"] == "ok"]
    base = {
        "node": node, "platform": name, "start_s": start, "end_s": end,
        "records": len(all_rows), "solutions": len(ok),
        "valid_solution_rate": len(ok) / len(all_rows) if all_rows else 0.0,
    }
    if not ok:
        return {**base, "median_x_mm": "", "median_y_mm": "", "median_z_mm": "",
                "rms_scatter_mm": "", "p95_scatter_mm": "", "vertical_scatter_std_mm": ""}
    xyz = np.asarray([[float(r["x_mm"]), float(r["y_mm"]), float(r["z_mm"])] for r in ok])
    med = np.median(xyz, axis=0)
    distance = np.linalg.norm(xyz - med, axis=1)
    return {
        **base,
        "median_x_mm": med[0], "median_y_mm": med[1], "median_z_mm": med[2],
        "rms_scatter_mm": float(np.sqrt(np.mean(distance * distance))),
        "p95_scatter_mm": float(np.quantile(distance, 0.95)),
        "vertical_scatter_std_mm": float(np.std(xyz[:, 2])),
    }


def table_vibration_rows(rows: list[dict], events_path: Path) -> list[dict]:
    out: list[dict] = []
    with events_path.open(newline="", encoding="utf-8") as handle:
        events = [row for row in csv.DictReader(handle) if row["classification"] == "TABLE_COMMON_MODE_VIBRATION"]
    for event in events:
        start, end = float(event["onset_s"]), float(event["end_s"])
        for node in NODES:
            during = solved(rows, node, start, end)
            before = solved(rows, node, max(0.0, start - 5.0), start)
            if not during or not before:
                out.append({
                    "event_id": event["event_id"], "node": node, "start_s": start, "end_s": end,
                    "solutions_during": len(during), "median_response_mm": "", "p95_response_mm": "",
                    "reason": "NO_DURING_OR_BASELINE_SOLUTION",
                })
                continue
            baseline = np.median(np.asarray([[float(r[k]) for k in ("x_mm", "y_mm", "z_mm")] for r in before]), axis=0)
            xyz = np.asarray([[float(r[k]) for k in ("x_mm", "y_mm", "z_mm")] for r in during])
            response = np.linalg.norm(xyz - baseline, axis=1)
            out.append({
                "event_id": event["event_id"], "node": node, "start_s": start, "end_s": end,
                "solutions_during": len(during), "median_response_mm": float(np.median(response)),
                "p95_response_mm": float(np.quantile(response, 0.95)), "reason": "",
            })
    return out


def comparison_rows(t4: list[dict], u5: list[dict]) -> list[dict]:
    out = []
    for left, right in zip(t4, u5, strict=True):
        if (left["node"], left["sweep"], left["master_ms"]) != (
            right["node"], right["sweep"], right["master_ms"]
        ):
            raise RuntimeError("T4/U5 replay key mismatch")
        both = left["solver_status"] == right["solver_status"] == "ok"
        delta = ""
        if both:
            delta = math.sqrt(sum((float(left[k]) - float(right[k])) ** 2 for k in ("x_mm", "y_mm", "z_mm")))
        out.append({
            "node": left["node"], "t0_s": left["t0_s"], "sweep": left["sweep"],
            "valid_mask": left["valid_mask"], "t4_status": left["solver_status"],
            "u5_status": right["solver_status"], "t4_x_mm": left["x_mm"], "t4_y_mm": left["y_mm"],
            "t4_z_mm": left["z_mm"], "u5_x_mm": right["x_mm"], "u5_y_mm": right["y_mm"],
            "u5_z_mm": right["z_mm"], "position_delta_mm": delta,
            "t4_residual_rms_mm": left["residual_rms_mm"],
            "u5_residual_rms_mm": right["residual_rms_mm"],
            "t4_anchors_used": left["anchors_used"], "u5_anchors_used": right["anchors_used"],
        })
    return out


def make_plots(out_dir: Path, rows: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 7))
    for node in NODES:
        ok = solved(rows, node, 1.0, 484.0)
        if not ok:
            continue
        xyz = np.asarray([[float(r["x_mm"]), float(r["y_mm"]), float(r["z_mm"])] for r in ok])
        ax.scatter(xyz[:: max(1, len(xyz) // 250), 0], xyz[:: max(1, len(xyz) // 250), 1], s=3, alpha=0.25)
        med = np.median(xyz, axis=0)
        ax.text(med[0], med[1], node, fontsize=7)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("V4-io x (mm)")
    ax.set_ylabel("V4-io y (mm)")
    ax.set_title("UWB_TAG_T4: T0+1–484 s positions (relative V4-io frame)")
    fig.tight_layout()
    fig.savefig(out_dir / "uwb_t4_static_positions.svg")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 5))
    for node in NODES:
        ok = solved(rows, node, 0.0, 1801.0)
        ax.plot([float(r["t0_s"]) for r in ok], [float(r["residual_rms_mm"]) for r in ok], lw=0.45, label=node)
    ax.set_xlabel("T0 elapsed (s)")
    ax.set_ylabel("per-sweep residual RMS (mm)")
    ax.set_title("UWB_TAG_T4 residual over the full formal capture")
    ax.legend(ncol=5, fontsize=7)
    fig.tight_layout()
    fig.savefig(out_dir / "uwb_t4_residual_timeline.svg")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    validate_delay_ownership(
        transport_applies_v4_delay=False,
        solver_applies_v4_delay=True,
    )
    _imu, uwb, audit = load_capture(args.data_root)
    if audit.raw_sha256 != "c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8":
        raise RuntimeError(f"raw SHA mismatch: {audit.raw_sha256}")

    t4 = replay_variant("UWB_TAG_T4", args.layout, uwb)
    u5 = replay_variant("UWB_TAG_U5", args.layout, uwb)
    write_csv(args.out_dir / "PER_SWEEP_POSITIONS.csv", t4, POSITION_FIELDS)
    comparison = comparison_rows(t4, u5)
    write_csv(args.out_dir / "T4_VS_U5_COMPARISON.csv", comparison, list(comparison[0]))

    static = [platform_row(t4, node, "T0_PLUS_1_TO_484", 1.0, 484.0) for node in NODES]
    write_csv(args.out_dir / "PER_NODE_STATIC_POSITION.csv", static, list(static[0]))
    moves = []
    for node in ("BSFC2CC", "BSFAA61"):
        moves.append(platform_row(t4, node, "PRE_MOVE_COMMON_STATIC", 1.0, 484.0))
        moves.append(platform_row(t4, node, "POST_MOVE_COMMON_STATIC", 506.0, 535.0))
    for node in ("BSFC2CC", "BSFAA61"):
        before, after = [row for row in moves if row["node"] == node]
        if before["median_x_mm"] != "" and after["median_x_mm"] != "":
            delta = np.asarray([after[f"median_{a}_mm"] - before[f"median_{a}_mm"] for a in "xyz"])
            after["delta_from_pre_x_mm"], after["delta_from_pre_y_mm"], after["delta_from_pre_z_mm"] = delta
            after["delta_norm_mm"] = float(np.linalg.norm(delta))
    move_fields = list(dict.fromkeys(key for row in moves for key in row))
    write_csv(args.out_dir / "MOVE_PLATFORM_POSITIONS.csv", moves, move_fields)

    events_path = args.data_root / "analysis_real_sensor_static_v1/DISTURBANCE_EVENTS.csv"
    vibration = table_vibration_rows(t4, events_path)
    write_csv(args.out_dir / "TABLE_VIBRATION_UWB_RESPONSE.csv", vibration, list(vibration[0]))

    accounting = {
        "schema": "biospur-v47-pure-uwb-position-replay-v1",
        "raw_sha256": audit.raw_sha256,
        "layout_sha256": sha256_file(args.layout),
        "t0_master_ms": T0_MASTER_MS,
        "nodes": list(NODES),
        "total_uwb_records": sum(len(uwb[node]) for node in NODES),
        "per_node_records": {node: len(uwb[node]) for node in NODES},
        "solvers": {},
        "accounting_closed": True,
        "pure_uwb": True,
        "imu_used": False,
        "zupt_used": False,
        "geometry_refit_from_tags": False,
        "unsupported_solver_outputs": ["condition", "GDOP", "VDOP", "covariance/uncertainty"],
    }
    for label, rows in (("UWB_TAG_T4", t4), ("UWB_TAG_U5", u5)):
        status = Counter(row["solver_status"] for row in rows)
        failures = Counter(row["failure_reason"] for row in rows if row["failure_reason"])
        accounting["solvers"][label] = {
            "rows": len(rows), "status": dict(sorted(status.items())),
            "failures": dict(sorted(failures.items())),
            "solution_rate": status.get("ok", 0) / len(rows),
        }
        accounting["accounting_closed"] &= len(rows) == accounting["total_uwb_records"]
    accounting["t4_u5_key_match"] = len(comparison) == len(t4) == len(u5)
    accounting["t4_u5_exact_position_matches"] = sum(
        row["position_delta_mm"] != "" and float(row["position_delta_mm"]) == 0.0 for row in comparison
    )
    accounting["table_vibration_event_count"] = len({row["event_id"] for row in vibration})
    (args.out_dir / "POSITION_SOLVER_ACCOUNTING.json").write_text(
        json.dumps(accounting, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    make_plots(args.out_dir, t4)
    print(json.dumps(accounting, indent=2, sort_keys=True))
    return 0 if accounting["accounting_closed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
