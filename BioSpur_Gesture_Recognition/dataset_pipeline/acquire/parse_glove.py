from __future__ import annotations

import csv
from typing import Any


GLOVE_COLUMNS = [
    "t_us",
    "thumb_adc",
    "index_adc",
    "middle_adc",
    "ring_adc",
    "pinky_adc",
    "ax_ms2",
    "ay_ms2",
    "az_ms2",
    "gx_rads",
    "gy_rads",
    "gz_rads",
    "temp_c",
]


def is_glove_header(line: str) -> bool:
    fields = [f.strip() for f in line.strip().split(",")]
    return fields[: len(GLOVE_COLUMNS)] == GLOVE_COLUMNS


def parse_glove_csv_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("error,") or is_glove_header(stripped):
        return None

    fields = next(csv.reader([stripped]))
    if len(fields) != len(GLOVE_COLUMNS):
        return None

    parsed: dict[str, Any] = {}
    for name, value in zip(GLOVE_COLUMNS, fields):
        if name.endswith("_adc") or name == "t_us":
            parsed[name] = int(value)
        else:
            parsed[name] = float(value)
    return parsed
