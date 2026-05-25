#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

ANCHORS = list("ABCDEFGH")

TAG_NOTIFY_PREFIX_RE = r"(?:BLE(?:\[(?P<conn>\d+)(?::[^\]]*)?\])?|BS[0-9A-F]{4}|NUS)"
TR_BCAST_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TR;"
    r"(?P<ver>2);"
    r"(?P<sweep>\d+);"
    r"(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<pmode>\d+);"
    r"(?P<active_mask>[0-9A-Fa-f]+);"
    r"(?P<valid_mask>[0-9A-Fa-f]+);"
    r"(?P<rx_mask>[0-9A-Fa-f]+);"
    r"(?P<raws>-?\d+(?:,-?\d+)*);"
    r"(?P<ranges>\d+(?:,\d+)*);"
    r"(?P<qs>\d+(?:,\d+)*);"
    r"(?P<statuses>[ORTEPL]+)"
    r"(?:;"
    r"(?P<qf>\d+);"
    r"(?P<air_us>\d+);"
    r"(?P<post_us>\d+);"
    r"(?P<cycle_us>\d+);"
    r"(?P<poll_count>\d+)"
    r")?"
)
TR_RANGE_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TR;"
    r"(?P<ver>[1234]);"
    r"(?P<sweep>\d+);"
    r"(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<pmode>\d+);"
    r"(?P<active_mask>[0-9A-Fa-f]+);"
    r"(?P<valid_mask>[0-9A-Fa-f]+);"
    r"(?P<raws>-?\d+(?:,-?\d+)*);"
    r"(?P<ranges>\d+(?:,\d+)*);"
    r"(?P<qs>\d+(?:,\d+)*);"
    r"(?P<statuses>[ORTEPL]+)"
    r"(?:;"
    r"(?P<qf>\d+);"
    r"(?P<first_to_last_us>\d+);"
    r"(?P<frame_us>\d+);"
    r"(?P<poll_count>\d+)"
    r")?"
)
PEER_RE = re.compile(r"(?:\[RECV\]\s+)?(?P<peer>BS[0-9A-F]{4})\s+notify:")


def eprint(msg: str) -> None:
    print(msg, flush=True)


def find_tr_all(capture: Path) -> Path:
    direct = capture / "tr_all.csv"
    if direct.exists():
        return direct
    matches = sorted(capture.glob("tag_capture*/tr_all.csv"))
    if not matches:
        raise SystemExit(f"[error] no tr_all.csv under {capture}")
    return matches[-1]


def find_raw_log(capture: Path) -> Path:
    direct = capture / "raw.log"
    if direct.exists():
        return direct
    matches = sorted(capture.glob("tag_capture*/raw.log"))
    if not matches:
        raise SystemExit(f"[error] no tr_all.csv or raw.log under {capture}")
    return matches[-1]


def find_range_source(capture: Path) -> tuple[Path, str]:
    try:
        return find_tr_all(capture), "tr_all"
    except SystemExit:
        return find_raw_log(capture), "raw_log"


def load_layout(path: Path) -> tuple[dict[int, np.ndarray], dict[int, float], float]:
    data = json.loads(path.read_text())
    layout: dict[int, np.ndarray] = {}
    delays: dict[int, float] = {}
    for item in data.get("anchors") or []:
        aid = int(item["id"])
        layout[aid] = np.asarray(
            [float(item["x_mm"]), float(item["y_mm"]), float(item["z_mm"])],
            dtype=float,
        )
        delays[aid] = float(item.get("d_anchor_mm") or 0.0)
    if len(layout) < 4:
        raise SystemExit(f"[error] layout has only {len(layout)} anchors: {path}")
    return layout, delays, float(data.get("tag_delay_mm") or 0.0)


def load_capture_targets(capture: Path) -> list[str]:
    matches = sorted(capture.glob("tag_capture*/commands.json"))
    if not matches:
        direct = capture / "commands.json"
        matches = [direct] if direct.exists() else []
    if not matches:
        return []
    try:
        data = json.loads(matches[-1].read_text())
        targets = data.get("targets")
        if not isinstance(targets, list):
            return []
        return sorted({str(target).strip().upper() for target in targets if str(target).strip()})
    except Exception:
        return []


def _tag_allowed(peer: str, tag_filter: set[str]) -> bool:
    return not tag_filter or peer in tag_filter


def read_frames(path: Path, tag_filter: set[str], tail_rows: int = 0) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    with path.open(newline="") as f:
        if tail_rows > 0:
            header = f.readline()
            reader = csv.DictReader([header, *deque(f, maxlen=tail_rows)])
        else:
            reader = csv.DictReader(f)
        for row in reader:
            try:
                if int(float(row.get("valid", "0") or 0)) != 1:
                    continue
                if row.get("status") not in ("", "O"):
                    continue
                peer = (row.get("peer_name") or "").strip()
                if not peer or not _tag_allowed(peer, tag_filter):
                    continue
                sweep = int(float(row.get("sweep") or 0))
                aid = int(float(row.get("anchor_id") or -1))
                rng = float(row.get("range_mm") or row.get("raw_mm") or 0)
                if aid < 0 or aid >= len(ANCHORS) or rng <= 0:
                    continue
                epoch = float(row.get("host_epoch_s") or 0.0)
                elapsed = float(row.get("host_elapsed_s") or 0.0)
            except Exception:
                continue
            key = (peer, sweep)
            frame = grouped.setdefault(
                key,
                {
                    "tag": peer,
                    "sweep": sweep,
                    "host_epoch_s": epoch,
                    "host_elapsed_s": elapsed,
                    "ranges": {},
                    "qualities": {},
                },
            )
            frame["host_epoch_s"] = min(frame["host_epoch_s"] or epoch, epoch)
            frame["host_elapsed_s"] = min(frame["host_elapsed_s"] or elapsed, elapsed)
            frame["ranges"][aid] = rng
            try:
                frame["qualities"][aid] = float(row.get("quality_percent") or 0)
            except Exception:
                pass
    return sorted(grouped.values(), key=lambda x: (x["host_epoch_s"], x["tag"], x["sweep"]))


def _peer_from_line(line: str) -> str:
    m = PEER_RE.search(line)
    return m.group("peer") if m else ""


def read_frames_from_raw(path: Path, tag_filter: set[str], tail_rows: int = 0) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    lines: list[str]
    with path.open(encoding="utf-8", errors="replace") as f:
        lines = list(deque(f, maxlen=tail_rows)) if tail_rows > 0 else f.readlines()
    min_sweep: int | None = None
    for line in lines:
        match = TR_BCAST_RE.search(line) or TR_RANGE_RE.search(line)
        if not match:
            continue
        peer = _peer_from_line(line)
        if not peer or not _tag_allowed(peer, tag_filter):
            continue
        try:
            sweep = int(match.group("sweep"))
            raws = [int(v) for v in match.group("raws").split(",")]
            ranges = [int(v) for v in match.group("ranges").split(",")]
            qualities = [int(v) for v in match.group("qs").split(",")]
            statuses = match.group("statuses")
            plan = match.group("plan")
            pmode = int(match.group("pmode"))
        except Exception:
            continue
        min_sweep = sweep if min_sweep is None else min(min_sweep, sweep)
        elapsed = max(0.0, (sweep - min_sweep) / 10.0)
        key = (peer, sweep)
        frame = grouped.setdefault(
            key,
            {
                "tag": peer,
                "sweep": sweep,
                "host_epoch_s": elapsed,
                "host_elapsed_s": elapsed,
                "ranges": {},
                "qualities": {},
            },
        )
        frame["plan"] = plan
        frame["pmode"] = pmode
        for aid, rng in enumerate(ranges[: len(ANCHORS)]):
            raw = raws[aid] if aid < len(raws) else rng
            q = qualities[aid] if aid < len(qualities) else 0
            status = statuses[aid] if aid < len(statuses) else "T"
            if status != "O" or q <= 0 or rng <= 0 or raw <= 0:
                continue
            frame["ranges"][aid] = float(rng)
            frame["qualities"][aid] = float(q)
    return sorted(grouped.values(), key=lambda x: (x["host_epoch_s"], x["tag"], x["sweep"]))


def solve_frame(
    frame: dict[str, Any],
    layout: dict[int, np.ndarray],
    delays: dict[int, float],
    tag_delay: float,
    last: np.ndarray | None,
) -> dict[str, Any] | None:
    valid = [
        (aid, float(rng))
        for aid, rng in frame["ranges"].items()
        if aid in layout and math.isfinite(float(rng)) and float(rng) > 0
    ]
    if len(valid) < 4:
        return None
    anchor_xyz = np.asarray([layout[aid] for aid, _ in valid], dtype=float)
    ranges = np.asarray([rng for _, rng in valid], dtype=float)
    dly = np.asarray([delays.get(aid, 0.0) + tag_delay for aid, _ in valid], dtype=float)
    x = (last.copy() if last is not None else anchor_xyz.mean(axis=0)).astype(float)
    # Small dense Gauss-Newton is much faster than scipy.least_squares for playback.
    # Warm-starting from the previous frame usually converges in a few iterations.
    for _ in range(8):
        diff = x[None, :] - anchor_xyz
        dist = np.linalg.norm(diff, axis=1)
        dist = np.maximum(dist, 1e-6)
        res = dist + dly - ranges
        jac = diff / dist[:, None]
        # Huber-like row weights: keep outliers from dominating without scipy overhead.
        abs_res = np.abs(res)
        weights = np.ones_like(res)
        mask = abs_res > 40.0
        weights[mask] = 40.0 / abs_res[mask]
        jw = jac * weights[:, None]
        rw = res * weights
        try:
            step = np.linalg.solve(jw.T @ jw + np.eye(3) * 1e-3, -(jw.T @ rw))
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(jw, -rw, rcond=None)[0]
        x += step
        if float(np.linalg.norm(step)) < 0.05:
            break
    res = np.linalg.norm(anchor_xyz - x[None, :], axis=1) + dly - ranges
    return {
        "tag": frame["tag"],
        "sweep": int(frame["sweep"]),
        "host_epoch_s": frame["host_epoch_s"],
        "host_elapsed_s": frame["host_elapsed_s"],
        "x_mm": float(x[0]),
        "y_mm": float(x[1]),
        "z_mm": float(x[2]),
        "anchors_used": len(valid),
        "residual_rms_mm": float(np.sqrt(np.mean(res * res))),
        "residual_p95_abs_mm": float(np.percentile(np.abs(res), 95)),
    }


def limit_frames_per_tag(
    frames: list[dict[str, Any]],
    max_frames_per_tag: int,
    tail: bool,
) -> list[dict[str, Any]]:
    if max_frames_per_tag <= 0:
        return frames
    grouped: dict[str, list[dict[str, Any]]] = {}
    for frame in frames:
        grouped.setdefault(str(frame["tag"]), []).append(frame)
    limited: list[dict[str, Any]] = []
    for tag_frames in grouped.values():
        limited.extend(tag_frames[-max_frames_per_tag:] if tail else tag_frames[:max_frames_per_tag])
    return sorted(limited, key=lambda x: (x["host_epoch_s"], x["tag"], x["sweep"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Export solved tag trajectory from a BioSpur tr_all.csv capture.")
    parser.add_argument("--layout", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--tags", help="Comma-separated tag allow-list, for example BSF66F,BS2DCE.")
    parser.add_argument("--max-frames", type=int, default=3000)
    parser.add_argument("--max-frames-per-tag", type=int, default=0)
    parser.add_argument("--tail", action="store_true", help="Keep the newest max-frames instead of the oldest max-frames.")
    parser.add_argument("--tail-rows", type=int, default=0, help="Read only the newest CSV data rows before grouping frames.")
    parser.add_argument("--stride", type=int, default=1)
    args = parser.parse_args()
    tag_filter = {
        tag.strip().upper()
        for raw in [args.tag or "", args.tags or ""]
        for tag in raw.split(",")
        if tag.strip()
    }
    capture_targets = load_capture_targets(args.capture)
    if not tag_filter and capture_targets:
        tag_filter = set(capture_targets)

    range_source, source_kind = find_range_source(args.capture)
    eprint(f"[export] layout={args.layout}")
    eprint(f"[export] capture={args.capture}")
    eprint(f"[export] {source_kind}={range_source}")
    layout, delays, tag_delay = load_layout(args.layout)
    if source_kind == "tr_all":
        frames = read_frames(range_source, tag_filter, args.tail_rows)
    else:
        frames = read_frames_from_raw(range_source, tag_filter, args.tail_rows)
    if args.stride > 1:
        frames = frames[:: args.stride]
    if args.max_frames_per_tag > 0:
        frames = limit_frames_per_tag(frames, args.max_frames_per_tag, args.tail)
    elif args.max_frames > 0:
        frames = frames[-args.max_frames :] if args.tail else frames[: args.max_frames]
    eprint(f"[export] candidate_frames={len(frames)}")

    solved = []
    last_by_tag: dict[str, np.ndarray] = {}
    total = max(1, len(frames))
    for idx, frame in enumerate(frames, start=1):
        if idx == 1 or idx == total or idx % max(1, total // 20) == 0:
            eprint(f"[EXPORT_TRAJ] [{'#' * int(idx / total * 20):<20}] {idx}/{total} solve frames")
        tag = frame["tag"]
        out = solve_frame(frame, layout, delays, tag_delay, last_by_tag.get(tag))
        if out is None:
            continue
        last_by_tag[tag] = np.asarray([out["x_mm"], out["y_mm"], out["z_mm"]], dtype=float)
        solved.append(out)

    tags = sorted({row["tag"] for row in solved})
    expected_tags = sorted(tag_filter) if tag_filter else capture_targets
    candidate_by_tag = {
        tag: sum(1 for frame in frames if frame["tag"] == tag)
        for tag in sorted({frame["tag"] for frame in frames}.union(expected_tags))
    }
    solved_by_tag = {
        tag: sum(1 for row in solved if row["tag"] == tag)
        for tag in sorted(set(tags).union(expected_tags))
    }
    payload = {
        "capture": str(args.capture),
        "tr_all_csv": str(range_source) if source_kind == "tr_all" else "",
        "raw_log": str(range_source) if source_kind == "raw_log" else "",
        "range_source": source_kind,
        "layout": str(args.layout),
        "tag_filter": ",".join(sorted(tag_filter)),
        "expected_tags": expected_tags,
        "candidate_frames": len(frames),
        "solved_frames": len(solved),
        "candidate_frames_by_tag": candidate_by_tag,
        "solved_frames_by_tag": solved_by_tag,
        "tags": tags,
        "time_start_s": min((r["host_elapsed_s"] for r in solved), default=0.0),
        "time_end_s": max((r["host_elapsed_s"] for r in solved), default=0.0),
        "frames": solved,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":")))
    eprint(f"[export] solved_frames={len(solved)} tags={','.join(tags) if tags else '-'}")
    eprint(f"[export] out={args.out}")
    return 0 if solved else 2


if __name__ == "__main__":
    raise SystemExit(main())
