from __future__ import annotations

import csv
from collections import defaultdict, deque
from pathlib import Path

from .models import Frame, ImuSummary, Observation


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    raw = str(value).strip()
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int_or_zero(value: str | None) -> int:
    val = _float_or_none(value)
    if val is None:
        return 0
    return int(val)


def _imu_summary_from_row(row: dict) -> ImuSummary | None:
    sample_count = _int_or_zero(row.get("imu_n") or row.get("imu_sample_count"))
    std_mg = _float_or_none(row.get("acc_norm_std_mg") or row.get("imu_acc_norm_std_mg"))
    mean_mg = _float_or_none(row.get("acc_norm_mean_mg") or row.get("imu_acc_norm_mean_mg"))
    min_mg = _float_or_none(row.get("acc_norm_min_mg") or row.get("imu_acc_norm_min_mg"))
    max_mg = _float_or_none(row.get("acc_norm_max_mg") or row.get("imu_acc_norm_max_mg"))
    skip_count = _int_or_zero(row.get("imu_skip_count"))
    valid_raw = row.get("imu_valid")
    valid = sample_count > 0 and std_mg is not None
    if valid_raw not in (None, ""):
        valid = valid and bool(_int_or_zero(valid_raw))
    if not valid and sample_count <= 0 and std_mg is None:
        return None
    return ImuSummary(
        sample_count=sample_count,
        acc_norm_mean_mg=mean_mg,
        acc_norm_std_mg=std_mg,
        acc_norm_min_mg=min_mg,
        acc_norm_max_mg=max_mg,
        skip_count=skip_count,
        valid=valid,
    )


def find_tr_all(capture: str | Path) -> Path:
    p = Path(capture)
    if p.is_file():
        return p
    direct = p / "tr_all.csv"
    if direct.exists():
        return direct
    matches = sorted(p.glob("tag_capture*/tr_all.csv"))
    if not matches:
        raise FileNotFoundError(f"no tr_all.csv under {p}")
    return matches[-1]


def read_tr_all_frames(
    path: str | Path,
    tags: set[str] | None = None,
    min_anchors: int = 4,
    tail_rows: int = 0,
) -> list[Frame]:
    p = find_tr_all(path)
    tag_filter = {tag.upper() for tag in tags} if tags else set()
    grouped: dict[tuple[str, int], dict] = {}
    with p.open(newline="", encoding="utf-8", errors="replace") as f:
        if tail_rows > 0:
            header = f.readline()
            reader = csv.DictReader([header, *deque(f, maxlen=tail_rows)])
        else:
            reader = csv.DictReader(f)
        for row in reader:
            try:
                if int(float(row.get("valid", "0") or 0)) != 1:
                    continue
                status = row.get("status") or "O"
                if status not in {"", "O"}:
                    continue
                tag = (row.get("peer_name") or row.get("tag_id") or "unknown").strip().upper()
                if tag_filter and tag not in tag_filter:
                    continue
                aid = int(float(row.get("anchor_id") or -1))
                rng = float(row.get("range_mm") or row.get("raw_mm") or 0.0)
                q = float(row.get("quality_percent") or 0.0)
                sweep = int(float(row.get("sweep") or 0))
                elapsed = float(row.get("host_elapsed_s") or 0.0)
                epoch = float(row.get("host_epoch_s") or elapsed)
            except Exception:
                continue
            if not (0 <= aid < 32 and rng > 0.0):
                continue
            key = (tag, sweep)
            frame = grouped.setdefault(
                key,
                {
                    "tag": tag,
                    "sweep": sweep,
                    "host_elapsed_s": elapsed,
                    "host_epoch_s": epoch,
                    "obs_by_anchor": {},
                    "imu": None,
                },
            )
            frame["host_elapsed_s"] = min(frame["host_elapsed_s"] or elapsed, elapsed)
            frame["host_epoch_s"] = min(frame["host_epoch_s"] or epoch, epoch)
            frame["obs_by_anchor"][aid] = Observation(aid, rng, q, status)
            imu = _imu_summary_from_row(row)
            if imu is not None:
                frame["imu"] = imu
    frames: list[Frame] = []
    for (_tag, _sweep), item in grouped.items():
        obs = tuple(item["obs_by_anchor"][aid] for aid in sorted(item["obs_by_anchor"]))
        if len(obs) < min_anchors:
            continue
        frames.append(
            Frame(
                tag=item["tag"],
                sweep=int(item["sweep"]),
                host_elapsed_s=float(item["host_elapsed_s"]),
                host_epoch_s=float(item["host_epoch_s"]),
                observations=obs,
                imu=item.get("imu"),
            )
        )
    return sorted(frames, key=lambda f: (f.host_epoch_s, f.tag, f.sweep))


def summarize_anchor_counts(frames: list[Frame]) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for frame in frames:
        counts[len(frame.observations)] += 1
    return dict(sorted(counts.items()))
