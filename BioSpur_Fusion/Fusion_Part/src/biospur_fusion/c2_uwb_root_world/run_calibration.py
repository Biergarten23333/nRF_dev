"""Bounded calibration runner for the approved C2 UWB contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.uwb.frontend import CanonicalT4Frontend

from .calibration import CALIBRATION_ORDER, bias_from_held, held_link_task, q_from_t4
from .u0 import ClockModel, decode_uwb_only

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
LAYOUT = ROOT.parent / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
NODES = ("BSFEC35", "BSFB165", "BSFAA61", "BSF1120", "BSF31CC", "BSFC2CC",
         "BSF44AD", "BSF3C79", "BSF6C53", "BSF8BC4")
PHYSICAL_DIRECTORY = {
    "00_initial_still": "00_initial_still", "02_t_pose": "02_t_pose",
    "03_pelvis_hula_circle": "03_pelvis_tilt_shift", "04_shoulder_left": "04_shoulder_left",
    "05_shoulder_right": "05_shoulder_right", "06_elbow_left": "06_elbow_left",
    "07_elbow_right": "07_elbow_right", "08_hip_left": "08_hip_left",
    "09_hip_right": "09_hip_right", "10_knee_left_seated": "10_knee_left",
    "11_knee_right_seated": "11_knee_right", "12_heel_raise_left": "12_heel_raise_left",
    "13_heel_raise_right": "13_heel_raise_right", "14_trunk_flex_extend": "14_trunk_flex_extend",
    "15_trunk_axial_rotation": "15_trunk_axial_rotation", "16_squat": "16_squat",
    "17_final_still": "17_final_still", "18_heel_to_butt_left": "18_heel_to_butt_left",
    "19_heel_to_butt_right": "19_heel_to_butt_right",
}

_ROWS = None
_CLOCKS = None
_ANCHORS = None


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    os.replace(temporary, path)


def _checkpoint_path(directory: Path, node: str, anchor: int) -> Path:
    return directory / f"{node}_anchor_{anchor}.json"


def _clock_models(clock_table: Path) -> dict[str, ClockModel]:
    document = json.loads(clock_table.read_text())
    if clock_table.name != "CLOCK_TABLE_CALIBRATION_ONLY.json":
        raise ValueError("production requires the exact calibration-only clock table")
    if document.get("source_function") != (
        "biospur_fusion.c2_uwb_root_world.beacon_clock.align_capture_beacon_only"
    ):
        raise ValueError("clock table was not produced by the beacon-only function")
    source = Path(__file__).with_name("beacon_clock.py")
    if document.get("source_sha256") != _sha(source):
        raise ValueError("clock source hash mismatch")
    contract = document.get("clock_contract", {})
    if not contract.get("pass"):
        raise ValueError("beacon-only clock gate did not pass")
    if contract.get("accepted_listener_record_kinds") != ["LBD"]:
        raise ValueError("clock table consumed non-beacon Listener records")
    if contract.get("poll_or_response_measurements_consumed") is not False:
        raise ValueError("clock table consumed Listener poll/response measurements")
    required_timers = {"uwb.strobe_us", "uwb.frame_us", "imu.base_us", "imu.trigger_us"}
    if set(contract.get("same_model_applies_to", [])) != required_timers:
        raise ValueError("clock table does not bind UWB and IMU to the same TIMER2 model")
    owner = document["models"]
    return {node: ClockModel(int(row["boot_epoch"]), float(row["a_ns_per_us"]),
                             float(row["b_ns"]), float(row["sigma_ns"]))
            for node, row in owner.items()}


def _beacon_boundary_bridges(clock_table: Path) -> list[tuple[float, float]]:
    """Return LBD-derived host-to-global bridges for action-boundary selection.

    Host time is used only to select the labelled ACTION_START/ACTION_STOP
    interval.  Every retained measurement timestamp is still produced from the
    node's B306 TIMER2 model.
    """
    document = json.loads(clock_table.read_text())
    bridges = document.get("audit", {}).get("bridges", {})
    result = [
        (float(row["global_us_per_host_s"]), float(row["global_us_intercept"]))
        for row in bridges.values()
    ]
    if not result:
        raise ValueError("clock table lacks LBD boundary bridges")
    return result


def labelled_bounds_global_ns(events_path: Path,
                              bridges: list[tuple[float, float]]) -> tuple[int, int]:
    """Map labelled host action boundaries onto the Beacon global time axis."""

    events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    by_name = {row["event"]: row for row in events}
    try:
        start_host_s = int(by_name["ACTION_START"]["host_monotonic_ns"]) * 1e-9
        stop_host_s = int(by_name["ACTION_STOP"]["host_monotonic_ns"]) * 1e-9
    except KeyError as exc:
        raise ValueError(f"missing labelled action boundary in {events_path}") from exc
    start_ns = int(round(np.median([slope * start_host_s + intercept
                                    for slope, intercept in bridges]) * 1_000.0))
    stop_ns = int(round(np.median([slope * stop_host_s + intercept
                                   for slope, intercept in bridges]) * 1_000.0))
    if stop_ns <= start_ns:
        raise ValueError(f"non-positive labelled action interval in {events_path}")
    return start_ns, stop_ns


def _action_bounds_global_ns(physical_directory: str,
                             bridges: list[tuple[float, float]]) -> tuple[int, int, Path]:
    events_path = DATASET / "actions" / physical_directory / "rep_01/events/ACTION_EVENTS.jsonl"
    start_ns, stop_ns = labelled_bounds_global_ns(events_path, bridges)
    return start_ns, stop_ns, events_path


def _parse_calibration(clocks: dict[str, ClockModel],
                       bridges: list[tuple[float, float]]) -> tuple[dict, dict]:
    episodes = {}
    audit = {"episodes": {}, "nodes": {}, "all_checks_pass": True}
    last = {}
    for episode in CALIBRATION_ORDER:
        path = DATASET / "actions" / PHYSICAL_DIRECTORY[episode] / "rep_01/raw/fusion_host_raw.cobs.bin"
        rows, summary = decode_uwb_only(path)
        lo, hi, events_path = _action_bounds_global_ns(PHYSICAL_DIRECTORY[episode], bridges)
        kept = [row for row in rows if row.node in clocks
                and lo <= int(round(clocks[row.node].a_ns_per_us * row.strobe_us
                                       + clocks[row.node].b_ns)) < hi]
        episodes[episode] = kept
        audit["episodes"][episode] = {
            "path": str(path), "sha256": _sha(path), "decoded_uwb": len(rows),
            "formal_uwb": len(kept), "decode_errors": summary.decode_errors,
            "selection": "LABELLED_ACTION_BOUNDARY_VIA_LBD_HOST_BRIDGE",
            "events_path": str(events_path), "events_sha256": _sha(events_path),
            "start_global_ns": lo, "stop_global_ns_exclusive": hi,
            "measurement_time_source": "B306_TIMER2",
            "host_time_role": "ACTION_BOUNDARY_SELECTION_ONLY",
            "listener_kinds_consumed": ["LBD"],
        }
        for node in NODES:
            node_rows = [row for row in kept if row.node == node]
            ok = bool(node_rows and all(row.boot == 0 for row in node_rows)
                      and len({row.identity for row in node_rows}) == 1
                      and all(b.strobe_us > a.strobe_us and b.frame_us > a.frame_us
                              for a, b in zip(node_rows, node_rows[1:])))
            if node in last and node_rows:
                prior = last[node]
                ok &= (node_rows[0].strobe_us > prior.strobe_us
                       and node_rows[0].frame_us > prior.frame_us
                       and 0 < ((node_rows[0].sequence - prior.sequence) & 0xffffffff) < 2**31
                       and 0 < ((node_rows[0].node_ms - prior.node_ms) & 0xffffffff) < 2**31)
            if node_rows:
                last[node] = node_rows[-1]
            audit["nodes"].setdefault(node, []).append({"episode": episode, "rows": len(node_rows), "pass": ok})
            audit["all_checks_pass"] &= ok
    if not audit["all_checks_pass"]:
        raise RuntimeError("calibration boot/time lineage failed")
    return episodes, audit


def _initialize_worker(rows, clocks, anchors):
    global _ROWS, _CLOCKS, _ANCHORS
    _ROWS, _CLOCKS, _ANCHORS = rows, clocks, anchors


def _held_worker(task):
    node, held = task
    start = time.perf_counter()
    result = held_link_task(_ROWS, node=node, held=held, clock=_CLOCKS[node],
                            layout_path=LAYOUT, anchors_m=_ANCHORS)
    result["wall_s"] = time.perf_counter() - start
    if result["wall_s"] > 120.0:
        raise TimeoutError("held-link child exceeded 120 seconds")
    return result


def _full_worker(node):
    start = time.perf_counter()
    q, _ = q_from_t4(_ROWS, node=node, held=None, clock=_CLOCKS[node], layout_path=LAYOUT)
    wall = time.perf_counter() - start
    if wall > 120.0:
        raise TimeoutError("T4_FULL child exceeded 120 seconds")
    return {"node": node, "q_accel_full": q.tolist(), "wall_s": wall}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock-table", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    started = time.perf_counter()
    args.output = args.output.resolve()
    args.clock_table = args.clock_table.resolve()
    if args.output.exists() and not args.resume:
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True, exist_ok=args.resume)
    checkpoint_dir = args.output / "HELD_LINK_CHECKPOINTS"
    checkpoint_dir.mkdir(exist_ok=args.resume)

    tasks = [(node, held) for node in NODES for held in range(8)]
    contract = {
        "schema": "biospur.c2.uwb.held_link_checkpoint_contract.v1",
        "clock_owner": str(args.clock_table),
        "clock_owner_sha256": _sha(args.clock_table),
        "source_sha256": {
            "run_calibration.py": _sha(Path(__file__).resolve()),
            "calibration.py": _sha(Path(__file__).with_name("calibration.py")),
        },
        "nodes": list(NODES),
        "tasks": [[node, anchor] for node, anchor in tasks],
        "per_task_hard_limit_s": 120.0,
        "listener_poll_response_consumed": False,
    }
    contract_path = args.output / "RUN_CONTRACT.json"
    if contract_path.exists():
        existing = json.loads(contract_path.read_text())
        if _canonical_json(existing) != _canonical_json(contract):
            raise ValueError("resume contract differs from the existing run")
    else:
        _write_json_atomic(contract_path, contract)

    clocks = _clock_models(args.clock_table)
    bridges = _beacon_boundary_bridges(args.clock_table)
    episodes, lineage = _parse_calibration(clocks, bridges)
    lineage_path = args.output / "CALIBRATION_INPUT_LINEAGE.json"
    if lineage_path.exists():
        if _canonical_json(json.loads(lineage_path.read_text())) != _canonical_json(lineage):
            raise ValueError("resume lineage differs from the existing run")
    else:
        _write_json_atomic(lineage_path, lineage)
    frontend = CanonicalT4Frontend(LAYOUT)
    anchors = np.asarray([[frontend.layout.anchors[i].x_mm, frontend.layout.anchors[i].y_mm,
                           frontend.layout.anchors[i].z_mm] for i in range(8)], float) / 1000.0

    completed: dict[tuple[str, int], dict] = {}
    for path in sorted(checkpoint_dir.glob("*_anchor_*.json")):
        row = json.loads(path.read_text())
        key = (str(row["node"]), int(row["anchor"]))
        if key not in tasks or path != _checkpoint_path(checkpoint_dir, *key):
            raise ValueError(f"unexpected held-link checkpoint: {path}")
        if key in completed:
            raise ValueError(f"duplicate held-link checkpoint: {key}")
        completed[key] = row
    pending = [task for task in tasks if task not in completed]

    context = mp.get_context("fork")
    full_path = args.output / "FULL_Q_ACCEL.json"
    full_result = json.loads(full_path.read_text()) if full_path.exists() else None
    timed_out = False
    with context.Pool(
        args.workers,
        initializer=_initialize_worker,
        initargs=(episodes, clocks, anchors),
    ) as pool:
        if full_result is None:
            remaining = args.timeout - (time.perf_counter() - started)
            if remaining <= 0:
                timed_out = True
            else:
                try:
                    full_result = pool.map_async(
                        _full_worker, list(NODES), chunksize=1,
                    ).get(timeout=min(remaining, 120.0))
                    _write_json_atomic(full_path, full_result)
                except mp.TimeoutError:
                    timed_out = True
        if not timed_out and pending:
            iterator = pool.imap_unordered(_held_worker, pending, chunksize=1)
            for _ in range(len(pending)):
                remaining = args.timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    result = iterator.next(timeout=min(remaining, 120.0))
                except mp.TimeoutError:
                    timed_out = True
                    break
                key = (str(result["node"]), int(result["anchor"]))
                if key in completed:
                    raise RuntimeError(f"worker returned completed task {key}")
                _write_json_atomic(_checkpoint_path(checkpoint_dir, *key), result)
                completed[key] = result
                print(json.dumps({
                    "checkpoint": f"{key[0]}/anchor_{key[1]}",
                    "completed": len(completed),
                    "remaining": len(tasks) - len(completed),
                    "task_wall_s": result["wall_s"],
                    "invocation_wall_s": time.perf_counter() - started,
                }), flush=True)
        if timed_out:
            pool.terminate()

    status = {
        "schema": "biospur.c2.uwb.held_link_checkpoint_status.v1",
        "status": "COMPLETE" if len(completed) == len(tasks) else "CHECKPOINTED_INCOMPLETE",
        "completed_tasks": len(completed),
        "remaining_tasks": len(tasks) - len(completed),
        "invocation_wall_s": time.perf_counter() - started,
        "resume_command_required": len(completed) != len(tasks),
        "no_partial_result_discarded": True,
        "clock_owner_sha256": contract["clock_owner_sha256"],
    }
    _write_json_atomic(args.output / "RUN_STATUS.json", status)
    if len(completed) != len(tasks):
        print(json.dumps(status, indent=2), flush=True)
        return

    results = [completed[task] for task in tasks]
    table = {}
    for task_index, result in enumerate(results):
        key = f"{result['node']}/anchor_{result['anchor']}"
        rng = np.random.Generator(np.random.PCG64(20260903 + task_index))
        table[key] = bias_from_held(result, rng)
        table[key]["q_accel"] = result["q_accel"]
        table[key]["wall_s"] = result["wall_s"]
        new = np.concatenate([np.asarray(x) for x in result["episode_new"].values()])
        old = np.concatenate([np.asarray(x) for x in result["episode_t4"].values()])
        table[key]["paired_new_median_abs_m"] = float(np.median(np.abs(new)))
        table[key]["paired_t4_median_abs_m"] = float(np.median(np.abs(old)))
        table[key]["paired_count"] = int(min(len(new), len(old)))
    complete = all(row["available"] for row in table.values())
    output = {
        "schema": "biospur.c2.uwb.calibration_held_link.v1",
        "status": "COMPLETE" if complete else "INCOMPLETE_LINK_TABLE",
        "calibration_episodes": list(CALIBRATION_ORDER), "tasks": len(tasks),
        "workers": args.workers, "wall_s": time.perf_counter() - started,
        "clock_owner": str(args.clock_table), "clock_owner_sha256": _sha(args.clock_table),
        "clock_quality": "EXACT_R4_CALIBRATION_ONLY_TABLE",
        "q_accel_full": {row["node"]: row for row in full_result},
        "links": table,
    }
    _write_json_atomic(args.output / "HELD_LINK_CALIBRATION.json", output)
    print(json.dumps({key: output[key] for key in ("status", "tasks", "workers", "wall_s")}, indent=2))


if __name__ == "__main__":
    main()
