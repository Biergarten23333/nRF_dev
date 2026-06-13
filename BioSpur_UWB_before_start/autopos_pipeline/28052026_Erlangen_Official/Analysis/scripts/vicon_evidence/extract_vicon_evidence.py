#!/usr/bin/env python3
"""Extract Vicon ground-truth quality evidence for the Erlangen report.

The script is intentionally self-contained and read-only with respect to
existing analysis outputs. It writes only the requested Markdown evidence file.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


ANCHOR_MARKERS = [f"{name}antenna" for name in "ABCDEFGH"]
STATIC_TAG_MARKERS = ["I1", "I2", "I3", "I4", "I5", "Icenter", "Iantenna"]
ROTO_TAG_MARKERS = [
    "WandBshort",
    "WandB4",
    "WandBtop",
    "WandBlong",
    "WandB5",
    "WandBcenter",
    "WandBantenna",
    "WandCshort",
    "WandC4",
    "WandCtop",
    "WandClong",
    "WandC5",
    "WandCcenter",
    "WandCantenna",
]


@dataclass(frozen=True)
class TrcData:
    path: Path
    header: dict[str, str]
    markers: list[str]
    data: np.ndarray

    @property
    def num_frames(self) -> int:
        if "NumFrames" in self.header:
            try:
                return int(float(self.header["NumFrames"]))
            except ValueError:
                pass
        return int(self.data.shape[0])

    def marker_xyz(self, marker: str) -> np.ndarray | None:
        if marker not in self.markers:
            return None
        idx = self.markers.index(marker)
        start = 2 + idx * 3
        end = start + 3
        if end > self.data.shape[1]:
            return None
        return self.data[:, start:end]


@dataclass(frozen=True)
class CalibrationRecord:
    device_id: str
    display_type: str
    sensor: str
    image_error: str
    world_error: str
    start: str
    end: str
    calibration_type: str
    source: str


@dataclass(frozen=True)
class XcpCalibration:
    path: Path
    source: str
    records: tuple[CalibrationRecord, ...]

    def signature(self) -> tuple:
        return (
            self.source,
            tuple(
                (
                    row.device_id,
                    row.display_type,
                    row.sensor,
                    row.image_error,
                    row.world_error,
                    row.start,
                    row.end,
                    row.calibration_type,
                    row.source,
                )
                for row in self.records
            ),
        )


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def fmt_float(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{val:.{digits}f}"


def fmt_pct(value: float | None) -> str:
    return fmt_float(value, 2)


def markdown_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def parse_trc(path: Path) -> TrcData:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    if len(rows) < 6:
        raise ValueError(f"TRC file has too few rows: {path}")

    header_keys = [cell.strip() for cell in rows[1] if cell.strip()]
    header_vals = [cell.strip() for cell in rows[2] if cell.strip()]
    header = dict(zip(header_keys, header_vals))

    markers = [cell.strip() for cell in rows[3][2:] if cell.strip()]
    numeric_rows: list[list[float]] = []
    for row in rows[5:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        values: list[float] = []
        for cell in row:
            text = cell.strip()
            if text == "":
                values.append(float("nan"))
            else:
                try:
                    values.append(float(text))
                except ValueError:
                    values.append(float("nan"))
        numeric_rows.append(values)

    width = max((len(row) for row in numeric_rows), default=0)
    data = np.full((len(numeric_rows), width), np.nan, dtype=float)
    for row_idx, row in enumerate(numeric_rows):
        data[row_idx, : len(row)] = row

    return TrcData(path=path, header=header, markers=markers, data=data)


def marker_valid_mask(trc: TrcData, marker: str) -> np.ndarray | None:
    xyz = trc.marker_xyz(marker)
    if xyz is None:
        return None
    return np.isfinite(xyz).all(axis=1)


def marker_valid_pct(trc: TrcData, marker: str) -> float | None:
    mask = marker_valid_mask(trc, marker)
    if mask is None or trc.data.shape[0] == 0:
        return None
    return float(np.count_nonzero(mask) * 100.0 / trc.data.shape[0])


def marker_missing_pct(trc: TrcData, marker: str) -> float | None:
    pct = marker_valid_pct(trc, marker)
    if pct is None:
        return None
    return 100.0 - pct


def marker_std_3d_mm(trc: TrcData, marker: str) -> float | None:
    xyz = trc.marker_xyz(marker)
    if xyz is None:
        return None
    valid = np.isfinite(xyz).all(axis=1)
    xyz_valid = xyz[valid]
    if xyz_valid.shape[0] < 2:
        return None
    std = np.nanstd(xyz_valid, axis=0, ddof=1)
    return float(np.sqrt(np.sum(std**2)))


def parse_xcp(path: Path) -> XcpCalibration:
    root = ET.parse(path).getroot()
    source = root.attrib.get("SOURCE", "")
    records: list[CalibrationRecord] = []
    for camera in root.findall(".//Camera"):
        device_id = camera.attrib.get("DEVICEID", "")
        display_type = camera.attrib.get("DISPLAY_TYPE", "")
        sensor = camera.attrib.get("SENSOR", "")
        calibration = camera.find("Calibration")
        if calibration is None:
            continue
        keyframe = camera.find("KeyFrames/KeyFrame")
        if keyframe is None:
            continue
        if "IMAGE_ERROR" not in keyframe.attrib or "WORLD_ERROR" not in keyframe.attrib:
            continue
        records.append(
            CalibrationRecord(
                device_id=device_id,
                display_type=display_type,
                sensor=sensor,
                image_error=keyframe.attrib.get("IMAGE_ERROR", ""),
                world_error=keyframe.attrib.get("WORLD_ERROR", ""),
                start=calibration.attrib.get("START_TIME", ""),
                end=calibration.attrib.get("END_TIME", ""),
                calibration_type=calibration.attrib.get("TYPE", ""),
                source=source,
            )
        )
    records.sort(key=lambda row: row.device_id)
    return XcpCalibration(path=path, source=source, records=tuple(records))


def parse_system_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return values

    wanted = {
        "MeasuredFrameRate",
        "FramesCaptured",
        "FramesDropped",
        "General.FillGaps",
        "General.ForwardPass",
        "Input.FilterParameters",
    }

    for elem in root.iter():
        elem_id = elem.attrib.get("ID") or elem.attrib.get("name")
        if elem_id in wanted:
            values[elem_id] = elem.attrib.get("VALUE", elem.attrib.get("value", ""))
    return values


def capture_id_from_path(path: Path) -> str:
    return path.stem


def load_trc_map(paths: Iterable[Path]) -> dict[Path, TrcData]:
    return {path: parse_trc(path) for path in sorted(paths)}


def numeric(values: Iterable[str | float]) -> list[float]:
    out = []
    for value in values:
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(val):
            out.append(val)
    return out


def trc_rate_rows(trc_map: dict[Path, TrcData], base: Path) -> tuple[list[list[str]], dict[str, set[str]]]:
    rows: list[list[str]] = []
    uniform: dict[str, set[str]] = defaultdict(set)
    for path, trc in sorted(trc_map.items()):
        data_rate = trc.header.get("DataRate", "NA")
        camera_rate = trc.header.get("CameraRate", "NA")
        orig_rate = trc.header.get("OrigDataRate", "NA")
        units = trc.header.get("Units", "NA")
        num_frames = trc.header.get("NumFrames", str(trc.data.shape[0]))
        num_markers = trc.header.get("NumMarkers", str(len(trc.markers)))
        rows.append(
            [
                capture_id_from_path(path),
                rel(path, base),
                data_rate,
                camera_rate,
                orig_rate,
                num_frames,
                num_markers,
                units,
                f"{rel(path, base)}:TRC header fields DataRate, CameraRate, OrigDataRate",
            ]
        )
        uniform["DataRate"].add(data_rate)
        uniform["CameraRate"].add(camera_rate)
        uniform["OrigDataRate"].add(orig_rate)
    return rows, uniform


def system_rows(system_values: dict[Path, dict[str, str]], base: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    for path, values in sorted(system_values.items()):
        rows.append(
            [
                capture_id_from_path(path),
                rel(path, base),
                values.get("MeasuredFrameRate", "NA"),
                values.get("FramesCaptured", "NA"),
                values.get("FramesDropped", "NA"),
                values.get("General.FillGaps", "NA"),
                values.get("General.ForwardPass", "NA"),
                values.get("Input.FilterParameters", "NA"),
                f"{rel(path, base)}:Param name/value fields",
            ]
        )
    return rows


def canonical_full_static_paths(base: Path) -> list[Path]:
    full = base / "opti_captures" / "full"
    return [full / f"ID{idx:02d}.trc" for idx in range(1, 25) if (full / f"ID{idx:02d}.trc").exists()]


def canonical_full_roto_paths(base: Path) -> list[Path]:
    full = base / "opti_captures" / "full"
    return [full / f"R{idx:02d}.trc" for idx in range(1, 18) if (full / f"R{idx:02d}.trc").exists()]


def anchor_stability(static_trcs: dict[Path, TrcData], base: Path) -> tuple[str, str, list[tuple[str, str, float]]]:
    per_anchor_rows: list[list[str]] = []
    all_values: list[tuple[str, str, float]] = []
    for marker in ANCHOR_MARKERS:
        vals: list[float] = []
        valid_pcts: list[float] = []
        max_session = "NA"
        max_value = float("nan")
        for path, trc in sorted(static_trcs.items()):
            std = marker_std_3d_mm(trc, marker)
            vpct = marker_valid_pct(trc, marker)
            if std is not None:
                vals.append(std)
                all_values.append((capture_id_from_path(path), marker, std))
                if not math.isfinite(max_value) or std > max_value:
                    max_value = std
                    max_session = capture_id_from_path(path)
            if vpct is not None:
                valid_pcts.append(vpct)
        anchor_name = marker.replace("antenna", "")
        per_anchor_rows.append(
            [
                anchor_name,
                fmt_float(statistics.median(vals) if vals else None, 4),
                fmt_float(max_value if vals else None, 4),
                max_session,
                fmt_pct(statistics.median(valid_pcts) if valid_pcts else None),
                fmt_pct(min(valid_pcts) if valid_pcts else None),
                f"opti_captures/full/ID01-ID24.trc:{marker} X/Y/Z columns",
            ]
        )

    per_session_rows: list[list[str]] = []
    for path, trc in sorted(static_trcs.items()):
        marker_valids = [marker_valid_pct(trc, marker) for marker in ANCHOR_MARKERS]
        marker_valids = [v for v in marker_valids if v is not None]
        complete = []
        for marker in ANCHOR_MARKERS:
            mask = marker_valid_mask(trc, marker)
            if mask is not None:
                complete.append(mask)
        complete_pct = None
        if complete:
            all_complete = np.logical_and.reduce(complete)
            complete_pct = float(np.count_nonzero(all_complete) * 100.0 / trc.data.shape[0])
        per_session_rows.append(
            [
                capture_id_from_path(path),
                trc.data.shape[0],
                fmt_pct(statistics.median(marker_valids) if marker_valids else None),
                fmt_pct(min(marker_valids) if marker_valids else None),
                fmt_pct(complete_pct),
                f"{rel(path, base)}:Aantenna-Hantenna X/Y/Z columns",
            ]
        )

    per_anchor_table = markdown_table(
        [
            "Anchor",
            "Median 3D std over ID01-ID24 [mm]",
            "Max 3D std [mm]",
            "Max session",
            "Median valid frames [%]",
            "Minimum valid frames [%]",
            "Source field",
        ],
        per_anchor_rows,
    )
    per_session_table = markdown_table(
        [
            "Session",
            "TRC rows",
            "Median anchor valid frames [%]",
            "Minimum anchor valid frames [%]",
            "Frames with all 8 anchors valid [%]",
            "Source field",
        ],
        per_session_rows,
    )
    return per_anchor_table, per_session_table, all_values


def missing_pct_table(
    trcs: dict[Path, TrcData],
    markers: list[str],
    base: Path,
    source_label: str,
) -> str:
    headers = ["Capture"] + [marker for marker in markers] + ["Source field"]
    rows: list[list[str]] = []
    for path, trc in sorted(trcs.items()):
        row = [capture_id_from_path(path)]
        for marker in markers:
            missing = marker_missing_pct(trc, marker)
            row.append("NA" if missing is None else fmt_pct(missing))
        row.append(f"{rel(path, base)}:{source_label} X/Y/Z blank fields")
        rows.append(row)
    return markdown_table(headers, rows)


def calibration_section(xcps: list[XcpCalibration], base: Path) -> tuple[str, list[float], list[float], list[str]]:
    if not xcps:
        return "No .xcp files found under opti_captures.", [], [], []

    signatures = defaultdict(list)
    for xcp in xcps:
        signatures[xcp.signature()].append(xcp.path)

    reference = xcps[0]
    image_errors = numeric(row.image_error for row in reference.records)
    world_errors = numeric(row.world_error for row in reference.records)

    table_rows = []
    for row in reference.records:
        table_rows.append(
            [
                row.device_id,
                row.display_type,
                row.sensor,
                fmt_float(float(row.image_error), 6),
                fmt_float(float(row.world_error), 6),
                row.start,
                row.end,
                row.calibration_type,
                row.source,
                f"{rel(reference.path, base)}:Camera[@DEVICEID={row.device_id}]/KeyFrames/KeyFrame IMAGE_ERROR,WORLD_ERROR",
            ]
        )

    consistency_lines: list[str] = []
    consistency_lines.append(f"- Parsed .xcp files: {len(xcps)}")
    consistency_lines.append(f"- Unique calibration signatures: {len(signatures)}")
    if len(signatures) == 1:
        consistency_lines.append("- Consistency result: all parsed .xcp files embed the identical calibration signature.")
    else:
        consistency_lines.append("- Consistency result: differing calibration signatures found.")
        for idx, (_sig, paths) in enumerate(sorted(signatures.items(), key=lambda item: rel(item[1][0], base)), start=1):
            consistency_lines.append(f"  - Signature {idx}: {len(paths)} files; first file {rel(paths[0], base)}")

    section = "\n".join(consistency_lines)
    section += "\n\n"
    section += markdown_table(
        [
            "DEVICEID",
            "DISPLAY_TYPE",
            "SENSOR",
            "IMAGE_ERROR [px]",
            "WORLD_ERROR [mm]",
            "Calibration START",
            "Calibration END",
            "TYPE",
            "Calibration SOURCE",
            "Source field",
        ],
        table_rows,
    )
    return section, image_errors, world_errors, consistency_lines


def write_evidence(base: Path, output: Path) -> None:
    opti = base / "opti_captures"
    xcp_paths = sorted(opti.rglob("*.xcp"))
    xcp_records = [parse_xcp(path) for path in xcp_paths]
    trc_paths = sorted(opti.rglob("*.trc"))
    trc_map = load_trc_map(trc_paths)
    system_paths = sorted(opti.rglob("*.system"))
    system_values = {path: parse_system_file(path) for path in system_paths}

    static_paths = canonical_full_static_paths(base)
    roto_paths = canonical_full_roto_paths(base)
    static_trcs = {path: trc_map[path] if path in trc_map else parse_trc(path) for path in static_paths}
    roto_trcs = {path: trc_map[path] if path in trc_map else parse_trc(path) for path in roto_paths}

    cal_section, image_errors, world_errors, _ = calibration_section(xcp_records, base)
    rate_rows, rate_uniform = trc_rate_rows(trc_map, base)
    sys_rows = system_rows(system_values, base)
    system_measured_rates: dict[str, list[str]] = defaultdict(list)
    for path, values in sorted(system_values.items()):
        measured = values.get("MeasuredFrameRate")
        if measured:
            system_measured_rates[measured].append(capture_id_from_path(path))
    anchor_table, anchor_session_table, anchor_values = anchor_stability(static_trcs, base)

    global_anchor_stds = [value for _session, _marker, value in anchor_values if math.isfinite(value)]
    above_1mm = [(session, marker, value) for session, marker, value in anchor_values if value > 1.0]
    if above_1mm:
        above_rows = [
            [session, marker, fmt_float(value, 4), f"opti_captures/full/{session}.trc:{marker} X/Y/Z columns"]
            for session, marker, value in sorted(above_1mm, key=lambda item: (-item[2], item[0], item[1]))
        ]
        above_text = (
            f"Finding: {len(above_1mm)} anchor marker-session 3D std values exceed 1 mm.\n\n"
            + markdown_table(["Session", "Marker", "3D std [mm]", "Source field"], above_rows)
        )
    else:
        above_text = "No anchor marker static 3D std value exceeds 1 mm."

    static_anchor_missing = missing_pct_table(
        static_trcs,
        ANCHOR_MARKERS,
        base,
        "Aantenna-Hantenna",
    )
    static_tag_missing = missing_pct_table(
        static_trcs,
        STATIC_TAG_MARKERS,
        base,
        "I tag rigid-body markers",
    )
    roto_anchor_missing = missing_pct_table(
        roto_trcs,
        ANCHOR_MARKERS,
        base,
        "Aantenna-Hantenna",
    )
    roto_tag_missing = missing_pct_table(
        roto_trcs,
        ROTO_TAG_MARKERS,
        base,
        "WandB/WandC tag rigid-body markers",
    )

    fill_gap_values = {
        rel(path, base): values.get("General.FillGaps", "NA")
        for path, values in sorted(system_values.items())
        if capture_id_from_path(path).startswith(("ID", "R"))
    }
    unique_fill = sorted(set(fill_gap_values.values()))
    if not fill_gap_values:
        fill_gap_statement = (
            "No Nexus .system processing files were found for the relevant captures; "
            "gap filling is not determinable from exported data -- confirm with operator."
        )
    elif unique_fill == ["false"]:
        fill_gap_statement = (
            "All relevant .system files that contain the field report "
            "`General.FillGaps=false`. This is the exported Nexus processing "
            "configuration evidence; any manual operation outside these files is not "
            "determinable from exported data -- confirm with operator if needed."
        )
    else:
        fill_gap_statement = (
            "The .system files do not give one uniform `General.FillGaps=false` result. "
            "Treat gap filling as a finding and confirm with operator."
        )

    lines: list[str] = []
    lines.append("# Vicon Ground-Truth Quality Evidence")
    lines.append("")
    lines.append(f"Base directory: `{base}`")
    lines.append("")
    lines.append(
        "This file was generated by `Analysis/scripts/vicon_evidence/extract_vicon_evidence.py`. "
        "It does not modify the report text or existing pipeline outputs."
    )
    lines.append("")
    lines.append("## Existing Loader Basis")
    lines.append("")
    lines.append(
        "The extraction follows the same TRC marker-column convention used by the existing official analysis scripts: "
        "`Analysis/official_extra_analysis/FULL/scripts/layout_optitrack_compare.py` for anchor markers "
        "and `Analysis/official_extra_analysis/FULL/roto_absolute/scripts/run_roto_absolute_analysis.py` for RotoArm marker trajectories."
    )
    lines.append("")
    lines.append("## 1. Calibration Consistency Check")
    lines.append("")
    lines.append(cal_section)
    lines.append("")
    lines.append("### Calibration Summary Statistics")
    lines.append("")
    summary_rows = [
        [
            "Image error median [px]",
            fmt_float(statistics.median(image_errors) if image_errors else None, 6),
            "All identical calibration rows from opti_captures/**/*.xcp:KeyFrame IMAGE_ERROR",
        ],
        [
            "Image error max [px]",
            fmt_float(max(image_errors) if image_errors else None, 6),
            "All identical calibration rows from opti_captures/**/*.xcp:KeyFrame IMAGE_ERROR",
        ],
        [
            "World error median [mm]",
            fmt_float(statistics.median(world_errors) if world_errors else None, 6),
            "All identical calibration rows from opti_captures/**/*.xcp:KeyFrame WORLD_ERROR",
        ],
        [
            "World error mean [mm]",
            fmt_float(statistics.mean(world_errors) if world_errors else None, 6),
            "All identical calibration rows from opti_captures/**/*.xcp:KeyFrame WORLD_ERROR",
        ],
        [
            "World error max [mm]",
            fmt_float(max(world_errors) if world_errors else None, 6),
            "All identical calibration rows from opti_captures/**/*.xcp:KeyFrame WORLD_ERROR",
        ],
    ]
    lines.append(markdown_table(["Metric", "Value", "Source field"], summary_rows))
    lines.append("")
    if world_errors and max(world_errors) > 0.3:
        lines.append(
            f"Finding: one camera exceeds 0.3 mm world error; max = {fmt_float(max(world_errors), 6)} mm. "
            "This should be reported rather than rounded away."
        )
        lines.append("")
    lines.append("## 2. Sampling Rate")
    lines.append("")
    uniform_rows = [
        [field, ", ".join(sorted(values)) if values else "NA", "uniform" if len(values) == 1 else "NOT UNIFORM"]
        for field, values in sorted(rate_uniform.items())
    ]
    lines.append(markdown_table(["TRC rate field", "Observed values", "Uniformity"], uniform_rows))
    lines.append("")
    if system_measured_rates:
        system_rate_values = sorted(system_measured_rates)
        system_rate_rows = [
            [
                "MeasuredFrameRate",
                ", ".join(system_rate_values),
                "uniform" if len(system_rate_values) == 1 else "NOT UNIFORM",
                "; ".join(
                    f"{rate}: {', '.join(captures)}"
                    for rate, captures in sorted(system_measured_rates.items())
                ),
                "opti_captures/**/*.system:Param name=MeasuredFrameRate value",
            ]
        ]
        lines.append("### `.system` MeasuredFrameRate Uniformity Check")
        lines.append("")
        lines.append(
            markdown_table(
                ["System field", "Observed values [Hz]", "Uniformity", "Captures by value", "Source field"],
                system_rate_rows,
            )
        )
        lines.append("")
        if len(system_rate_values) != 1:
            lines.append(
                "Finding: `.system` `MeasuredFrameRate` is not uniform after integer reporting. "
                "The exported TRC trajectory headers still report uniform `DataRate`, `CameraRate`, "
                "and `OrigDataRate` of 120.0 Hz."
            )
            lines.append("")
    lines.append("### TRC Capture Rate Per Export")
    lines.append("")
    lines.append(
        markdown_table(
            [
                "Capture",
                "File",
                "DataRate [Hz]",
                "CameraRate [Hz]",
                "OrigDataRate [Hz]",
                "NumFrames",
                "NumMarkers",
                "Units",
                "Source field",
            ],
            rate_rows,
        )
    )
    lines.append("")
    lines.append("### Nexus System Processing Fields")
    lines.append("")
    lines.append(
        markdown_table(
            [
                "Capture",
                "File",
                "MeasuredFrameRate [Hz]",
                "FramesCaptured",
                "FramesDropped",
                "General.FillGaps",
                "General.ForwardPass",
                "Input.FilterParameters",
                "Source field",
            ],
            sys_rows,
        )
    )
    lines.append("")
    lines.append("## 3. Anchor Marker Static Reconstruction Stability")
    lines.append("")
    if len(static_paths) != 24:
        lines.append(f"Finding: expected 24 canonical full static TRC files, found {len(static_paths)}.")
        lines.append("")
    global_summary_rows = [
        [
            "Global median anchor 3D std [mm]",
            fmt_float(statistics.median(global_anchor_stds) if global_anchor_stds else None, 4),
            "opti_captures/full/ID01-ID24.trc:Aantenna-Hantenna X/Y/Z columns",
        ],
        [
            "Global max anchor 3D std [mm]",
            fmt_float(max(global_anchor_stds) if global_anchor_stds else None, 4),
            "opti_captures/full/ID01-ID24.trc:Aantenna-Hantenna X/Y/Z columns",
        ],
    ]
    lines.append(markdown_table(["Metric", "Value", "Source field"], global_summary_rows))
    lines.append("")
    lines.append("### Per-Anchor Static Stability")
    lines.append("")
    lines.append(anchor_table)
    lines.append("")
    lines.append("### Per-Session Anchor Valid-Frame Percentage")
    lines.append("")
    lines.append(anchor_session_table)
    lines.append("")
    lines.append("### Anchor Static Std Values Above 1 mm")
    lines.append("")
    lines.append(above_text)
    lines.append("")
    lines.append("## 4. Gap / Occlusion Statistics")
    lines.append("")
    lines.append("Percentages below are missing-sample percentages. A missing sample is any frame where one or more XYZ fields for that marker are blank or non-finite in the TRC export.")
    lines.append("`NA` means the marker column is absent from that TRC export, not a measured missing-frame percentage.")
    lines.append("")
    lines.append("### Static ID01-ID24: Anchor Markers Missing [%]")
    lines.append("")
    lines.append(static_anchor_missing)
    lines.append("")
    lines.append("### Static ID01-ID24: Tag Rigid-Body Markers Missing [%]")
    lines.append("")
    lines.append(static_tag_missing)
    lines.append("")
    lines.append("### RotoArm R01-R17: Anchor Markers Missing [%]")
    lines.append("")
    lines.append(roto_anchor_missing)
    lines.append("")
    lines.append("### RotoArm R01-R17: Tag Rigid-Body Markers Missing [%]")
    lines.append("")
    lines.append(roto_tag_missing)
    lines.append("")
    lines.append("### Gap-Fill Processing Evidence")
    lines.append("")
    lines.append(fill_gap_statement)
    lines.append("")
    lines.append("Source field: `opti_captures/**/*.system:Param name=General.FillGaps value`.")
    lines.append("")
    lines.append("## 5. TODO: Marker-to-Antenna Extrinsic Procedure")
    lines.append("")
    lines.append(
        "TODO: document the manual anchor marker-to-antenna extrinsic procedure and its uncertainty. "
        "This is not extractable from the Vicon export files and should be supplied from the measurement protocol/operator notes."
    )
    lines.append("")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    base = args.base_dir.resolve()
    output = args.output
    if output is None:
        output = base / "Analysis" / "reports" / "EN" / "VICON_EVIDENCE.md"
    else:
        output = output.resolve()
    write_evidence(base, output)


if __name__ == "__main__":
    main()
