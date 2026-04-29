#!/usr/bin/env python3
import json
from pathlib import Path


def midpoint(a, b):
    return {
        "x_mm": round((a["x_mm"] + b["x_mm"]) / 2),
        "y_mm": round((a["y_mm"] + b["y_mm"]) / 2),
        "z_mm": round((a["z_mm"] + b["z_mm"]) / 2),
    }


def center(points):
    return {
        "x_mm": round(sum(p["x_mm"] for p in points) / len(points)),
        "y_mm": round(sum(p["y_mm"] for p in points) / len(points)),
        "z_mm": round(sum(p["z_mm"] for p in points) / len(points)),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    layout_path = root / "data" / "anchor_layout_ah_runtime.json"
    output_path = root / "data" / "ground_truth_points_suggested.json"

    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    anchors = {a["label"]: a for a in layout["anchors"]}

    lower = [anchors[label] for label in ("A", "B", "C", "D")]
    upper = [anchors[label] for label in ("E", "F", "G", "H")]
    lower_center = center(lower)
    upper_center = center(upper)
    mid_z = round((upper_center["z_mm"] + lower_center["z_mm"]) / 2)
    high_z = max(0, upper_center["z_mm"] - 250)

    suggested = {
        "coordinate_frame": layout["reference_frame"],
        "source_layout": str(layout_path.relative_to(root)),
        "notes": [
            "Suggested ground-truth points derived automatically from the current runtime anchor layout.",
            "These are intended to reduce manual XYZ planning effort.",
            "Use floor tape, string, or simple measuring tape to approximate these points physically.",
        ],
        "points": [
            {
                "label": "P1_center_floor",
                **lower_center,
                "placement_hint": "Intersection of the two floor diagonals: line A-C and line B-D.",
            },
            {
                "label": "P2_center_mid",
                "x_mm": lower_center["x_mm"],
                "y_mm": lower_center["y_mm"],
                "z_mm": mid_z,
                "placement_hint": "Same XY as the room center, Tag held around the middle between lower and upper planes.",
            },
            {
                "label": "P3_center_high",
                "x_mm": lower_center["x_mm"],
                "y_mm": lower_center["y_mm"],
                "z_mm": high_z,
                "placement_hint": "Same XY as the room center, Tag held clearly below the upper anchors.",
            },
            {
                "label": "P4_AB_mid_floor",
                **midpoint(anchors["A"], anchors["B"]),
                "placement_hint": "Midpoint of floor edge A-B.",
            },
            {
                "label": "P5_BC_mid_floor",
                **midpoint(anchors["B"], anchors["C"]),
                "placement_hint": "Midpoint of floor edge B-C.",
            },
            {
                "label": "P6_CD_mid_floor",
                **midpoint(anchors["C"], anchors["D"]),
                "placement_hint": "Midpoint of floor edge C-D.",
            },
            {
                "label": "P7_DA_mid_floor",
                **midpoint(anchors["D"], anchors["A"]),
                "placement_hint": "Midpoint of floor edge D-A.",
            },
            {
                "label": "P8_under_E_floor",
                "x_mm": anchors["E"]["x_mm"],
                "y_mm": anchors["E"]["y_mm"],
                "z_mm": 0,
                "placement_hint": "Approximate floor projection of upper anchor E.",
            },
            {
                "label": "P9_under_G_floor",
                "x_mm": anchors["G"]["x_mm"],
                "y_mm": anchors["G"]["y_mm"],
                "z_mm": 0,
                "placement_hint": "Approximate floor projection of upper anchor G.",
            },
        ],
    }

    output_path.write_text(
        json.dumps(suggested, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
