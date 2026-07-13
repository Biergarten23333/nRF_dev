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
    """Per-anchor base sigma (mm), keyed by anchor id.

    Recognizes a ``default_sigma_mm`` key: the preferred form is a single uniform
    hardware baseline (e.g. {"default_sigma_mm": 25.0}) applied to all 8 anchors,
    with all environment/NLOS adaptation done per-frame by the RF metric in the
    solver -- NOT static per-anchor values baked from one environment. Explicit
    per-anchor label keys (A..H) still override, for back-compat.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    out: dict[int, float] = {}
    if "default_sigma_mm" in data:
        try:
            default = float(data["default_sigma_mm"])
            for aid in range(len(ANCHOR_LABELS)):
                out[aid] = default
        except Exception:
            pass
    for key, value in data.items():
        if key in ("default_sigma_mm", "_comment"):
            continue
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


def load_v5_layout(path: str | Path, anchor_sigma_path: str | Path | None = None) -> Layout:
    """Load a V5 (or V4-IO) anchor_layout.json into the same Layout the host
    solver consumes -- drop-in compatible with load_layout_json.

    Adds two conveniences over load_layout_json:
      1. If anchor_sigma_path is not given, auto-discovers a sibling
         ``anchor_sigma.json`` next to the layout (the overnight-measured
         per-anchor sigma1). This is what feeds effective_sigma in the C core.
      2. Surfaces V5-specific fields into Layout.metadata so callers can act on
         them: common_mode_delay_mm, scale_identifiable, warnings, and per-anchor
         stability_class / differential_delay_mm maps.

    The populated anchor_xyz / d_anchor_mm / sigma_mm arrays are IDENTICAL to
    what load_layout_json produces, so nothing downstream needs to change.
    """
    p = Path(path)
    if anchor_sigma_path is None:
        sibling = p.parent / "anchor_sigma.json"
        if sibling.exists():
            anchor_sigma_path = sibling
    layout = load_layout_json(p, anchor_sigma_path)

    data = json.loads(p.read_text(encoding="utf-8"))
    stability = {}
    differential = {}
    for item in data.get("anchors") or []:
        aid = _anchor_id(item.get("id", item.get("label")))
        if "stability_class" in item:
            stability[aid] = item["stability_class"]
        if "differential_delay_mm" in item:
            differential[aid] = float(item["differential_delay_mm"])
    layout.metadata.update({
        "common_mode_delay_mm": data.get("common_mode_delay_mm"),
        "scale_identifiable": data.get("scale_identifiable", True),
        "warnings": data.get("warnings") or [],
        "stability_class": stability,
        "differential_delay_mm": differential,
        "anchor_sigma_path": str(anchor_sigma_path) if anchor_sigma_path else None,
    })
    return layout

