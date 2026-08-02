#!/usr/bin/env python3
"""Convert Fusion-Master host-binary kind-1 records to frozen T4 input.

This module deliberately lives outside the solver freeze.  It only adapts the
wire record into the frozen ``tr_all.csv`` schema and invokes the pristine T4
package without modifying it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from .fusion_host_binary import (
        HostFrame,
        KIND_UWB,
        decode_superframe_flags,
        encode_frame,
    )
    from .sweep_counter_rebase import RebootAwareSweepCounter
except ImportError:
    from fusion_host_binary import (
        HostFrame,
        KIND_UWB,
        decode_superframe_flags,
        encode_frame,
    )
    from sweep_counter_rebase import RebootAwareSweepCounter


FROZEN_T4_ROOT = (
    Path(__file__).resolve().parents[2]
    / "UWB_Part"
    / "2026-07-15-FREEZE"
    / "scripts"
    / "solvers"
    / "erlangen_deployment_v4io_t4"
    / "stage2_position_T4_pristine"
)

TR_FIELDS = (
    "host_elapsed_s",
    "host_epoch_s",
    "sweep",
    "peer_name",
    "tag_id",
    "anchor_id",
    "raw_mm",
    "range_mm",
    "quality_percent",
    "valid",
    "status",
    "master_arrival_ms",
    "host_frame_sequence",
    "node_sequence",
    "identity_code",
    "logical_tag_id",
    "rank",
    "t_round_us",
    "cfo_ppm_q8",
    "valid_mask",
    "flags",
    "superframe_valid",
    "superframe_mod16",
)

EPOCH_FIELDS = (
    "host_elapsed_s",
    "host_epoch_s",
    "master_arrival_ms",
    "host_frame_sequence",
    "node_id",
    "peer_name",
    "record_version",
    "node_sequence",
    "node_uptime_ms",
    "sweep",
    "poll_tx_ts",
    "identity_code",
    "logical_tag_id",
    "guard_us",
    "spacing_us",
    "anchor_ids",
    "ranks",
    "ranges_mm",
    "t_round_us",
    "quality_percent",
    "cfo_ppm_q8",
    "valid_mask",
    "flags",
    "superframe_valid",
    "superframe_mod16",
    "valid_anchor_count",
)


@dataclass(frozen=True)
class RelayedUwb:
    peer_name: str
    node_id: int
    host_frame_sequence: int
    master_arrival_ms: int
    record_version: int
    node_sequence: int
    node_uptime_ms: int
    sweep: int
    poll_tx_ts: int
    identity_code: int
    logical_tag_id: int
    guard_us: int
    spacing_us: int
    anchor_ids: tuple[int, ...]
    ranks: tuple[int, ...]
    ranges_mm: tuple[int, ...]
    t_round_us: tuple[int, ...]
    quality_percent: tuple[int, ...]
    cfo_ppm_q8: tuple[int, ...]
    valid_mask: int
    flags: int

    @property
    def superframe_valid(self) -> bool:
        return decode_superframe_flags(self.flags)[0]

    @property
    def superframe_mod16(self) -> int | None:
        return decode_superframe_flags(self.flags)[1]

    @property
    def valid_anchor_count(self) -> int:
        return sum(
            1
            for index, (anchor_id, distance) in enumerate(
                zip(self.anchor_ids, self.ranges_mm)
            )
            if self.valid_mask & (1 << index)
            and anchor_id != 0xFF
            and 0 < distance < 0xFFFF
        )


def decode_relayed_uwb(frame: HostFrame) -> RelayedUwb:
    """Decode the exact packed bsf_ble_uwb_packet_t inside host kind-1."""
    if frame.kind != KIND_UWB:
        raise ValueError(f"host frame kind {frame.kind} is not UWB")
    if len(frame.payload) != 184:
        raise ValueError(f"kind-1 payload length {len(frame.payload)} != 184")

    version, kind, declared, node_sequence, node_ms = struct.unpack_from(
        "<BBHII", frame.payload, 0
    )
    if kind != 1 or declared != 184:
        raise ValueError(
            f"invalid relayed UWB header kind={kind} declared={declared}"
        )
    body = frame.payload[12:102]
    return RelayedUwb(
        peer_name=frame.node_name,
        node_id=frame.node_id,
        host_frame_sequence=frame.sequence,
        master_arrival_ms=frame.master_arrival_ms,
        record_version=version,
        node_sequence=node_sequence,
        node_uptime_ms=node_ms,
        sweep=struct.unpack_from("<I", body, 0)[0],
        poll_tx_ts=int.from_bytes(body[4:9], "little"),
        identity_code=struct.unpack_from("<H", body, 9)[0],
        logical_tag_id=body[11],
        guard_us=struct.unpack_from("<H", body, 12)[0],
        spacing_us=struct.unpack_from("<H", body, 14)[0],
        anchor_ids=tuple(body[16:24]),
        ranks=tuple(body[24:32]),
        ranges_mm=tuple(struct.unpack_from("<8H", body, 32)),
        t_round_us=tuple(struct.unpack_from("<8H", body, 48)),
        quality_percent=tuple(body[64:72]),
        cfo_ppm_q8=tuple(struct.unpack_from("<8h", body, 72)),
        valid_mask=body[88],
        flags=body[89],
    )


def epoch_row(record: RelayedUwb, first_master_ms: int) -> dict[str, object]:
    elapsed = (record.master_arrival_ms - first_master_ms) / 1000.0
    return {
        "host_elapsed_s": f"{elapsed:.6f}",
        "host_epoch_s": f"{record.master_arrival_ms / 1000.0:.6f}",
        "master_arrival_ms": record.master_arrival_ms,
        "host_frame_sequence": record.host_frame_sequence,
        "node_id": f"{record.node_id:04X}",
        "peer_name": record.peer_name,
        "record_version": record.record_version,
        "node_sequence": record.node_sequence,
        "node_uptime_ms": record.node_uptime_ms,
        "sweep": record.sweep,
        "poll_tx_ts": f"{record.poll_tx_ts:010X}",
        "identity_code": f"{record.identity_code:04X}",
        "logical_tag_id": record.logical_tag_id,
        "guard_us": record.guard_us,
        "spacing_us": record.spacing_us,
        "anchor_ids": ";".join(str(value) for value in record.anchor_ids),
        "ranks": ";".join(str(value) for value in record.ranks),
        "ranges_mm": ";".join(str(value) for value in record.ranges_mm),
        "t_round_us": ";".join(str(value) for value in record.t_round_us),
        "quality_percent": ";".join(
            str(value) for value in record.quality_percent
        ),
        "cfo_ppm_q8": ";".join(str(value) for value in record.cfo_ppm_q8),
        "valid_mask": f"0x{record.valid_mask:02X}",
        "flags": f"0x{record.flags:02X}",
        "superframe_valid": int(record.superframe_valid),
        "superframe_mod16": (
            "" if record.superframe_mod16 is None else record.superframe_mod16
        ),
        "valid_anchor_count": record.valid_anchor_count,
    }


def t4_rows(
    record: RelayedUwb, first_master_ms: int
) -> Iterable[dict[str, object]]:
    elapsed = (record.master_arrival_ms - first_master_ms) / 1000.0
    epoch = record.master_arrival_ms / 1000.0
    for index, anchor_id in enumerate(record.anchor_ids):
        if anchor_id == 0xFF:
            continue
        distance = record.ranges_mm[index]
        usable = bool(
            record.valid_mask & (1 << index) and 0 < distance < 0xFFFF
        )
        yield {
            "host_elapsed_s": f"{elapsed:.6f}",
            "host_epoch_s": f"{epoch:.6f}",
            "sweep": record.sweep,
            "peer_name": record.peer_name,
            "tag_id": record.peer_name,
            "anchor_id": anchor_id,
            "raw_mm": distance,
            "range_mm": distance,
            "quality_percent": record.quality_percent[index],
            "valid": int(usable),
            "status": "O" if usable else "E",
            "master_arrival_ms": record.master_arrival_ms,
            "host_frame_sequence": record.host_frame_sequence,
            "node_sequence": record.node_sequence,
            "identity_code": f"{record.identity_code:04X}",
            "logical_tag_id": record.logical_tag_id,
            "rank": record.ranks[index],
            "t_round_us": record.t_round_us[index],
            "cfo_ppm_q8": record.cfo_ppm_q8[index],
            "valid_mask": f"0x{record.valid_mask:02X}",
            "flags": f"0x{record.flags:02X}",
            "superframe_valid": int(record.superframe_valid),
            "superframe_mod16": (
                "" if record.superframe_mod16 is None else record.superframe_mod16
            ),
        }


class RelayedUwbArchive:
    """Streaming archive plus the preregistered per-node validity histogram."""

    def __init__(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.epochs_path = output_dir / "relayed_uwb_epochs.csv"
        self.tr_path = output_dir / "tr_all.csv"
        self.binary_path = output_dir / "host_kind1.cobs"
        self._epochs_file = self.epochs_path.open(
            "w", newline="", encoding="utf-8"
        )
        self._tr_file = self.tr_path.open("w", newline="", encoding="utf-8")
        self._binary_file = self.binary_path.open("wb")
        self._epochs = csv.DictWriter(
            self._epochs_file, fieldnames=EPOCH_FIELDS
        )
        self._tr = csv.DictWriter(self._tr_file, fieldnames=TR_FIELDS)
        self._epochs.writeheader()
        self._tr.writeheader()
        self.first_master_ms: int | None = None
        self.histograms: dict[str, Counter[int]] = defaultdict(Counter)
        self.total_records: Counter[str] = Counter()
        self.records_with_valid: Counter[str] = Counter()
        self.sweep_counter = RebootAwareSweepCounter()

    def note_tag_boot_or_join(self, name: str, reason: str) -> None:
        """Attach an external OTA/boot fact to the next decoded tag record."""
        self.sweep_counter.note_tag_boot_or_join(name, reason)

    def observe_host_frame(self, frame: HostFrame) -> None:
        if frame.kind != KIND_UWB:
            return
        record = decode_relayed_uwb(frame)
        self.sweep_counter.observe(
            record.peer_name, record.sweep, record.node_uptime_ms
        )
        self._binary_file.write(encode_frame(frame))
        self._binary_file.flush()
        if self.first_master_ms is None:
            self.first_master_ms = record.master_arrival_ms
        self._epochs.writerow(epoch_row(record, self.first_master_ms))
        self._tr.writerows(t4_rows(record, self.first_master_ms))
        self._epochs_file.flush()
        self._tr_file.flush()
        count = record.valid_anchor_count
        self.histograms[record.peer_name][count] += 1
        self.total_records[record.peer_name] += 1
        if count:
            self.records_with_valid[record.peer_name] += 1

    def snapshot(self, expected_nodes: Iterable[str] = ()) -> dict[str, object]:
        nodes = sorted(set(expected_nodes) | set(self.total_records))
        result: dict[str, object] = {}
        for node in nodes:
            total = self.total_records[node]
            nonzero = self.records_with_valid[node]
            result[node] = {
                "records": total,
                "records_with_at_least_one_valid": nonzero,
                "fraction_with_at_least_one_valid": (
                    nonzero / total if total else 0.0
                ),
                "valid_anchor_count_histogram": {
                    str(key): self.histograms[node][key]
                    for key in range(9)
                },
                "sweep_counter": self.sweep_counter.snapshot().get(
                    node, {}
                ),
            }
        return result

    def close(self) -> None:
        self._epochs_file.close()
        self._tr_file.close()
        self._binary_file.close()


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _load_epoch_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            counts[row["peer_name"].upper()] += 1
    return counts


def run_frozen_t4(
    layout_path: Path,
    tr_path: Path,
    epochs_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    """Run one pristine T4 state per tag and summarize static clusters."""
    sys.path.insert(0, str(FROZEN_T4_ROOT))
    try:
        from biospur_tag_positioning_offline_solver.capture_io import (
            read_tr_all_frames,
        )
        from biospur_tag_positioning_offline_solver.c_solver import (
            TagPositionSolver,
        )
        from biospur_tag_positioning_offline_solver.layout_io import (
            load_layout_json,
        )
        from biospur_tag_positioning_offline_solver.models import SolverConfig
    finally:
        sys.path.pop(0)

    layout = load_layout_json(layout_path)
    frames = read_tr_all_frames(tr_path, min_anchors=4)
    by_tag: dict[str, list] = defaultdict(list)
    for frame in frames:
        by_tag[frame.tag.upper()].append(frame)
    epoch_counts = _load_epoch_counts(epochs_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    solved_path = output_dir / "t4_solved_frames.csv"
    solved_fields = (
        "tag",
        "sweep",
        "host_epoch_s",
        "x_mm",
        "y_mm",
        "z_mm",
        "anchors_input",
        "anchors_used",
        "rejected_anchor_id",
        "residual_rms_mm",
        "residual_p95_abs_mm",
        "max_abs_residual_mm",
    )
    solved_rows: list[dict[str, object]] = []
    per_tag: dict[str, object] = {}
    timed_points: dict[str, list[tuple[float, tuple[float, float, float]]]] = {}

    for tag in sorted(set(epoch_counts) | set(by_tag)):
        solver = TagPositionSolver(layout, SolverConfig(method="T4"))
        points: list[tuple[float, float, float]] = []
        times: list[float] = []
        residuals: list[float] = []
        input_frames = sorted(
            by_tag.get(tag, []), key=lambda item: (item.host_epoch_s, item.sweep)
        )
        for frame in input_frames:
            result = solver.solve_frame(frame)
            if result is None:
                continue
            xyz = (float(result.x_mm), float(result.y_mm), float(result.z_mm))
            if not all(math.isfinite(value) for value in xyz):
                continue
            points.append(xyz)
            times.append(float(result.host_epoch_s))
            residuals.append(float(result.residual_rms_mm))
            solved_rows.append(
                {
                    "tag": tag,
                    "sweep": result.sweep,
                    "host_epoch_s": f"{result.host_epoch_s:.6f}",
                    "x_mm": result.x_mm,
                    "y_mm": result.y_mm,
                    "z_mm": result.z_mm,
                    "anchors_input": result.anchors_input,
                    "anchors_used": result.anchors_used,
                    "rejected_anchor_id": (
                        ""
                        if result.rejected_anchor_id is None
                        else result.rejected_anchor_id
                    ),
                    "residual_rms_mm": result.residual_rms_mm,
                    "residual_p95_abs_mm": result.residual_p95_abs_mm,
                    "max_abs_residual_mm": result.max_abs_residual_mm,
                }
            )
        if not points:
            per_tag[tag] = {
                "epochs_captured": epoch_counts[tag],
                "frames_with_at_least_4_valid_anchors": len(input_frames),
                "frames_solved": 0,
                "fraction_epochs_solvable": 0.0,
                "cluster": None,
            }
            timed_points[tag] = []
            continue

        columns = list(zip(*points))
        mean = tuple(statistics.fmean(column) for column in columns)
        median = tuple(statistics.median(column) for column in columns)
        std = tuple(statistics.pstdev(column) for column in columns)
        scatter = math.sqrt(
            statistics.fmean(
                sum((point[index] - mean[index]) ** 2 for index in range(3))
                for point in points
            )
        )
        per_tag[tag] = {
            "epochs_captured": epoch_counts[tag],
            "frames_with_at_least_4_valid_anchors": len(input_frames),
            "frames_solved": len(points),
            "fraction_epochs_solvable": (
                len(input_frames) / epoch_counts[tag] if epoch_counts[tag] else 0.0
            ),
            "fraction_solver_success_given_min4": (
                len(points) / len(input_frames) if input_frames else 0.0
            ),
            "cluster": {
                "mean_xyz_mm": [round(value, 3) for value in mean],
                "median_xyz_mm": [round(value, 3) for value in median],
                "axis_std_mm": [round(value, 3) for value in std],
                "rms_3d_scatter_mm": round(scatter, 3),
            },
            "solve_residual_rms_mm": {
                "median": round(statistics.median(residuals), 3),
                "p95": round(_percentile(residuals, 0.95) or 0.0, 3),
                "maximum": round(max(residuals), 3),
            },
        }
        timed_points[tag] = list(zip(times, points))

    with solved_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=solved_fields)
        writer.writeheader()
        writer.writerows(solved_rows)

    pairwise: dict[str, object] = {}
    tags = sorted(timed_points)
    for left_index, left in enumerate(tags):
        for right in tags[left_index + 1 :]:
            right_rows = timed_points[right]
            distances: list[float] = []
            right_cursor = 0
            for timestamp, point in timed_points[left]:
                while (
                    right_cursor + 1 < len(right_rows)
                    and abs(right_rows[right_cursor + 1][0] - timestamp)
                    <= abs(right_rows[right_cursor][0] - timestamp)
                ):
                    right_cursor += 1
                if not right_rows:
                    break
                other_time, other = right_rows[right_cursor]
                if abs(other_time - timestamp) > 0.075:
                    continue
                distances.append(
                    math.sqrt(
                        sum((point[index] - other[index]) ** 2 for index in range(3))
                    )
                )
            pairwise[f"{left}__{right}"] = {
                "matched_epochs_within_75ms": len(distances),
                "distance_mean_mm": (
                    round(statistics.fmean(distances), 3) if distances else None
                ),
                "distance_std_mm": (
                    round(statistics.pstdev(distances), 3)
                    if len(distances) > 1
                    else (0.0 if distances else None)
                ),
            }

    anchor_xyz = [
        (anchor.x_mm, anchor.y_mm, anchor.z_mm)
        for anchor in layout.anchors.values()
    ]
    lower = [min(point[index] for point in anchor_xyz) - 1000.0 for index in range(3)]
    upper = [max(point[index] for point in anchor_xyz) + 1000.0 for index in range(3)]
    all_clusters_present = len(per_tag) == 5 and all(
        row.get("cluster") is not None for row in per_tag.values()
    )
    inside_bounds = all_clusters_present and all(
        all(
            lower[index] <= row["cluster"]["mean_xyz_mm"][index] <= upper[index]
            for index in range(3)
        )
        for row in per_tag.values()
    )
    nonoverlap = True
    for pair, stats in pairwise.items():
        left, right = pair.split("__", 1)
        left_cluster = per_tag[left].get("cluster")
        right_cluster = per_tag[right].get("cluster")
        if not left_cluster or not right_cluster:
            nonoverlap = False
            continue
        left_mean = left_cluster["mean_xyz_mm"]
        right_mean = right_cluster["mean_xyz_mm"]
        center_distance = math.sqrt(
            sum(
                (left_mean[index] - right_mean[index]) ** 2
                for index in range(3)
            )
        )
        stats["cluster_center_distance_mm"] = round(center_distance, 3)
        stats["combined_rms_scatter_mm"] = round(
            left_cluster["rms_3d_scatter_mm"]
            + right_cluster["rms_3d_scatter_mm"],
            3,
        )
        stats["one_rms_clusters_nonoverlap"] = bool(
            center_distance > stats["combined_rms_scatter_mm"]
        )
        nonoverlap = nonoverlap and stats["one_rms_clusters_nonoverlap"]

    result = {
        "solver": "frozen pristine T4, SolverConfig(method='T4')",
        "frozen_t4_root": str(FROZEN_T4_ROOT),
        "layout": str(layout_path),
        "tr_all": str(tr_path),
        "epochs": str(epochs_path),
        "per_tag": per_tag,
        "pairwise": pairwise,
        "sanity": {
            "five_clusters_present": all_clusters_present,
            "one_rms_clusters_nonoverlap": nonoverlap,
            "plausible_bounds_definition": (
                "axis-aligned anchor-layout bounds padded by 1000 mm"
            ),
            "plausible_bounds_mm": {"lower": lower, "upper": upper},
            "all_cluster_means_inside_plausible_bounds": inside_bounds,
        },
        "solved_frames_csv": str(solved_path),
    }
    (output_dir / "t4_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", required=True, type=Path)
    parser.add_argument("--tr-all", required=True, type=Path)
    parser.add_argument("--epochs", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_frozen_t4(
        args.layout, args.tr_all, args.epochs, args.output_dir
    )
    print(
        "T4_COMPLETE "
        f"tags={len(result['per_tag'])} "
        f"five_clusters={int(result['sanity']['five_clusters_present'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
