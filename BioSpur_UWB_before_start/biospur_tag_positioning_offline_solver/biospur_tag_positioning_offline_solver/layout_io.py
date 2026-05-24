from __future__ import annotations

import json
from pathlib import Path

from .models import Anchor, Layout

ANCHOR_LABELS = "ABCDEFGH"


def _anchor_id(value) -> int:
    if isinstance(value, str):
        s = value.strip().upper()
        if s in ANCHOR_LABELS:
            return ANCHOR_LABELS.index(s)
    return int(value)


def load_anchor_sigma(path: str | Path | None) -> dict[int, float]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    out: dict[int, float] = {}
    for key, value in data.items():
        try:
            out[_anchor_id(key)] = float(value)
        except Exception:
            continue
    return out


def load_layout_json(path: str | Path, anchor_sigma_path: str | Path | None = None) -> Layout:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    sigma = load_anchor_sigma(anchor_sigma_path)
    anchors: dict[int, Anchor] = {}
    for item in data.get("anchors") or []:
        aid = _anchor_id(item.get("id", item.get("label")))
        label = str(item.get("label") or ANCHOR_LABELS[aid] if 0 <= aid < len(ANCHOR_LABELS) else aid)
        anchors[aid] = Anchor(
            id=aid,
            label=label,
            x_mm=float(item["x_mm"]),
            y_mm=float(item["y_mm"]),
            z_mm=float(item["z_mm"]),
            d_anchor_mm=float(item.get("d_anchor_mm") or 0.0),
            sigma_mm=float(sigma.get(aid, 50.0)),
        )
    if len(anchors) < 4:
        raise ValueError(f"layout has only {len(anchors)} anchors: {p}")
    return Layout(
        path=str(p),
        anchors=anchors,
        tag_delay_mm=float(data.get("tag_delay_mm") or 0.0),
        metadata={
            "version": data.get("version"),
            "label": data.get("label"),
            "solver": data.get("solver"),
            "extra": data.get("extra") or {},
        },
    )

