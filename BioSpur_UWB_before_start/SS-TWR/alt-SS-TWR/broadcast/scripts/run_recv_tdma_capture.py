#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import serial
from serial import SerialException

from master_control_port import assert_not_jlink_when_biospur_available


TAG_NOTIFY_PREFIX_RE = r"(?:BLE(?:\[(?P<conn>\d+)(?::[^\]]*)?\])?|BS[0-9A-F]{4}|NUS)"

TR_SINGLE_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TR;"
    r"(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<anchor>\d+);"
    r"(?P<raw>-?\d+);"
    r"(?P<range>\d+);"
    r"(?P<q>\d+);"
    r"(?P<valid>[01]);"
    r"(?P<status>[ORTEP]);"
    r"(?P<pmode>\d+)"
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
    r"(?P<statuses>[ORTEP]+)"
    r"(?:;"
    r"(?P<qf>\d+);"
    r"(?P<first_to_last_us>\d+);"
    r"(?P<frame_us>\d+);"
    r"(?P<poll_count>\d+)"
    r")?"
    r"(?:;D(?P<tr_diag_version>\d+),(?P<tr_diag_b64>[A-Za-z0-9+/=]+))?"
)

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
    r"(?:;D(?P<tr_diag_version>\d+),(?P<tr_diag_b64>[A-Za-z0-9+/=]+))?"
)

RFD_RE = re.compile(
    rf"(?:{TAG_NOTIFY_PREFIX_RE} notify:\s*)?"
    r"RFD;(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<poll_seq>\d+);"
    r"(?P<anchor_id>\d+);"
    r"(?P<raw_mm>-?\d+);"
    r"(?P<resp_rx_ts>\d+);"
    r"(?P<carrier_integrator>-?\d+);"
    r"(?P<anchor_flags>\d+);"
    r"(?P<anchor_fp_index>\d+);"
    r"(?P<anchor_fp1>\d+);"
    r"(?P<anchor_fp2>\d+);"
    r"(?P<anchor_fp3>\d+);"
    r"(?P<anchor_cir_pwr>\d+);"
    r"(?P<anchor_rxpacc>\d+);"
    r"(?P<anchor_std_noise>\d+);"
    r"(?P<tag_flags>\d+);"
    r"(?P<tag_fp_index>\d+);"
    r"(?P<tag_fp1>\d+);"
    r"(?P<tag_fp2>\d+);"
    r"(?P<tag_fp3>\d+);"
    r"(?P<tag_cir_pwr>\d+);"
    r"(?P<tag_rxpacc>\d+);"
    r"(?P<tag_std_noise>\d+)"
)

RANGE_ACTIVITY_RE = re.compile(r"\b(?:TR|RFD|CM|CR|CF|CS);")
CIR_MODE_CHOICES = ("off", "compact", "full")

# Backwards-compatible aliases for older helper code and text checks.
TR_RE = TR_RANGE_RE
TR2_RE = TR_RANGE_RE

TR_IMU_TRAILER_RE = re.compile(
    r";I(?:MU)?[,;]"
    r"(?P<imu_n>\d+)[,;]"
    r"(?P<acc_norm_mean_mg>-?\d+)[,;]"
    r"(?P<acc_norm_std_mg>-?\d+)[,;]"
    r"(?P<acc_norm_min_mg>-?\d+)[,;]"
    r"(?P<acc_norm_max_mg>-?\d+)"
    r"(?:[,;](?P<imu_skip_count>\d+))?$"
)

CONNECTED_RE = re.compile(
    r"Connected\[(?P<conn>\d+)\]:.*?(?:name=(?P<name>[^\s]+))?.*?(?:bs=(?P<bs>BS[0-9A-F]{4}))?.*?tag_id=(?P<tag_id>-?\d+)"
)

CFG_ASSIGNED_RE = re.compile(
    r"CFG assigned\[(?P<conn>\d+)\]: bs=(?P<bs>BS[0-9A-F]{4}) tag=(?P<tag_id>\d+)"
    r".*?pmode=(?P<pmode>\d+)"
)

CFG_ASSIGNED_DETAIL_RE = re.compile(
    r"CFG assigned\[(?P<conn>\d+)\]: bs=(?P<bs>BS[0-9A-F]{4}) "
    r"tag=(?P<tag_id>\d+) slot=(?P<slot>\d+)/(?P<count>\d+) "
    r"mask=0x(?P<mask>[0-9A-Fa-f]+) period=(?P<period>\d+) "
    r"active=(?P<active>\d+)(?: active_us=(?P<active_us>\d+))? "
    r"gen=(?P<gen>\d+) pmode=(?P<pmode>\d+)"
)

CFG_OK_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: CFG_OK TAG=(?P<tag_id>\d+) "
    r"SLOT=(?P<slot>\d+)/(?P<count>\d+) MASK=0x(?P<mask>[0-9A-Fa-f]+) "
    r"PERIOD=(?P<period>\d+) ACTIVE=(?P<active>\d+)(?: ACTIVE_US=(?P<active_us>\d+))? GEN=(?P<gen>\d+) "
    r"LIVE=(?P<live>\d+)"
)

TDMA_WEIGHTED_RE = re.compile(
    r"TDMA weighted\[\d+\]: bs=(?P<bs>BS[0-9A-F]{4}) profile=(?P<profile>\w+) "
    r"target=(?P<target_hz>\d+)Hz mask=0x(?P<mask>[0-9A-Fa-f]+) "
    r"slots=(?P<slots>\d+)/(?P<count>\d+) actual_x100=(?P<actual_x100>\d+)"
)

TDMA_SHOW_PROFILE_RE = re.compile(
    r"TDMA profile: (?P<bs>BS[0-9A-F]{4}) -> (?P<profile>\w+) target=(?P<target_hz>\d+)Hz"
)

def extract_bs_name(text: str) -> str:
    match = re.search(r"\b(BS[0-9A-F]{4})\b", text)
    return match.group(1) if match else ""


def line_has_range_activity(line: str) -> bool:
    return bool(RANGE_ACTIVITY_RE.search(line))


TR_RF_DIAG_COMPACT_RECORD_LEN = 8


def decode_tr_rf_diag_records(match: re.Match, active_anchors: list[int]) -> dict[int, dict]:
    version_text = match.groupdict().get("tr_diag_version")
    payload_text = match.groupdict().get("tr_diag_b64")
    if not version_text or not payload_text:
        return {}

    try:
        version = int(version_text)
        payload = base64.b64decode(payload_text, validate=True)
    except (ValueError, binascii.Error):
        return {}

    records: dict[int, dict] = {}
    for pos, anchor_id in enumerate(active_anchors):
        offset = pos * TR_RF_DIAG_COMPACT_RECORD_LEN
        if offset + TR_RF_DIAG_COMPACT_RECORD_LEN > len(payload):
            break
        record = payload[offset : offset + TR_RF_DIAG_COMPACT_RECORD_LEN]
        anchor_flags = record[0]
        tag_flags = record[1]
        records[anchor_id] = {
            "diag_source": "tr_compact",
            "tr_diag_version": version,
            "anchor_diag_valid": 1 if (anchor_flags & 0x01) else 0,
            "anchor_diag_flags": anchor_flags,
            "anchor_fp_sum_q8": record[2],
            "anchor_cir_pwr_q8": record[3],
            "anchor_rxpacc_q8": record[4],
            "tag_diag_valid": 1 if (tag_flags & 0x01) else 0,
            "tag_diag_flags": tag_flags,
            "tag_fp_sum_q8": record[5],
            "tag_cir_pwr_q8": record[6],
            "tag_rxpacc_q8": record[7],
        }
    return records


def q8_from_u16(value: int) -> int:
    return max(0, min(255, (int(value) + 128) // 256))


def q8_saturate(value: int) -> int:
    return max(0, min(255, int(value)))


def iter_tr_matches(text: str):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith("TR;"):
            fragment = (prefix or "BLE notify: ") + fragment

        match = TR_SINGLE_RE.search(fragment)
        if match:
            yield match


def iter_tr_records(text: str):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith("TR;"):
            fragment = (prefix or "BLE notify: ") + fragment

        imu_fields = extract_imu_trailer(fragment)
        fragment = imu_fields.pop("_fragment", fragment)

        match = TR_SINGLE_RE.search(fragment)
        if match:
            yield {
                "sweep": int(match.group("sweep")),
                "plan": match.group("plan"),
                "pmode": int(match.group("pmode")),
                "anchor_id": int(match.group("anchor")),
                "raw_mm": int(match.group("raw")),
                "range_mm": int(match.group("range")),
                "quality_percent": int(match.group("q")),
                "valid": int(match.group("valid")),
                "status": match.group("status"),
                **imu_fields,
            }
            continue

        match = TR_BCAST_RE.search(fragment)
        if match:
            tr_version = int(match.group("ver"))
            active_mask = int(match.group("active_mask"), 16)
            valid_mask = int(match.group("valid_mask"), 16)
            rx_mask = int(match.group("rx_mask"), 16)
            raws = [int(v) for v in match.group("raws").split(",")]
            ranges = [int(v) for v in match.group("ranges").split(",")]
            qualities = [int(v) for v in match.group("qs").split(",")]
            statuses = list(match.group("statuses"))
            active_anchors = [anchor_id for anchor_id in range(8)
                              if active_mask & (1 << anchor_id)]
            diag_by_anchor = decode_tr_rf_diag_records(match, active_anchors)
            count = min(len(active_anchors), len(raws), len(ranges),
                        len(qualities), len(statuses))
            for pos in range(count):
                anchor_id = active_anchors[pos]
                status = statuses[pos]
                range_mm = ranges[pos]
                # TR2 is a debug/broadcast diagnostic record.  Its valid_mask
                # can lag or represent the diagnostic mask, while the per-anchor
                # status field is the ranging success contract.
                effective_valid = (
                    (valid_mask & (1 << anchor_id)) != 0
                    or (tr_version == 2 and status == "O" and range_mm > 100)
                )
                yield {
                    "sweep": int(match.group("sweep")),
                    "plan": match.group("plan"),
                    "pmode": int(match.group("pmode")),
                    "anchor_id": anchor_id,
                    "raw_mm": raws[pos],
                    "range_mm": range_mm,
                    "quality_percent": qualities[pos],
                    "valid": 1 if effective_valid else 0,
                    "status": status,
                    "quality_flag_percent": int(match.group("qf") or 0),
                    "first_to_last_us": int(match.group("air_us") or 0),
                    "frame_us": int(match.group("post_us") or 0),
                    "poll_count": int(match.group("poll_count") or 0),
                    "tr_version": tr_version,
                    "rx_mask": f"{rx_mask:02x}",
                    "air_us": int(match.group("air_us") or 0),
                    "post_us": int(match.group("post_us") or 0),
                    "cycle_us": int(match.group("cycle_us") or 0),
                    "rx_seen": 1 if (rx_mask & (1 << anchor_id)) else 0,
                    **diag_by_anchor.get(anchor_id, {}),
                    **imu_fields,
                }
            continue

        match = TR_RANGE_RE.search(fragment)
        if not match:
            continue

        tr_version = int(match.group("ver"))
        active_mask = int(match.group("active_mask"), 16)
        valid_mask = int(match.group("valid_mask"), 16)
        raws = [int(v) for v in match.group("raws").split(",")]
        ranges = [int(v) for v in match.group("ranges").split(",")]
        qualities = [int(v) for v in match.group("qs").split(",")]
        statuses = list(match.group("statuses"))
        active_anchors = [anchor_id for anchor_id in range(8)
                          if active_mask & (1 << anchor_id)]
        diag_by_anchor = decode_tr_rf_diag_records(match, active_anchors)
        count = min(len(active_anchors), len(raws), len(ranges),
                    len(qualities), len(statuses))
        for pos in range(count):
            anchor_id = active_anchors[pos]
            status = statuses[pos]
            range_mm = ranges[pos]
            # TR1/TR2 short records are legacy/debug output.  Some older
            # images did not keep valid_mask aligned with per-anchor status.
            effective_valid = (
                (valid_mask & (1 << anchor_id)) != 0
                or (tr_version in {1, 2} and status == "O" and range_mm > 100)
            )
            yield {
                "sweep": int(match.group("sweep")),
                "plan": match.group("plan"),
                "pmode": int(match.group("pmode")),
                "anchor_id": anchor_id,
                "raw_mm": raws[pos],
                "range_mm": range_mm,
                "quality_percent": qualities[pos],
                "valid": 1 if effective_valid else 0,
                "status": status,
                "quality_flag_percent": int(match.group("qf") or 0),
                "first_to_last_us": int(match.group("first_to_last_us") or 0),
                "frame_us": int(match.group("frame_us") or 0),
                "poll_count": int(match.group("poll_count") or 0),
                "tr_version": tr_version,
                "rx_mask": "",
                "air_us": "",
                "post_us": "",
                "cycle_us": "",
                "rx_seen": "",
                **diag_by_anchor.get(anchor_id, {}),
                **imu_fields,
            }


def iter_rfd_records(text: str):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith("RFD;"):
            fragment = (prefix or "BLE notify: ") + fragment

        match = RFD_RE.search(fragment)
        if not match:
            continue

        anchor_flags = int(match.group("anchor_flags"))
        tag_flags = int(match.group("tag_flags"))
        anchor_fp1 = int(match.group("anchor_fp1"))
        anchor_fp2 = int(match.group("anchor_fp2"))
        anchor_fp3 = int(match.group("anchor_fp3"))
        tag_fp1 = int(match.group("tag_fp1"))
        tag_fp2 = int(match.group("tag_fp2"))
        tag_fp3 = int(match.group("tag_fp3"))
        anchor_fp_sum = anchor_fp1 + anchor_fp2 + anchor_fp3
        tag_fp_sum = tag_fp1 + tag_fp2 + tag_fp3
        anchor_cir_pwr = int(match.group("anchor_cir_pwr"))
        anchor_rxpacc = int(match.group("anchor_rxpacc"))
        tag_cir_pwr = int(match.group("tag_cir_pwr"))
        tag_rxpacc = int(match.group("tag_rxpacc"))
        yield {
            "diag_source": "rfd_legacy",
            "tr_diag_version": "",
            "rfd_version": int(match.group("ver")),
            "sweep": int(match.group("sweep")),
            "poll_seq": int(match.group("poll_seq")),
            "anchor_id": int(match.group("anchor_id")),
            "raw_mm": int(match.group("raw_mm")),
            "resp_rx_ts": int(match.group("resp_rx_ts")),
            "carrier_integrator": int(match.group("carrier_integrator")),
            "anchor_diag_valid": 1 if (anchor_flags & 0x01) else 0,
            "anchor_diag_flags": anchor_flags,
            "anchor_fp_index": int(match.group("anchor_fp_index")),
            "anchor_fp1": anchor_fp1,
            "anchor_fp2": anchor_fp2,
            "anchor_fp3": anchor_fp3,
            "anchor_fp_sum": anchor_fp_sum,
            "anchor_fp_sum_q8": q8_from_u16(anchor_fp_sum),
            "anchor_cir_pwr": anchor_cir_pwr,
            "anchor_cir_pwr_q8": q8_from_u16(anchor_cir_pwr),
            "anchor_rxpacc": anchor_rxpacc,
            "anchor_rxpacc_q8": q8_saturate(anchor_rxpacc),
            "anchor_std_noise": int(match.group("anchor_std_noise")),
            "tag_diag_valid": 1 if (tag_flags & 0x01) else 0,
            "tag_diag_flags": tag_flags,
            "tag_fp_index": int(match.group("tag_fp_index")),
            "tag_fp1": tag_fp1,
            "tag_fp2": tag_fp2,
            "tag_fp3": tag_fp3,
            "tag_fp_sum": tag_fp_sum,
            "tag_fp_sum_q8": q8_from_u16(tag_fp_sum),
            "tag_cir_pwr": tag_cir_pwr,
            "tag_cir_pwr_q8": q8_from_u16(tag_cir_pwr),
            "tag_rxpacc": tag_rxpacc,
            "tag_rxpacc_q8": q8_saturate(tag_rxpacc),
            "tag_std_noise": int(match.group("tag_std_noise")),
        }


def extract_imu_trailer(fragment: str) -> dict:
    """Parse optional TRv4 IMU summary trailer and return the stripped fragment."""
    match = TR_IMU_TRAILER_RE.search(fragment)
    if not match:
        return {"_fragment": fragment}
    fields = {
        "_fragment": fragment[: match.start()],
        "imu_valid": 1,
        "imu_n": int(match.group("imu_n")),
        "acc_norm_mean_mg": int(match.group("acc_norm_mean_mg")),
        "acc_norm_std_mg": int(match.group("acc_norm_std_mg")),
        "acc_norm_min_mg": int(match.group("acc_norm_min_mg")),
        "acc_norm_max_mg": int(match.group("acc_norm_max_mg")),
        "imu_skip_count": int(match.group("imu_skip_count") or 0),
    }
    return fields


def normalize_target(name: str) -> str:
    raw = name.strip()
    value = raw.upper()
    if value.startswith("BS"):
        return value
    raise ValueError(f"Invalid target name: {name}")


def target_aliases(name: str) -> set[str]:
    value = name.strip().upper()
    return {target_bs_name(value)}


def target_bs_name(name: str) -> str:
    return name.strip().upper()


DEFAULT_KNOWN_BS_TAGS = [
    "BSF66F",
    "BS2DCE",
    "BSDC91",
    "BS9336",
    "BS955A",
    "BSCCF4",
]


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def default_full_cir_script_path() -> str:
    repo_root = Path(__file__).resolve().parents[4]
    return str(repo_root / "flutter_ui_autopos" / "scripts" / "cir_full_usb_capture.py")


def tag_cir_range_phase(requested_mode: str) -> str:
    mode = str(requested_mode or "off").strip().lower()
    return "off" if mode == "full" else mode


def parse_bs_tag_csv(value: str) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for item in value.split(","):
        raw = item.strip().upper()
        if not raw:
            continue
        tag = target_bs_name(normalize_target(raw))
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def target_discovery_prefix(targets: list[str]) -> str:
    return "BS"


def open_serial_with_retry(port: str, baud: int, timeout_s: float = 0.2, retries: int = 240) -> serial.Serial:
    last_exc = None
    for _ in range(retries):
        try:
            return serial.Serial(port, baud, timeout=timeout_s, write_timeout=2)
        except (SerialException, OSError) as exc:
            last_exc = exc
            time.sleep(0.25)
    raise last_exc if last_exc is not None else RuntimeError("serial open failed")


def drain_serial_until(ser: serial.Serial, logf, wait_s: float) -> str:
    deadline = time.time() + wait_s
    chunks: list[str] = []
    while time.time() < deadline:
        try:
            data = ser.read(ser.in_waiting or 1)
        except (SerialException, OSError):
            raise
        if not data:
            continue
        text = data.decode("utf-8", errors="replace")
        chunks.append(text)
        logf.write(text)
        logf.flush()
    return "".join(chunks)


def send_cmd(ser: serial.Serial, logf, cmd: str, wait_s: float) -> serial.Serial:
    print(f"[HOST_CMD] {cmd}", flush=True)
    logf.write(f"[HOST_CMD {time.monotonic():.3f}] {cmd}\n")
    logf.flush()
    try:
        written = ser.write((cmd + "\n").encode("utf-8"))
        if written <= 0:
            raise SerialException(f"serial write returned {written}")
    except (serial.SerialTimeoutException, SerialException, OSError) as exc:
        logf.write(
            f"[HOST_WARN {time.monotonic():.3f}] write failed for {cmd!r}: {exc}; reopen/retry\n"
        )
        logf.flush()
        port = ser.port
        baud = ser.baudrate
        try:
            ser.close()
        except Exception:
            pass
        ser = open_serial_with_retry(port, baud)
        time.sleep(1.0)
        drain_serial_until(ser, logf, 1.0)
        written = ser.write((cmd + "\n").encode("utf-8"))
        if written <= 0:
            raise SerialException(f"serial retry write returned {written}")
    try:
        drain_serial_until(ser, logf, wait_s)
        return ser
    except (SerialException, OSError):
        try:
            ser.close()
        except Exception:
            pass
        last_exc = None
        for _ in range(12):
            try:
                time.sleep(0.75)
                ser = open_serial_with_retry(ser.port, ser.baudrate)
                drain_serial_until(ser, logf, max(wait_s, 2.5))
                logf.write(
                    f"[HOST_WARN {time.monotonic():.3f}] read failed after {cmd!r}; reopened serial and continued\n"
                )
                logf.flush()
                return ser
            except (SerialException, OSError) as exc:
                last_exc = exc
                try:
                    ser.close()
                except Exception:
                    pass
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("mode recv reopen failed")


def send_cmd_collect(ser: serial.Serial, logf, cmd: str, wait_s: float) -> tuple[serial.Serial, str]:
    print(f"[HOST_CMD] {cmd}", flush=True)
    logf.write(f"[HOST_CMD {time.monotonic():.3f}] {cmd}\n")
    logf.flush()
    try:
        written = ser.write((cmd + "\n").encode("utf-8"))
        if written <= 0:
            raise SerialException(f"serial write returned {written}")
    except (serial.SerialTimeoutException, SerialException, OSError) as exc:
        logf.write(
            f"[HOST_WARN {time.monotonic():.3f}] write failed for {cmd!r}: {exc}; reopen/retry\n"
        )
        logf.flush()
        port = ser.port
        baud = ser.baudrate
        try:
            ser.close()
        except Exception:
            pass
        ser = open_serial_with_retry(port, baud)
        time.sleep(1.0)
        drain_serial_until(ser, logf, 1.0)
        written = ser.write((cmd + "\n").encode("utf-8"))
        if written <= 0:
            raise SerialException(f"serial retry write returned {written}")
    try:
        return ser, drain_serial_until(ser, logf, wait_s)
    except (SerialException, OSError):
        try:
            ser.close()
        except Exception:
            pass
        last_exc = None
        port = ser.port
        baud = ser.baudrate
        for _ in range(12):
            try:
                time.sleep(0.75)
                ser = open_serial_with_retry(port, baud)
                text = drain_serial_until(ser, logf, max(wait_s, 2.5))
                logf.write(
                    f"[HOST_WARN {time.monotonic():.3f}] read failed after {cmd!r}; reopened serial and continued\n"
                )
                logf.flush()
                return ser, text
            except (SerialException, OSError) as exc:
                last_exc = exc
                try:
                    ser.close()
                except Exception:
                    pass
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("command reopen failed")


def reset_controller_via_jlink(logf, snr: str) -> bool:
    snr = (snr or "").strip()
    if not snr:
        return False

    ok = True
    for core in ("NET", "APP"):
        cmd_path = Path(f"/tmp/biospur_b120_reset_{snr}_{core.lower()}.jlink")
        cmd_path.write_text(
            "\n".join(
                [
                    f"Device NRF5340_XXAA_{core}",
                    "SI SWD",
                    "Speed 4000",
                    "r",
                    "g",
                    "q",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        logf.write(
            f"[HOST_RECOVERY {time.monotonic():.3f}] J-Link reset B120 {core} snr={snr}\n"
        )
        logf.flush()
        cp = subprocess.run(
            [
                "JLinkExe",
                "-NoGui",
                "1",
                "-SelectEmuBySN",
                snr,
                "-CommanderScript",
                str(cmd_path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        logf.write(cp.stdout)
        logf.write(
            f"[HOST_RECOVERY {time.monotonic():.3f}] J-Link reset {core} rc={cp.returncode}\n"
        )
        logf.flush()
        ok = ok and (cp.returncode == 0)
    return ok


def recover_controller_serial(args, logf, reason: str) -> serial.Serial | None:
    """Recover the B120 CDC port after USB disappears during capture."""
    logf.write(f"[HOST_RECOVERY {time.monotonic():.3f}] serial recovery start: {reason}\n")
    logf.flush()
    try:
        ser = open_serial_with_retry(args.port, args.baud, retries=40)
        logf.write(f"[HOST_RECOVERY {time.monotonic():.3f}] serial reopened without reset\n")
        logf.flush()
        return ser
    except (SerialException, OSError) as exc:
        logf.write(f"[HOST_RECOVERY {time.monotonic():.3f}] reopen failed before reset: {exc}\n")
        logf.flush()

    if args.controller_reset_snr == "-":
        logf.write(f"[HOST_RECOVERY {time.monotonic():.3f}] controller reset disabled\n")
        logf.flush()
        return None

    if not reset_controller_via_jlink(logf, args.controller_reset_snr):
        return None

    time.sleep(2.0)
    try:
        ser = open_serial_with_retry(args.port, args.baud, retries=160)
        logf.write(f"[HOST_RECOVERY {time.monotonic():.3f}] serial reopened after J-Link reset\n")
        logf.flush()
        return ser
    except (SerialException, OSError) as exc:
        logf.write(f"[HOST_RECOVERY {time.monotonic():.3f}] reopen failed after reset: {exc}\n")
        logf.flush()
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare master RECV tag session, configure TDMA, and capture multi-tag logs."
    )
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00",
        help="Master control serial port",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--controller-reset-snr",
        default=os.environ.get("B120_SNR", "960148546"),
        help="B120 J-Link SNR used for hard recovery when BLE central links get stuck. Use '-' to disable.",
    )
    parser.add_argument("--duration", type=float, default=120.0, help="Capture duration in seconds")
    parser.add_argument(
        "--targets",
        default="BSF66F,BS2DCE,BSDC91",
        help="Comma-separated BS names to collect",
    )
    parser.add_argument(
        "--tr-hz",
        "--hz",
        dest="tr_hz",
        type=int,
        default=None,
        help="Unified broadcast TR rate per target.",
    )
    parser.add_argument(
        "--out-dir",
        default="logs/recv_tdma_capture",
        help="Base output directory. By default a timestamped sibling directory is created.",
    )
    parser.add_argument(
        "--out-dir-exact",
        action="store_true",
        help="Use --out-dir itself as the session directory instead of creating a timestamped sibling.",
    )
    parser.add_argument(
        "--skip-anchor-preflight",
        action="store_true",
        help="Skip 8/8 runtime responder verification before tag capture.",
    )
    parser.add_argument(
        "--anchor-preflight-port",
        default="",
        help="Optional separate Master_Anchor serial port for dual-master responder preflight/repair.",
    )
    parser.add_argument(
        "--anchor-preflight-timeout-s",
        type=float,
        default=30.0,
        help="Per-attempt anchor runtime responder command timeout.",
    )
    parser.add_argument(
        "--anchor-preflight-retries",
        type=int,
        default=3,
        help="Runtime responder verification attempts before aborting capture.",
    )
    parser.add_argument(
        "--anchor-preflight-launch-retries",
        type=int,
        default=2,
        help="Rerun the whole responder preflight if a controller reboot window causes a failed launch.",
    )
    parser.add_argument(
        "--anchor-responder-settle-s",
        type=float,
        default=10.0,
        help="Seconds to wait after a successful runtime responder ack before starting tag polling.",
    )
    parser.add_argument(
        "--reuse-tag-links",
        action="store_true",
        help="Assume a Master_Tag boot profile is already maintaining BS* links; do not force mode/device reconnect unless recovery is needed.",
    )
    parser.add_argument(
        "--known-bs-tags",
        default=",".join(DEFAULT_KNOWN_BS_TAGS),
        help=(
            "Comma-separated BS tag identities known in the field kit. Tags in this list "
            "are used only for optional legacy non-target idle handling."
        ),
    )
    parser.add_argument(
        "--no-silence-non-target-tags",
        dest="no_silence_non_target_tags",
        action="store_true",
        default=True,
        help=(
            "Disable optional pre-capture MODE IDLE for known BS tags that are not in "
            "the requested TDMA roster. This is the default because capture scenes "
            "are not firmware positioning modes."
        ),
    )
    parser.add_argument(
        "--silence-non-target-tags",
        dest="no_silence_non_target_tags",
        action="store_false",
        help=(
            "Opt in to pre-capture non-target MODE IDLE. Use only when the "
            "controller is known to have separate live links to non-target Tags."
        ),
    )
    parser.add_argument(
        "--non-target-silence-settle-s",
        type=float,
        default=1.0,
        help="Seconds to drain serial after targeted non-target idle commands.",
    )
    parser.add_argument(
        "--tag-link-timeout-s",
        type=float,
        default=120.0,
        help="Seconds to wait for all target tag links before recovery.",
    )
    parser.add_argument(
        "--tag-link-stable-s",
        type=float,
        default=8.0,
        help="Seconds to keep all requested tag links connected before TDMA release.",
    )
    parser.add_argument(
        "--tdma-config-retries",
        type=int,
        default=3,
        help="TDMA CFG apply/check attempts before capture starts.",
    )
    parser.add_argument(
        "--tdma-profile",
        choices=["motion"],
        default="motion",
        help="Deprecated compatibility option. Capture scenes always use motion TDMA/PMODE=0.",
    )
    parser.add_argument(
        "--tag-cir",
        choices=CIR_MODE_CHOICES,
        default="off",
        help="Runtime CIR output requested from target Tags: off, compact, or full.",
    )
    parser.add_argument(
        "--legacy-no-touch-tags",
        action="store_true",
        help=(
            "Do not send pre-capture/final cmd_all MODE IDLE or cmd_all CIR OFF/COMPACT/FULL. "
            "Use only for reproducing legacy high-throughput captures where the Tag runtime "
            "state must be left untouched before TDMA release."
        ),
    )
    parser.add_argument(
        "--legacy-keep-tdma-state",
        action="store_true",
        help=(
            "Do not clear the Master_Tag TDMA roster/state during setup. This is only "
            "for reproducing known-good legacy captures where resident links are already "
            "streaming and tdma clear disrupts admission."
        ),
    )
    parser.add_argument(
        "--legacy-skip-link-ready-wait",
        action="store_true",
        help=(
            "Do not block setup waiting for every target link before TDMA release. "
            "This matches legacy captures that released TDMA from resident links and "
            "judged success from actual TR output."
        ),
    )
    parser.add_argument(
        "--skip-initial-mode-idle",
        action="store_true",
        help="Skip the initial pre-setup cmd_all MODE IDLE while still allowing other setup commands.",
    )
    parser.add_argument(
        "--skip-final-mode-idle",
        action="store_true",
        help="Skip the final pre-release cmd_all MODE IDLE while still allowing other setup commands.",
    )
    parser.add_argument(
        "--skip-target-cir-command",
        action="store_true",
        help="Skip runtime cmd_all CIR <mode> before capture; useful when measuring ranging-only stability.",
    )
    parser.add_argument(
        "--full-cir-duration-s",
        type=float,
        default=env_float("BIOSPUR_FULL_CIR_DURATION_S", 30.0),
        help="Seconds to run the deferred full-CIR USB phase after range capture.",
    )
    parser.add_argument(
        "--full-cir-ports",
        default=os.environ.get("BIOSPUR_CIR_FULL_USB_PORTS", ""),
        help="Comma-separated LABEL=/dev/... USB CDC ports for deferred full-CIR capture.",
    )
    parser.add_argument(
        "--full-cir-script",
        default=os.environ.get("BIOSPUR_CIR_FULL_SCRIPT", default_full_cir_script_path()),
        help="USB full-CIR capture helper script.",
    )
    parser.add_argument(
        "--full-cir-anchor-control-port",
        default=os.environ.get("BIOSPUR_ANCHOR_PORT", ""),
        help="Master_Anchor CDC used to switch anchor CIR during the deferred full-CIR phase.",
    )
    parser.add_argument(
        "--allow-legacy-tdma-show-only",
        action="store_true",
        help=(
            "Allow capture to start when legacy Tags do not emit CFG_ASSIGNED/CFG_OK, "
            "provided tdma show reports every requested BS target/profile."
        ),
    )
    parser.add_argument(
        "--allow-scheduler-actual-hz-below-request",
        action="store_true",
        help=(
            "Allow TDMA CFG verify to pass when the Master scheduler reports an "
            "actual rate below the requested target Hz. Default is strict."
        ),
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Leave tags running after capture. By default tags are returned to IDLE mode.",
    )
    parser.add_argument(
        "--no-tr-timeout-s",
        type=float,
        default=15.0,
        help="Abort capture early if no TR row is seen this many seconds after TDMA release. Use 0 to disable.",
    )
    return parser


def effective_tr_hz(args) -> int:
    return int(args.tr_hz if args.tr_hz is not None else 10)


def expected_tdma_maps(args, targets: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    tr_hz = effective_tr_hz(args)
    expected_pmode = 0
    expected_pmode_by_target = {target: expected_pmode for target in targets}
    expected_freq_by_target = {target: tr_hz for target in targets}
    for target in targets:
        for alias in target_aliases(target):
            expected_pmode_by_target[alias] = expected_pmode
            expected_freq_by_target[alias] = tr_hz
    return expected_pmode_by_target, expected_freq_by_target


def tdma_roster_profile(args: argparse.Namespace | None = None) -> str:
    _ = args
    return "motion"


def write_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def diag_join_key(row: dict) -> tuple[str, str, int, int] | None:
    try:
        sweep = int(row["sweep"])
        anchor_id = int(row["anchor_id"])
    except (KeyError, TypeError, ValueError):
        return None

    peer = str(row.get("peer_name") or "")
    tag_id = str(row.get("tag_id") or "")
    if not peer and not tag_id:
        return None
    return (peer, tag_id, sweep, anchor_id)


def build_range_diag_joined_rows(
    tr_rows: list[dict],
    rfd_rows: list[dict],
    tr_fields: list[str],
    rfd_join_fields: list[tuple[str, str]],
) -> list[dict]:
    rfd_by_key: dict[tuple[str, str, int, int], dict] = {}
    for row in rfd_rows:
        key = diag_join_key(row)
        if key is not None:
            rfd_by_key[key] = row

    joined_rows: list[dict] = []
    for tr in tr_rows:
        out = {field: tr.get(field, "") for field in tr_fields}
        rfd = rfd_by_key.get(diag_join_key(tr))
        tr_diag_available = bool(tr.get("diag_source"))
        out["rfd_joined"] = 1 if rfd or tr_diag_available else 0
        for out_field, source_field in rfd_join_fields:
            if rfd:
                out[out_field] = rfd.get(source_field, "")
            elif tr_diag_available and not out_field.startswith("rfd_"):
                out[out_field] = tr.get(source_field, "")
            else:
                out[out_field] = ""
        joined_rows.append(out)
    return joined_rows


def tag_id_fallbacks_from_tdma_config(tdma_config_check: dict) -> dict[str, int]:
    tag_ids: dict[str, int] = {}
    for target, info in (tdma_config_check.get("per_target") or {}).items():
        if not isinstance(info, dict):
            continue
        bs = str(info.get("bs") or target_bs_name(str(target))).upper()
        record = info.get("actual") or info.get("expected") or {}
        if not isinstance(record, dict):
            continue
        try:
            tag_id = int(record["tag_id"])
        except (KeyError, TypeError, ValueError):
            continue
        for alias in target_aliases(str(target)):
            tag_ids[alias] = tag_id
        if bs:
            tag_ids[bs] = tag_id
    return tag_ids


def backfill_tag_ids(rows: list[dict], tag_id_by_peer: dict[str, int]) -> None:
    for row in rows:
        if row.get("tag_id") not in (None, ""):
            continue
        peer = str(row.get("peer_name") or "").upper()
        if not peer:
            continue
        tag_id = tag_id_by_peer.get(peer)
        if tag_id is not None:
            row["tag_id"] = tag_id


def summarize_sweep_validity(rows: list[dict]) -> dict:
    """Summarize whether each tag sweep saw enough valid anchor responses."""
    by_sweep: dict[tuple[str, int], set[int]] = defaultdict(set)
    for row in rows:
        if not int(row.get("valid") or 0):
            continue
        try:
            peer = str(row.get("peer_name") or row.get("bs") or row.get("tag_id") or "")
            sweep = int(row["sweep"])
            anchor_id = int(row["anchor_id"])
        except (KeyError, TypeError, ValueError):
            continue
        by_sweep[(peer, sweep)].add(anchor_id)

    valid_counts = [len(anchor_ids) for anchor_ids in by_sweep.values()]
    all_sweeps: set[tuple[str, int]] = set()
    for row in rows:
        try:
            peer = str(row.get("peer_name") or row.get("bs") or row.get("tag_id") or "")
            all_sweeps.add((peer, int(row["sweep"])))
        except (KeyError, TypeError, ValueError):
            continue
    total_sweeps = len(all_sweeps)
    distribution = {str(count): valid_counts.count(count) for count in range(9)}
    ge4 = sum(1 for count in valid_counts if count >= 4)
    ge7 = sum(1 for count in valid_counts if count >= 7)
    ge8 = sum(1 for count in valid_counts if count >= 8)
    return {
        "sweeps_total": total_sweeps,
        "sweeps_with_any_valid": len(by_sweep),
        "sweeps_ge4": ge4,
        "sweeps_ge7": ge7,
        "sweeps_ge8": ge8,
        "ratio_ge4": round(ge4 / total_sweeps, 6) if total_sweeps else 0.0,
        "ratio_ge7": round(ge7 / total_sweeps, 6) if total_sweeps else 0.0,
        "ratio_ge8": round(ge8 / total_sweeps, 6) if total_sweeps else 0.0,
        "valid_count_distribution": distribution,
    }


def run_anchor_responder_preflight(args, session_dir: Path) -> dict:
    print("[CAPTURE] anchor preflight: require 8/8 runtime responder ack", flush=True)
    attempts = []
    launch_retries = max(1, int(args.anchor_preflight_launch_retries))
    result: dict = {"success": False, "error": "preflight_not_run"}
    preflight_port = getattr(args, "anchor_preflight_port", "") or args.port

    for launch in range(1, launch_retries + 1):
        preflight_base = session_dir / f"anchor_responder_preflight_launch{launch}"
        preflight_log = session_dir / f"anchor_responder_preflight_launch{launch}.console.log"
        preflight_script = Path(__file__).resolve().parent / "verify_all_anchor_responder_runtime.py"
        cmd = [
            sys.executable,
            str(preflight_script),
            "--port",
            preflight_port,
            "--live-output",
            "--command-timeout-s",
            str(args.anchor_preflight_timeout_s),
            "--retry-count",
            str(args.anchor_preflight_retries),
            "--out-dir",
            str(preflight_base),
        ]
        print(
            f"[CAPTURE] anchor preflight launch {launch}/{launch_retries}: "
            f"log={preflight_log}",
            flush=True,
        )
        output_parts: list[str] = []
        with preflight_log.open("w", encoding="utf-8") as pf:
            proc = subprocess.Popen(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                output_parts.append(line)
                pf.write(line)
                pf.flush()
                print(line, end="", flush=True)
            returncode = proc.wait()
        stdout = "".join(output_parts)

        result = {
            "success": False,
            "returncode": returncode,
            "console_log": str(preflight_log),
            "launch": launch,
        }
        json_match = re.search(r"(\{\s*\"success\".*\})\s*$", stdout, re.S)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                result.update(parsed)
            except json.JSONDecodeError as exc:
                result["error"] = f"preflight_json_parse_failed: {exc}"
        else:
            result["error"] = "preflight_json_not_found"
        attempts.append(dict(result))
        if result.get("success"):
            break
        print(
            f"[CAPTURE] anchor preflight launch {launch}/{launch_retries} failed; retrying whole preflight",
            flush=True,
        )
        time.sleep(3.0)

    result["launch_attempts"] = attempts
    return result


def settle_after_anchor_preflight(args, reason: str) -> None:
    settle_s = max(0.0, float(getattr(args, "anchor_responder_settle_s", 0.0)))
    if settle_s <= 0:
        return
    print(
        f"[CAPTURE] anchor responder settle after {reason}: {settle_s:.1f}s",
        flush=True,
    )
    time.sleep(settle_s)


def ensure_target_links_ready(
    ser: serial.Serial,
    logf,
    targets: list[str],
    controller_reset_snr: str = "",
    wait_per_target_s: float = 120.0,
    stable_s: float = 8.0,
    initial_text: str = "",
    discovery_prefix_override: str = "",
) -> serial.Serial:
    ready_targets: set[str] = set()
    hard_reset_used = False
    discovery_prefix = discovery_prefix_override or target_discovery_prefix(targets)

    def mark_ready_from_text(text: str) -> None:
        text_u = text.upper()
        for item in targets:
            for alias in target_aliases(item):
                if (
                    f"{alias} NOTIFY:" in text_u
                    or (f"CFG_OK" in text_u and alias in text_u)
                    or (f"CONNECTED" in text_u and f"BS={alias}" in text_u)
                    or (f"CFG ASSIGNED" in text_u and f"BS={alias}" in text_u)
                    or (f"TDMA WEIGHTED" in text_u and f"BS={alias}" in text_u)
                ):
                    ready_targets.add(item.upper())
                    break

    if initial_text:
        mark_ready_from_text(initial_text)

    def passive_wait(label: str, wait_s: float) -> bool:
        # `device kind tag` starts scan/connect/GATT setup by itself. Avoid
        # pushing more UART commands during that busy window; internal-oscillator
        # B120s expose CDC write starvation when host retries overlap BLE setup.
        deadline = time.time() + wait_s
        last_progress_print = 0.0
        all_ready_since: float | None = None
        print(
            f"[CAPTURE] link setup passive {label}: wait up to {wait_s:.0f}s",
            flush=True,
        )
        while time.time() < deadline:
            burst = drain_serial_until_capture(ser, logf, 1.0)
            if burst:
                mark_ready_from_text(burst)
            now = time.time()
            if now - last_progress_print >= 5.0:
                last_progress_print = now
                print(
                    "[CAPTURE] link setup passive: ready="
                    + ",".join(sorted(ready_targets) or ["-"])
                    + f" ({len(ready_targets)}/{len(targets)})",
                    flush=True,
                )
            all_ready = all(target.upper() in ready_targets for target in targets)
            if all_ready and all_ready_since is None:
                all_ready_since = now
                print(
                    f"[CAPTURE] link setup passive: all {len(targets)}/{len(targets)} ready; settle {stable_s:.1f}s before TDMA release",
                    flush=True,
                )
            if all_ready and (now - (all_ready_since or now)) >= stable_s:
                return True
        return all(target.upper() in ready_targets for target in targets) and all_ready_since is not None

    def ensure_tag_kind(reason: str) -> None:
        nonlocal ser
        ser, device_text = send_cmd_collect(ser, logf, "device show", 0.8)
        if "kind=tag" in device_text.lower():
            logf.write(
                f"[HOST_INFO {time.monotonic():.3f}] device_kind_tag_skip reason={reason} already_tag=1\n"
            )
            logf.flush()
            return
        ser = send_cmd(ser, logf, "device kind tag", 2.0)

    def exact_target_enrollment(pass_idx: int, wait_s: float) -> bool:
        nonlocal ser
        missing = [
            target for target in targets
            if target.upper() not in ready_targets
        ]
        if not missing:
            return True
        print(
            "[CAPTURE] link setup exact enrollment pass "
            f"{pass_idx}: missing={','.join(missing)}",
            flush=True,
        )
        if len(targets) > 1:
            # Do not narrow the runtime target filter to a single missing tag
            # during multi-tag captures.  The Master disconnects connected
            # peers that no longer match the active filter, so exact-enrolling
            # BSDC91 can drop an already-ready BS2DCE and collapse 2/2 -> 1/2
            # -> 0/2.  Keep the broad capture prefix and let conn/scan recover
            # missing peers without evicting ready ones.
            ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
            ser = send_cmd(ser, logf, "ota_target name -", 0.5)
            ser = send_cmd(ser, logf, f"ota_target prefix {discovery_prefix}", 0.5)
            ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
            ensure_tag_kind(f"multi-target-recover{pass_idx}")
            ser = send_cmd(ser, logf, "conn", 0.5)
            return passive_wait(f"multi-target-recover{pass_idx}", wait_s)
        for target in missing:
            ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
            ser = send_cmd(ser, logf, f"ota_target name {target}", 0.5)
            ser = send_cmd(ser, logf, "ota_target prefix -", 0.5)
            ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
            ensure_tag_kind(f"exact-{target}")
            ser = send_cmd(ser, logf, "conn", 0.5)
            if passive_wait(f"exact-{target}", wait_s):
                return True
        return all(target.upper() in ready_targets for target in targets)

    if passive_wait("initial", wait_per_target_s):
        return ser

    for pass_idx in range(1, 6):
        print(
            f"[CAPTURE] link setup recovery pass {pass_idx}/5: ready={len(ready_targets)}/{len(targets)}",
            flush=True,
        )

        if passive_wait(f"recovery{pass_idx}", wait_per_target_s):
            break

        if exact_target_enrollment(pass_idx, min(45.0, wait_per_target_s)):
            break

        missing = sorted(
            target for target in targets
            if target.upper() not in ready_targets
        )
        logf.write(
            f"[HOST_WARN {time.monotonic():.3f}] target links missing after pass {pass_idx}: {','.join(missing)}\n"
        )
        logf.flush()

        if ready_targets and len(targets) > 1:
            # Partial multi-tag readiness is valuable state. A global `mode recv`
            # or controller reset drops already-enrolled peers and can turn a
            # recoverable 1/N case into 0/N. Keep the broad BS filter active and
            # let the next passive pass recover only the missing peers.
            logf.write(
                f"[HOST_WARN {time.monotonic():.3f}] partial target links ready="
                + ",".join(sorted(ready_targets))
                + "; keep resident links and avoid global recv reset\n"
            )
            logf.flush()
            ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
            ser = send_cmd(ser, logf, "ota_target name -", 0.5)
            ser = send_cmd(ser, logf, f"ota_target prefix {discovery_prefix}", 0.5)
            ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
            ser = send_cmd(ser, logf, "conn", 0.5)
            continue

        if not hard_reset_used and controller_reset_snr != "-":
            port = ser.port
            baud = ser.baudrate
            try:
                ser.close()
            except Exception:
                pass
            if reset_controller_via_jlink(logf, controller_reset_snr):
                hard_reset_used = True
                ready_targets.clear()
                time.sleep(2.5)
                ser = open_serial_with_retry(port, baud, retries=60)
                drain_serial_until(ser, logf, 3.0)
                ser = send_cmd(ser, logf, "mode recv", 8.0)
                ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
                if len(targets) == 1:
                    ser = send_cmd(ser, logf, f"ota_target name {targets[0]}", 0.5)
                    ser = send_cmd(ser, logf, "ota_target prefix -", 0.5)
                else:
                    ser = send_cmd(ser, logf, "ota_target name -", 0.5)
                    ser = send_cmd(ser, logf, f"ota_target prefix {discovery_prefix}", 0.5)
                ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
                ensure_tag_kind(f"post-reset{pass_idx}")
                if passive_wait(f"post-reset{pass_idx}", wait_per_target_s):
                    break
                continue
            ser = open_serial_with_retry(port, baud, retries=60)

        ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
        if len(targets) == 1:
            ser = send_cmd(ser, logf, f"ota_target name {targets[0]}", 0.5)
            ser = send_cmd(ser, logf, "ota_target prefix -", 0.5)
        else:
            ser = send_cmd(ser, logf, "ota_target name -", 0.5)
            ser = send_cmd(ser, logf, f"ota_target prefix {discovery_prefix}", 0.5)
        ser = send_cmd(ser, logf, "mode recv", 8.0)
        ensure_tag_kind(f"recovery{pass_idx}")

    missing = [
        target for target in targets
        if target.upper() not in ready_targets
    ]
    if missing:
        raise RuntimeError(f"target_link_not_ready:{','.join(missing)}")

    ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
    if len(targets) == 1:
        ser = send_cmd(ser, logf, f"ota_target name {targets[0]}", 0.5)
        ser = send_cmd(ser, logf, "ota_target prefix -", 0.5)
    else:
        ser = send_cmd(ser, logf, "ota_target name -", 0.5)
        ser = send_cmd(ser, logf, f"ota_target prefix {discovery_prefix}", 0.5)
    ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
    return ser


def drain_serial_until_capture(ser: serial.Serial, logf, wait_s: float) -> str:
    deadline = time.time() + wait_s
    chunks: list[str] = []
    while time.time() < deadline:
        try:
            data = ser.read(ser.in_waiting or 1)
        except (SerialException, OSError):
            raise
        if not data:
            continue
        text = data.decode("utf-8", errors="replace")
        chunks.append(text)
        logf.write(text)
        logf.flush()
    return "".join(chunks)


def apply_target_cir_mode(ser: serial.Serial, logf, args, targets: list[str]) -> serial.Serial:
    requested_mode = str(getattr(args, "tag_cir", "off") or "off").strip().lower()
    if requested_mode not in CIR_MODE_CHOICES:
        raise RuntimeError(f"invalid_tag_cir:{requested_mode}")
    if getattr(args, "legacy_no_touch_tags", False) or getattr(args, "skip_target_cir_command", False):
        print(
            f"[CAPTURE] configure: skip target CIR command requested={requested_mode}",
            flush=True,
        )
        logf.write(
            f"[HOST_INFO {time.monotonic():.3f}] target_cir_skipped "
            f"requested={requested_mode} targets={','.join(targets)}\n"
        )
        logf.flush()
        return ser
    mode = tag_cir_range_phase(requested_mode)

    if requested_mode == "full":
        print(
            "[CAPTURE] configure: target CIR requested=full; range phase uses CIR off, "
            "full CIR is deferred",
            flush=True,
        )
    else:
        print(f"[CAPTURE] configure: target CIR mode={mode}", flush=True)
    logf.write(
        f"[HOST_INFO {time.monotonic():.3f}] target_cir_requested={requested_mode} "
        f"target_cir_range_phase={mode} "
        f"targets={','.join(targets)}\n"
    )
    logf.flush()
    ser, text = send_cmd_collect(ser, logf, f"cmd_all CIR {mode.upper()}", 1.5)
    if "CIR_UNSUPPORTED" in text or "CIR_BAD" in text:
        raise RuntimeError(f"tag_cir_rejected:{mode}")
    text_upper = text.upper()
    if f"CIR_OK MODE={mode.upper()}" not in text_upper and "BLE CMD SENT" not in text_upper:
        logf.write(
            f"[HOST_WARN {time.monotonic():.3f}] target_cir_ack_not_seen mode={mode}\n"
        )
        logf.flush()
    return ser


def parse_full_cir_ports(spec: str) -> list[str]:
    ports: list[str] = []
    for item in str(spec or "").split(","):
        value = item.strip()
        if value:
            ports.append(value)
    return ports


def summarize_full_cir_capture_dir(capture_root: Path) -> dict:
    dirs = [p for p in capture_root.glob("CIRRAW_*") if p.is_dir()]
    if not dirs:
        return {"capture_dir": "", "frames": 0, "ports": {}, "meta_csv": ""}
    capture_dir = max(dirs, key=lambda p: p.stat().st_mtime)
    meta_csv = capture_dir / "cir_full_meta.csv"
    ports: dict[str, dict] = {}
    frames = 0
    if meta_csv.exists():
        with meta_csv.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                frames += 1
                label = row.get("port_label", "") or "unknown"
                stream = row.get("stream", "") or "unknown"
                info = ports.setdefault(label, {"frames": 0, "streams": {}})
                info["frames"] += 1
                info["streams"][stream] = info["streams"].get(stream, 0) + 1
    return {
        "capture_dir": str(capture_dir),
        "frames": frames,
        "ports": ports,
        "meta_csv": str(meta_csv) if meta_csv.exists() else "",
        "raw_log": str(capture_dir / "cir_full_raw_serial.log"),
    }


def run_deferred_full_cir_phase(
    ser: serial.Serial,
    logf,
    args,
    session_dir: Path,
    targets: list[str],
) -> tuple[serial.Serial, dict]:
    requested_mode = str(getattr(args, "tag_cir", "off") or "off").strip().lower()
    if requested_mode != "full":
        return ser, {
            "requested": requested_mode,
            "attempted": False,
            "reason": "not_full",
        }

    duration_s = max(0.0, float(getattr(args, "full_cir_duration_s", 0.0) or 0.0))
    ports = parse_full_cir_ports(getattr(args, "full_cir_ports", ""))
    script_path = Path(str(getattr(args, "full_cir_script", ""))).expanduser()
    capture_root = session_dir / "cir_full_usb"
    result = {
        "requested": "full",
        "attempted": False,
        "range_phase_cir": "off",
        "duration_s": duration_s,
        "ports_requested": ports,
        "capture_root": str(capture_root),
        "script": str(script_path),
        "returncode": None,
        "tag_full_sent": False,
        "tag_full_ack_seen": False,
        "tag_off_ack_seen": False,
        "error": "",
    }

    if duration_s <= 0.0:
        result["reason"] = "duration_zero"
        return ser, result
    if not ports:
        result["reason"] = "no_full_cir_ports"
        return ser, result
    if not script_path.exists():
        result["reason"] = "missing_full_cir_script"
        result["error"] = str(script_path)
        return ser, result

    capture_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script_path),
        "--seconds",
        str(duration_s),
        "--capture-root",
        str(capture_root),
        "--target",
        ",".join(targets),
        "--baud",
        str(args.baud),
    ]
    for port in ports:
        cmd.extend(["--port", port])
    anchor_control_port = str(getattr(args, "full_cir_anchor_control_port", "") or "")
    if anchor_control_port:
        cmd.extend(["--control-port", anchor_control_port, "--control-role", "responder"])

    result["attempted"] = True
    result["command"] = cmd
    print(
        f"[CAPTURE] deferred full CIR: range is done; USB phase duration={duration_s:.0f}s",
        flush=True,
    )
    logf.write(
        f"[HOST_INFO {time.monotonic():.3f}] deferred_full_cir_start duration_s={duration_s:.3f} "
        f"ports={len(ports)}\n"
    )
    logf.flush()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    capture_started = threading.Event()

    def stream_child_stdout() -> None:
        for line in proc.stdout:
            print(line, end="", flush=True)
            logf.write(f"[CIRRAW_PROC] {line}")
            if "[CIRRAW] capture start" in line:
                capture_started.set()
        logf.flush()

    stdout_thread = threading.Thread(target=stream_child_stdout, daemon=True)
    stdout_thread.start()

    try:
        wait_deadline = time.time() + 30.0
        while proc.poll() is None and not capture_started.is_set() and time.time() < wait_deadline:
            drain_serial_until(ser, logf, 0.2)

        if proc.poll() is None and capture_started.is_set():
            ser, text = send_cmd_collect(ser, logf, "cmd_all CIR FULL", 1.5)
            result["tag_full_sent"] = True
            text_upper = text.upper()
            result["tag_full_ack_seen"] = (
                "CIR_OK MODE=FULL" in text_upper or "BLE CMD SENT" in text_upper
            )

        while proc.poll() is None:
            drain_serial_until(ser, logf, 0.5)

        result["returncode"] = proc.wait()
        stdout_thread.join(timeout=2.0)
    finally:
        if result["tag_full_sent"]:
            try:
                ser, text = send_cmd_collect(ser, logf, "cmd_all CIR OFF", 1.5)
                text_upper = text.upper()
                result["tag_off_ack_seen"] = (
                    "CIR_OK MODE=OFF" in text_upper or "BLE CMD SENT" in text_upper
                )
            except Exception as exc:
                result["error"] = f"tag_cir_off_failed:{exc}"
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        result["returncode"] = proc.returncode

    result.update(summarize_full_cir_capture_dir(capture_root))
    result["success"] = result.get("returncode") == 0 and result.get("frames", 0) > 0
    if not result["success"] and not result["error"]:
        result["error"] = "full_cir_no_frames_or_helper_failed"
    logf.write(
        f"[HOST_INFO {time.monotonic():.3f}] deferred_full_cir_done "
        + json.dumps(result, sort_keys=True)
        + "\n"
    )
    logf.flush()
    print(
        f"[CAPTURE] deferred full CIR done: frames={result.get('frames', 0)} rc={result.get('returncode')}",
        flush=True,
    )
    return ser, result


def configure_recv_capture_session(
    ser: serial.Serial,
    logf,
    args,
    targets: list[str],
) -> serial.Serial:
    single_target = targets[0] if len(targets) == 1 else ""
    discovery_prefix = target_discovery_prefix(targets)
    clear_text = ""
    status_text = ""
    device_text = ""

    def restore_capture_filter() -> None:
        nonlocal ser
        ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
        if single_target:
            ser = send_cmd(ser, logf, f"ota_target name {single_target}", 0.5)
            ser = send_cmd(ser, logf, "ota_target prefix -", 0.5)
        else:
            ser = send_cmd(ser, logf, "ota_target name -", 0.5)
            ser = send_cmd(ser, logf, f"ota_target prefix {discovery_prefix}", 0.5)
        ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)

    def ensure_config_tag_kind(reason: str) -> None:
        nonlocal ser, device_text
        ser, device_text = send_cmd_collect(ser, logf, "device show", 0.8)
        if "kind=tag" in device_text.lower():
            logf.write(
                f"[HOST_INFO {time.monotonic():.3f}] device_kind_tag_skip reason={reason} already_tag=1\n"
            )
            logf.flush()
            return
        ser = send_cmd(ser, logf, "device kind tag", 2.0)
        ser, device_text = send_cmd_collect(ser, logf, "device show", 0.8)

    def silence_non_target_tags() -> list[str]:
        nonlocal ser
        if getattr(args, "no_silence_non_target_tags", False):
            print("[CAPTURE] configure: targeted non-target idle disabled", flush=True)
            return []

        known_tags = parse_bs_tag_csv(getattr(args, "known_bs_tags", ""))
        target_tags = {target_bs_name(target) for target in targets}
        non_targets = [tag for tag in known_tags if tag not in target_tags]
        if not non_targets:
            print("[CAPTURE] configure: no known non-target tags to silence", flush=True)
            return []

        print(
            "[CAPTURE] configure: targeted idle non-target tags="
            + ",".join(non_targets),
            flush=True,
        )
        logf.write(
            f"[HOST_INFO {time.monotonic():.3f}] targeted_non_target_idle="
            + ",".join(non_targets)
            + "\n"
        )
        logf.flush()
        for tag in non_targets:
            ser = send_cmd(ser, logf, "ota_target token -1", 0.25)
            ser = send_cmd(ser, logf, f"ota_target name {tag}", 0.25)
            ser = send_cmd(ser, logf, "ota_target prefix -", 0.25)
            ser = send_cmd(ser, logf, "ota_target uuid -", 0.25)
            ser = send_cmd(ser, logf, "cmd_all MODE IDLE", 0.8)
        settle_s = max(0.0, float(getattr(args, "non_target_silence_settle_s", 1.0)))
        if settle_s > 0:
            drain_serial_until(ser, logf, settle_s)
        return non_targets

    ser, status_text = send_cmd_collect(ser, logf, "status", 0.8)
    ser, device_text = send_cmd_collect(ser, logf, "device show", 0.8)
    already_recv_tag = (
        "mode=RECV" in status_text
        and "kind=tag" in device_text.lower()
    )
    if args.legacy_no_touch_tags or args.skip_initial_mode_idle:
        print("[CAPTURE] configure: skip initial cmd_all MODE IDLE", flush=True)
        logf.write(f"[HOST_INFO {time.monotonic():.3f}] skip_initial_mode_idle=1\n")
        logf.flush()
    else:
        print("[CAPTURE] configure: stop resident/stale Tag ranging before TDMA setup", flush=True)
        ser = send_cmd(ser, logf, "cmd_all MODE IDLE", 1.0)
        drain_serial_until(ser, logf, 0.4)

    if args.reuse_tag_links:
        print("[CAPTURE] configure: reuse Master_Tag resident links", flush=True)
        ensure_config_tag_kind("reuse-tag-links")
    else:
        print("[CAPTURE] configure: enter clean RECV/tag mode", flush=True)
        if already_recv_tag:
            ser = send_cmd(ser, logf, "device kind tag", 2.0)
        else:
            ser = send_cmd(ser, logf, "mode recv", 8.0)
        ser, status_text = send_cmd_collect(ser, logf, "status", 0.8)
        ser, device_text = send_cmd_collect(ser, logf, "device show", 0.8)

    if not args.reuse_tag_links:
        print("[CAPTURE] configure: preseed TDMA allow-list before tag discovery", flush=True)
        ser = send_cmd(ser, logf, "tdma hold 1", 0.5)
        if args.legacy_keep_tdma_state:
            logf.write(
                f"[HOST_INFO {time.monotonic():.3f}] legacy_keep_tdma_state skip_preseed_tdma_clear=1\n"
            )
            logf.flush()
        else:
            ser, clear_text = send_cmd_collect(ser, logf, "tdma clear", 1.2)
        tr_hz = effective_tr_hz(args)
        ser = send_cmd(ser, logf, f"tdma freq {tdma_roster_profile(args)} {tr_hz}", 0.5)
        for target in targets:
            ser = send_cmd(ser, logf, f"tdma roster {target_bs_name(target)} {tdma_roster_profile(args)}", 0.5)
        if single_target:
            print(
                f"[CAPTURE] configure: restrict tag discovery to {single_target}",
                flush=True,
            )
            ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
            ser = send_cmd(ser, logf, f"ota_target name {single_target}", 0.5)
            ser = send_cmd(ser, logf, "ota_target prefix -", 0.5)
            ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
        if "kind=tag" not in device_text.lower():
            ensure_config_tag_kind("preseed-discovery")
    restore_capture_filter()
    silenced_non_targets = silence_non_target_tags()
    restore_capture_filter()
    print(
        "[CAPTURE] configure: silence resident Tag links for enrollment"
        + (f" after non-target idle ({','.join(silenced_non_targets)})" if silenced_non_targets else ""),
        flush=True,
    )
    ser = send_cmd(ser, logf, "tdma hold 1", 0.5)
    print(
        "[CAPTURE] configure: skip setup MODE IDLE; TDMA roster/hold controls admission",
        flush=True,
    )
    print("[CAPTURE] configure: reassert TDMA target roster", flush=True)
    tr_hz = effective_tr_hz(args)
    # Always clear before reasserting the capture roster.  Reusing resident
    # Tag links is useful for speed, but stale TDMA identities from a previous
    # multi-tag run can otherwise survive and put two targets on the same slot.
    if args.legacy_keep_tdma_state:
        print("[CAPTURE] configure: legacy keep TDMA state; skip tdma clear", flush=True)
        logf.write(
            f"[HOST_INFO {time.monotonic():.3f}] legacy_keep_tdma_state skip_reassert_tdma_clear=1\n"
        )
        logf.flush()
    else:
        ser, clear_text = send_cmd_collect(ser, logf, "tdma clear", 1.2)
    ser = send_cmd(ser, logf, f"tdma freq {tdma_roster_profile(args)} {tr_hz}", 0.5)
    for target in targets:
        ser = send_cmd(ser, logf, f"tdma roster {target_bs_name(target)} {tdma_roster_profile(args)}", 0.5)
    # Keep discovery broad, but make Master_Tag's profile allow-list equal to
    # this run's requested roster before link setup.  Otherwise new Tags that
    # are not in the firmware boot allow-list can be silently ignored until a
    # per-name target is set, which does not scale to 10-tag stress tests.
    restore_capture_filter()
    ser = send_cmd(ser, logf, "conn", 0.5)
    if args.legacy_skip_link_ready_wait:
        print("[CAPTURE] configure: legacy skip target link-ready wait", flush=True)
        logf.write(
            f"[HOST_INFO {time.monotonic():.3f}] legacy_skip_link_ready_wait=1\n"
        )
        logf.flush()
        drain_serial_until(ser, logf, max(0.0, float(args.tag_link_stable_s)))
    else:
        print("[CAPTURE] configure: wait for target links", flush=True)
        ser = ensure_target_links_ready(
            ser,
            logf,
            targets,
            args.controller_reset_snr,
            wait_per_target_s=args.tag_link_timeout_s,
            stable_s=args.tag_link_stable_s,
            initial_text=status_text + device_text + clear_text,
            discovery_prefix_override="",
        )
    if args.legacy_no_touch_tags or args.skip_final_mode_idle:
        print("[CAPTURE] configure: skip final cmd_all MODE IDLE", flush=True)
        logf.write(f"[HOST_INFO {time.monotonic():.3f}] skip_final_mode_idle=1\n")
        logf.flush()
    else:
        print("[CAPTURE] configure: stop target Tag ranging before final TDMA release", flush=True)
        ser = send_cmd(ser, logf, "cmd_all MODE IDLE", 1.0)
        drain_serial_until(ser, logf, 0.4)
    ser = apply_target_cir_mode(ser, logf, args, targets)
    print("[CAPTURE] configure: release TDMA hold and verify TDMA CFG", flush=True)
    ser = send_cmd(ser, logf, "tdma hold 0", 1.0)
    ser = send_cmd(ser, logf, "tdma rebalance", 1.0)
    # Runtime CFG can collide with link traffic right after hold release, and a
    # disconnected target can make Master print TDMA weighted without a matching
    # CFG_OK. Close that loop before capture starts.
    expected_pmode_by_target, expected_freq_by_target = expected_tdma_maps(args, targets)
    config_retries = max(1, int(getattr(args, "tdma_config_retries", 3)))
    last_check: dict | None = None
    for attempt in range(1, config_retries + 1):
        print(
            f"[CAPTURE] configure: TDMA CFG apply/check attempt {attempt}/{config_retries}",
            flush=True,
        )
        ser, _ = send_cmd_collect(ser, logf, "tdma show", 1.0)
        ser, _ = send_cmd_collect(ser, logf, "status", 0.8)
        ser, _ = send_cmd_collect(ser, logf, "device show", 0.8)
        logf.flush()
        raw_log = Path(getattr(logf, "name", ""))
        last_check = build_tdma_config_check(
            raw_log,
            targets,
            expected_pmode_by_target,
            expected_freq_by_target,
            allow_scheduler_actual_hz_below_request=args.allow_scheduler_actual_hz_below_request,
        )
        if last_check.get("match", False):
            print("[CAPTURE] configure: TDMA CFG verified match=true", flush=True)
            break

        bad = [
            f"{target}:{','.join(info.get('mismatches', [])) or 'unknown'}"
            for target, info in last_check.get("per_target", {}).items()
            if not info.get("match", False)
        ]
        print(
            "[CAPTURE] configure: TDMA CFG mismatch; retry "
            + "; ".join(bad),
            flush=True,
        )
        if args.legacy_skip_link_ready_wait:
            logf.write(
                f"[HOST_WARN {time.monotonic():.3f}] legacy_skip_link_ready_wait continuing despite TDMA CFG mismatch: "
                + "; ".join(bad)
                + "\n"
            )
            logf.flush()
            break
        if attempt >= config_retries:
            break

        print("[CAPTURE] configure: refresh links and reassert roster before retry", flush=True)
        ser = send_cmd(ser, logf, "tdma hold 1", 0.5)
        ser = send_cmd(ser, logf, "tdma clear", 1.2)
        restore_capture_filter()
        ser = send_cmd(ser, logf, "conn", 0.5)
        ser = ensure_target_links_ready(
            ser,
            logf,
            targets,
            args.controller_reset_snr,
            wait_per_target_s=args.tag_link_timeout_s,
            stable_s=args.tag_link_stable_s,
            initial_text="",
            discovery_prefix_override="",
        )
        for target in targets:
            ser = send_cmd(ser, logf, f"tdma roster {target_bs_name(target)} {tdma_roster_profile(args)}", 0.5)
        ser = send_cmd(ser, logf, f"tdma freq {tdma_roster_profile(args)} {tr_hz}", 0.5)
        ser = send_cmd(ser, logf, "tdma hold 0", 1.0)

    if last_check is not None and not last_check.get("match", False):
        if getattr(args, "allow_legacy_tdma_show_only", False):
            legacy_check = build_legacy_tdma_show_check(
                raw_log,
                targets,
                expected_freq_by_target,
                tdma_roster_profile(args),
            )
            if legacy_check.get("match", False):
                print(
                    "[CAPTURE] configure: legacy TDMA show-only match=true; "
                    "continuing without CFG_OK",
                    flush=True,
                )
                logf.write(
                    "[HOST_INFO "
                    f"{time.monotonic():.3f}] legacy_tdma_show_only="
                    + json.dumps(legacy_check, sort_keys=True)
                    + "\n"
                )
                logf.flush()
                last_check = legacy_check

        if last_check.get("match", False):
            print("[CAPTURE] configure: TDMA ready", flush=True)
            return ser

        bad = [
            f"{target}:{','.join(info.get('mismatches', [])) or 'unknown'}"
            for target, info in last_check.get("per_target", {}).items()
            if not info.get("match", False)
        ]
        if args.legacy_skip_link_ready_wait:
            print(
                "[CAPTURE] configure: legacy continuing despite TDMA CFG mismatch; "
                + "; ".join(bad),
                flush=True,
            )
            logf.write(
                f"[HOST_WARN {time.monotonic():.3f}] legacy_continue_with_tdma_cfg_mismatch="
                + "; ".join(bad)
                + "\n"
            )
            logf.flush()
            return ser
        raise RuntimeError("TDMA CFG verify failed before capture: " + "; ".join(bad))
    print("[CAPTURE] configure: TDMA ready", flush=True)
    return ser


def cleanup_capture_session(ser: serial.Serial, logf, args) -> tuple[serial.Serial, dict]:
    if args.no_cleanup:
        return ser, {"attempted": False, "reason": "disabled_by_flag"}

    result = {
        "attempted": True,
        "success": False,
        "command": "cmd_all MODE AOTA",
        "error": "",
        "stop_notify_rows": None,
        "stop_verify_s": 3.0,
        "tdma_cleanup": False,
        "tdma_release_hold": False,
        "fallback_command": None,
    }

    def verify_quiet() -> int:
        text = drain_serial_until(ser, logf, result["stop_verify_s"])
        rows = 0
        for line in text.splitlines():
            if line_has_range_activity(line):
                rows += 1
        return rows

    try:
        print("[CAPTURE] cleanup: stopping all tag ranging with cmd_all MODE AOTA", flush=True)
        logf.write(f"[HOST_CLEANUP {time.monotonic():.3f}] stop all tag ranging via cmd_all MODE AOTA\n")
        logf.flush()
        ser = send_cmd(ser, logf, "cmd_all MODE AOTA", 1.0)
        ser = send_cmd(ser, logf, "tdma hold 1", 0.5)
        ser = send_cmd(ser, logf, "tdma clear", 1.0)
        ser = send_cmd(ser, logf, "tdma freq motion 10", 0.5)
        ser, tdma_show = send_cmd_collect(ser, logf, "tdma show", 0.8)
        result["tdma_cleanup"] = True
        if "roster=explicit" in tdma_show:
            ser = send_cmd(ser, logf, "tdma hold 0", 0.5)
            result["tdma_release_hold"] = True
        else:
            logf.write(
                f"[HOST_CLEANUP {time.monotonic():.3f}] legacy_master_keep_tdma_hold=1\n"
            )
            logf.flush()
        rows = verify_quiet()
        if rows > 0:
            result["fallback_command"] = "cmd_all MODE IDLE"
            print("[CAPTURE] cleanup: MODE AOTA not quiet; fallback cmd_all MODE IDLE", flush=True)
            logf.write(f"[HOST_CLEANUP {time.monotonic():.3f}] fallback stop via cmd_all MODE IDLE\n")
            logf.flush()
            ser = send_cmd(ser, logf, "cmd_all MODE IDLE", 1.0)
            rows = verify_quiet()
        result["stop_notify_rows"] = rows
        result["success"] = rows == 0
        if result["success"]:
            print("[CAPTURE] cleanup: all tags quiet after stop command", flush=True)
        else:
            result["error"] = f"still saw {rows} range rows after stop"
            print(f"[CAPTURE] cleanup: stop verify failed: {result['error']}", flush=True)
    except (SerialException, OSError) as exc:
        result["error"] = str(exc)
        try:
            ser.close()
        except Exception:
            pass
        try:
            ser = open_serial_with_retry(args.port, args.baud, retries=30)
            time.sleep(0.8)
            drain_serial_until(ser, logf, 0.8)
            ser = send_cmd(ser, logf, "cmd_all MODE AOTA", 1.0)
            ser = send_cmd(ser, logf, "tdma hold 1", 0.5)
            ser = send_cmd(ser, logf, "tdma clear", 1.0)
            ser = send_cmd(ser, logf, "tdma freq motion 10", 0.5)
            ser, tdma_show = send_cmd_collect(ser, logf, "tdma show", 0.8)
            result["tdma_cleanup"] = True
            if "roster=explicit" in tdma_show:
                ser = send_cmd(ser, logf, "tdma hold 0", 0.5)
                result["tdma_release_hold"] = True
            else:
                logf.write(
                    f"[HOST_CLEANUP {time.monotonic():.3f}] legacy_master_keep_tdma_hold=1\n"
                )
                logf.flush()
            rows = verify_quiet()
            if rows > 0:
                result["fallback_command"] = "cmd_all MODE IDLE"
                ser = send_cmd(ser, logf, "cmd_all MODE IDLE", 1.0)
                rows = verify_quiet()
            result["stop_notify_rows"] = rows
            result["success"] = rows == 0
            result["error"] = "" if result["success"] else f"still saw {rows} range rows after stop"
            if result["success"]:
                print("[CAPTURE] cleanup: all tags quiet after serial reopen", flush=True)
            else:
                print(f"[CAPTURE] cleanup: stop verify failed after reopen: {result['error']}", flush=True)
        except Exception as retry_exc:
            result["error"] = f"{type(retry_exc).__name__}: {retry_exc}"
            print(f"[CAPTURE] cleanup: failed: {result['error']}", flush=True)
    return ser, result


def _cfg_assigned_record(match: re.Match) -> dict:
    return {
        "tag_id": int(match.group("tag_id")),
        "slot": int(match.group("slot")),
        "count": int(match.group("count")),
        "mask": int(match.group("mask"), 16),
        "mask_hex": f"0x{int(match.group('mask'), 16):04X}",
        "period_ms": int(match.group("period")),
        "active_ms": int(match.group("active")),
        "active_us": int(match.group("active_us") or 0),
        "generation": int(match.group("gen")),
        "pmode": int(match.group("pmode")),
    }


def _cfg_ok_record(match: re.Match) -> dict:
    return {
        "tag_id": int(match.group("tag_id")),
        "slot": int(match.group("slot")),
        "count": int(match.group("count")),
        "mask": int(match.group("mask"), 16),
        "mask_hex": f"0x{int(match.group('mask'), 16):04X}",
        "period_ms": int(match.group("period")),
        "active_ms": int(match.group("active")),
        "active_us": int(match.group("active_us") or 0),
        "generation": int(match.group("gen")),
        "live": int(match.group("live")),
    }


def _tdma_weighted_record(match: re.Match) -> dict:
    return {
        "profile": match.group("profile"),
        "target_hz": int(match.group("target_hz")),
        "mask": int(match.group("mask"), 16),
        "mask_hex": f"0x{int(match.group('mask'), 16):04X}",
        "slots": int(match.group("slots")),
        "count": int(match.group("count")),
        "actual_x100": int(match.group("actual_x100")),
        "actual_hz": int(match.group("actual_x100")) / 100.0,
    }


def _mask_slot_count(mask: int) -> int:
    return int(mask).bit_count()


def _actual_cfg_hz(actual: dict | None) -> float | None:
    if not actual:
        return None
    try:
        period_ms = int(actual.get("period_ms") or 0)
        count = int(actual.get("count") or 0)
        mask = int(actual.get("mask") or 0)
    except (TypeError, ValueError):
        return None
    if period_ms <= 0 or count <= 0 or mask <= 0:
        return None
    epoch_s = (period_ms * count) / 1000.0
    if epoch_s <= 0:
        return None
    return _mask_slot_count(mask) / epoch_s


def build_tdma_config_check(
    raw_log_path: Path,
    targets: list[str],
    expected_pmode_by_target: dict[str, int],
    expected_freq_by_target: dict[str, int] | None = None,
    allow_scheduler_actual_hz_below_request: bool = False,
) -> dict:
    expected_by_bs: dict[str, dict] = {}
    actual_by_bs: dict[str, dict] = {}
    weighted_by_bs: dict[str, dict] = {}

    if raw_log_path.exists():
        for line in raw_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            for fragment in line.split("|"):
                assigned = CFG_ASSIGNED_DETAIL_RE.search(fragment)
                if assigned:
                    expected_by_bs[assigned.group("bs").upper()] = _cfg_assigned_record(assigned)

                ok = CFG_OK_RE.search(fragment)
                if ok:
                    bs = extract_bs_name(fragment)
                    if bs:
                        actual_by_bs[bs.upper()] = _cfg_ok_record(ok)
                weighted = TDMA_WEIGHTED_RE.search(fragment)
                if weighted:
                    weighted_by_bs[weighted.group("bs").upper()] = _tdma_weighted_record(weighted)

    per_target: dict[str, dict] = {}
    all_match = True
    strict_fields = ["slot", "count", "mask", "period_ms", "active_ms", "active_us"]
    diagnostic_fields = ["tag_id", "generation"]
    for target in targets:
        bs = target_bs_name(target).upper()
        expected = expected_by_bs.get(bs)
        actual = actual_by_bs.get(bs)
        mismatches: list[str] = []
        warnings: list[str] = []

        if actual is None:
            mismatches.append("missing_actual_cfg_ok")
        elif expected is None:
            # Console diagnostics can be split/coalesced with TR notify traffic
            # during high-rate multi-tag captures. CFG_OK is the target-side
            # acknowledgement that the runtime TDMA tuple was actually applied.
            warnings.append("missing_expected_cfg_assigned")
        if expected is not None and actual is not None:
            for field in strict_fields:
                if expected.get(field) != actual.get(field):
                    mismatches.append(field)
            for field in diagnostic_fields:
                if expected.get(field) != actual.get(field):
                    warnings.append(field)
            if actual.get("live") != 1:
                mismatches.append("live")

        expected_pmode = expected_pmode_by_target.get(target)
        if expected_pmode is None:
            expected_pmode = expected_pmode_by_target.get(bs)
        if expected is not None and expected_pmode is not None and expected.get("pmode") != expected_pmode:
            mismatches.append("pmode")

        expected_freq_hz = None
        if expected_freq_by_target:
            expected_freq_hz = expected_freq_by_target.get(target)
            if expected_freq_hz is None:
                expected_freq_hz = expected_freq_by_target.get(bs)
        actual_cfg_hz = _actual_cfg_hz(actual)
        weighted = weighted_by_bs.get(bs)
        if weighted is None:
            warnings.append("missing_tdma_weighted")
            if expected_freq_hz is not None and actual_cfg_hz is not None:
                if round(actual_cfg_hz, 3) != round(float(expected_freq_hz), 3):
                    mismatches.append("actual_cfg_hz")
        elif expected_freq_hz is not None:
            if weighted.get("target_hz") != expected_freq_hz:
                warnings.append("scheduler_target_hz")
            if weighted.get("actual_x100", 0) < expected_freq_hz * 100:
                warnings.append("scheduler_actual_hz")
            if actual is not None:
                if expected is None:
                    # In high-rate multi-tag setup logs, the Master-side
                    # CFG/TDM weighted lines can be split by incoming TR notify
                    # traffic. In that case the latest parsed weighted record
                    # may be stale, while CFG_OK is the Tag-side applied tuple.
                    if expected_freq_hz is not None and actual_cfg_hz is not None:
                        if round(actual_cfg_hz, 3) != round(float(expected_freq_hz), 3):
                            mismatches.append("actual_cfg_hz")
                else:
                    if weighted.get("mask") != actual.get("mask"):
                        warnings.append("scheduler_mask_not_applied")
                    if weighted.get("count") != actual.get("count"):
                        warnings.append("scheduler_count_not_applied")

        match = not mismatches
        all_match = all_match and match
        per_target[target] = {
            "bs": bs,
            "match": match,
            "mismatches": mismatches,
            "warnings": warnings,
            "expected": expected,
            "actual": actual,
            "actual_cfg_hz": actual_cfg_hz,
            "scheduler": weighted,
            "expected_pmode": expected_pmode,
            "expected_freq_hz": expected_freq_hz,
        }

    identity_to_targets: dict[tuple[int, int, int, int], list[str]] = {}
    for target, info in per_target.items():
        actual = info.get("actual") or {}
        if not actual:
            continue

        def _actual_int(name: str, default: int) -> int:
            value = actual.get(name)
            if value is None:
                return default
            return int(value)

        key = (
            _actual_int("tag_id", -1),
            _actual_int("slot", -1),
            _actual_int("count", -1),
            _actual_int("mask", 0),
        )
        identity_to_targets.setdefault(key, []).append(target)

    duplicate_identities: list[dict[str, object]] = []
    for key, dup_targets in identity_to_targets.items():
        if len(dup_targets) < 2:
            continue
        tag_id, slot, count, mask = key
        duplicate_identities.append(
            {
                "targets": dup_targets,
                "tag_id": tag_id,
                "slot": slot,
                "count": count,
                "mask": mask,
                "mask_hex": f"0x{mask:04X}",
            }
        )
        for dup_target in dup_targets:
            info = per_target.get(dup_target)
            if not info:
                continue
            mismatches = info.setdefault("mismatches", [])
            if "duplicate_tdma_identity" not in mismatches:
                mismatches.append("duplicate_tdma_identity")
            info["match"] = False
        all_match = False

    return {
        "match": all_match,
        "per_target": per_target,
        "duplicate_identities": duplicate_identities,
    }


def build_legacy_tdma_show_check(
    raw_log_path: Path,
    targets: list[str],
    expected_freq_by_target: dict[str, int] | None = None,
    expected_profile: str = "motion",
) -> dict:
    show_by_bs: dict[str, dict] = {}

    if raw_log_path.exists():
        for line in raw_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            for fragment in line.split("|"):
                profile = TDMA_SHOW_PROFILE_RE.search(fragment)
                if profile:
                    show_by_bs[profile.group("bs").upper()] = {
                        "profile": profile.group("profile"),
                        "target_hz": int(profile.group("target_hz")),
                    }

    per_target: dict[str, dict] = {}
    all_match = True
    for target in targets:
        bs = target_bs_name(target).upper()
        expected_hz = None
        if expected_freq_by_target:
            expected_hz = expected_freq_by_target.get(target)
            if expected_hz is None:
                expected_hz = expected_freq_by_target.get(bs)
        show = show_by_bs.get(bs)
        mismatches: list[str] = []
        if show is None:
            mismatches.append("missing_tdma_show_profile")
        else:
            if show.get("profile") != expected_profile:
                mismatches.append("profile")
            if expected_hz is not None and show.get("target_hz") != expected_hz:
                mismatches.append("target_hz")

        per_target[target] = {
            "bs": bs,
            "tdma_show": show,
            "mismatches": mismatches,
            "match": not mismatches,
        }
        if mismatches:
            all_match = False

    return {
        "match": all_match,
        "mode": "legacy_tdma_show_only",
        "per_target": per_target,
    }


def print_capture_status(capture_start_wall: float,
                         end_time: float,
                         tr_rows: list[dict],
                         targets: list[str],
                         tr_by_target: dict[str, int],
                         expected_freq_by_target: dict[str, int] | None = None) -> None:
    elapsed = max(0.0, time.time() - capture_start_wall)
    remaining = max(0.0, end_time - time.time())
    total = max(0.001, end_time - capture_start_wall)
    pct = min(100.0, max(0.0, elapsed * 100.0 / total))
    filled = int(round(pct / 5.0))
    bar = "#" * filled + "." * (20 - filled)
    row_rate = len(tr_rows) / elapsed if elapsed > 0 else 0.0
    sweep_quality = summarize_sweep_validity(tr_rows)
    sweeps_total = int(sweep_quality.get("sweeps_total") or 0)
    sweeps_ge7 = int(sweep_quality.get("sweeps_ge7") or 0)
    sweeps_ge8 = int(sweep_quality.get("sweeps_ge8") or 0)
    ge7_pct = (sweeps_ge7 * 100.0 / sweeps_total) if sweeps_total > 0 else 0.0
    ge8_pct = (sweeps_ge8 * 100.0 / sweeps_total) if sweeps_total > 0 else 0.0
    parts = [
        f"[{bar}]",
        f"{pct:5.1f}%",
        f"elapsed={elapsed:.0f}s",
        f"eta={remaining:.0f}s",
        f"rows={len(tr_rows)}",
        f"row_rate={row_rate:.1f}/s",
        f"sw={sweeps_total}",
        f"ge7={ge7_pct:.0f}%",
        f"ge8={ge8_pct:.0f}%",
    ]
    for target in targets:
        count = sum(tr_by_target.get(alias, 0) for alias in target_aliases(target))
        cfg_hz = None
        if expected_freq_by_target:
            cfg_hz = expected_freq_by_target.get(target)
            if cfg_hz is None:
                cfg_hz = expected_freq_by_target.get(target_bs_name(target))
        if cfg_hz is not None:
            parts.append(f"{target}:rows={count},cfg={cfg_hz}Hz")
        else:
            parts.append(f"{target}:rows={count}")
    print("[CAPTURE] " + " ".join(parts), flush=True)


def main() -> int:
    args = build_parser().parse_args()
    if (
        args.controller_reset_snr == "960148546"
        and "6918E0384172A49F" in str(args.port)
    ):
        args.controller_reset_snr = "1050070698"
        print(
            "[CAPTURE] BioSpur_1 port detected; controller reset SNR set to 1050070698",
            flush=True,
        )
    assert_not_jlink_when_biospur_available(args.port)

    targets = [normalize_target(x) for x in args.targets.split(",") if x.strip()]
    tr_hz = effective_tr_hz(args)
    target_set = {alias for target in targets for alias in target_aliases(target)}
    expected_pmode_by_target, expected_freq_by_target = expected_tdma_maps(args, targets)
    known_bs_tags = parse_bs_tag_csv(args.known_bs_tags)
    non_target_silence_tags = [
        tag for tag in known_bs_tags if tag not in {target_bs_name(target) for target in targets}
    ]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_out = Path(args.out_dir)
    session_dir = base_out if args.out_dir_exact else base_out.parent / f"{base_out.name}_{ts}"
    session_dir.mkdir(parents=True, exist_ok=True)

    raw_log_path = session_dir / "raw.log"
    summary_json_path = session_dir / "summary.json"
    commands_json_path = session_dir / "commands.json"

    cmd_plan = {
        "mode": "recv",
        "device_kind": "tag",
        "targets": targets,
        "expected_pmode": expected_pmode_by_target,
        "expected_freq_hz": expected_freq_by_target,
        "freq_hz": {"tr": tr_hz},
        "tag_cir": args.tag_cir,
        "tag_cir_range_phase": tag_cir_range_phase(args.tag_cir),
        "full_cir_duration_s": args.full_cir_duration_s if args.tag_cir == "full" else 0.0,
        "full_cir_ports": parse_full_cir_ports(args.full_cir_ports) if args.tag_cir == "full" else [],
        "duration_s": args.duration,
        "known_bs_tags": known_bs_tags,
        "silence_non_target_tags": not args.no_silence_non_target_tags,
        "non_target_silence_tags": non_target_silence_tags
        if not args.no_silence_non_target_tags
        else [],
    }
    commands_json_path.write_text(json.dumps(cmd_plan, indent=2), encoding="utf-8")
    print(f"[CAPTURE] session_dir={session_dir}", flush=True)
    print(f"[CAPTURE] raw_log={raw_log_path}", flush=True)
    print(
        "[CAPTURE] plan: targets="
        + ",".join(targets)
        + f" tr={tr_hz}Hz duration={args.duration:.0f}s",
        flush=True,
    )

    start_wall = time.time()
    anchor_preflight = {"skipped": True, "success": True}
    if not args.skip_anchor_preflight:
        anchor_preflight = run_anchor_responder_preflight(args, session_dir)
        if not anchor_preflight.get("success"):
            summary = {
                "success": False,
                "anchor_preflight_failed": True,
                "anchor_preflight": anchor_preflight,
                "port": args.port,
                "duration_s": args.duration,
                "elapsed_s": time.time() - start_wall,
                "session_dir": str(session_dir),
                "targets": targets,
                "freq_hz": {"tr": tr_hz},
                "tr_all": 0,
                "tr_valid_all": 0,
                "raw_log": str(raw_log_path),
                "tr_all_csv": str(session_dir / "tr_all.csv"),
            }
            summary_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary, indent=2))
            return 2
        settle_after_anchor_preflight(args, "initial preflight")

    conn_meta: dict[str, dict] = {}
    tr_rows: list[dict] = []
    rfd_rows: list[dict] = []
    interrupted = False
    startup_failed = False
    startup_fail_targets: list[str] = []
    no_tr_timeout = False
    controller_lost = False
    controller_recovery_attempts = 0
    controller_recovery_successes = 0
    cleanup_result: dict = {"attempted": False, "reason": "not_reached"}
    cir_full_phase: dict = {"attempted": False, "reason": "not_reached"}

    with raw_log_path.open("w", encoding="utf-8") as logf:
        print(f"[CAPTURE] open serial: {args.port}", flush=True)
        with open_serial_with_retry(args.port, args.baud) as ser:
            time.sleep(0.8)

            # Initial drain
            print("[CAPTURE] drain boot/runtime output", flush=True)
            drain_serial_until(ser, logf, 1.0)

            pending = ""
            capture_start_wall = time.time()
            end_time = capture_start_wall
            no_tr_deadline = None
            last_status_at = 0.0
            tr_seen: dict[str, int] = defaultdict(int)
            skipped_before_target_pmode = 0
            # The setup commands above drain serial output before the parser loop
            # starts, so seed the desired final pmode from the requested TDMA CFG.
            pmode_by_peer: dict[str, int] = {
                name: pmode
                for name, pmode in expected_pmode_by_target.items()
                if pmode is not None
            }
            try:
                ser = configure_recv_capture_session(ser, logf, args, targets)
            except (SerialException, OSError, RuntimeError) as exc:
                startup_failed = True
                message = str(exc)
                target_match = re.search(
                    r"target_link_not_ready:([A-Za-z0-9_,.-]+)",
                    message,
                )
                if target_match:
                    startup_fail_targets = [
                        item.strip()
                        for item in target_match.group(1).split(",")
                        if item.strip()
                    ]
                logf.write(
                    f"[HOST_ERROR {time.monotonic():.3f}] startup configure failed: {message}\n"
                )
                logf.flush()
                print(f"[CAPTURE] abort: startup configure failed: {message}", flush=True)
            else:
                print("[CAPTURE] TDMA verified; start TR capture", flush=True)
                capture_start_wall = time.time()
                end_time = capture_start_wall + args.duration
                no_tr_deadline = (
                    capture_start_wall + max(0.0, float(args.no_tr_timeout_s))
                    if float(args.no_tr_timeout_s) > 0.0
                    else None
                )
            try:
                while time.time() < end_time:
                    try:
                        chunk = ser.read(ser.in_waiting or 1)
                    except (SerialException, OSError) as exc:
                        controller_recovery_attempts += 1
                        logf.write(
                            f"[HOST_WARN {time.monotonic():.3f}] capture read failed: {exc}; recover controller\n"
                        )
                        logf.flush()
                        try:
                            ser.close()
                        except Exception:
                            pass
                        recovered = recover_controller_serial(args, logf, str(exc))
                        if recovered is None:
                            controller_lost = True
                            logf.write(
                                f"[HOST_ERROR {time.monotonic():.3f}] controller recovery failed; closing capture and writing partial summary\n"
                            )
                            logf.flush()
                            break
                        controller_recovery_successes += 1
                        ser = recovered
                        time.sleep(0.8)
                        drain_serial_until(ser, logf, 0.8)
                        if not args.skip_anchor_preflight:
                            try:
                                ser.close()
                            except Exception:
                                pass
                            logf.write(
                                f"[HOST_WARN {time.monotonic():.3f}] controller recovery: rerun anchor responder preflight before tag TDMA\n"
                            )
                            logf.flush()
                            anchor_preflight = run_anchor_responder_preflight(args, session_dir)
                            if not anchor_preflight.get("success"):
                                controller_lost = True
                                logf.write(
                                    f"[HOST_ERROR {time.monotonic():.3f}] controller recovery anchor preflight failed; closing capture and writing partial summary\n"
                                )
                                logf.flush()
                                break
                            settle_after_anchor_preflight(args, "controller recovery preflight")
                            ser = open_serial_with_retry(args.port, args.baud)
                            drain_serial_until(ser, logf, 0.8)
                        logf.write(
                            f"[HOST_WARN {time.monotonic():.3f}] controller reopened; reconfigure recv/tdma session\n"
                        )
                        logf.flush()
                        try:
                            ser = configure_recv_capture_session(
                                ser, logf, args, targets
                            )
                        except (SerialException, OSError, RuntimeError) as cfg_exc:
                            controller_lost = True
                            logf.write(
                                f"[HOST_ERROR {time.monotonic():.3f}] controller recovery reconfigure failed: {cfg_exc}; closing capture and writing partial summary\n"
                            )
                            logf.flush()
                            break
                        continue
                    if not chunk:
                        if (
                            no_tr_deadline is not None
                            and not tr_rows
                            and time.time() >= no_tr_deadline
                        ):
                            no_tr_timeout = True
                            logf.write(
                                f"[HOST_ERROR {time.monotonic():.3f}] no range rows within {args.no_tr_timeout_s:.1f}s after TDMA release; abort capture early\n"
                            )
                            logf.flush()
                            print(
                                f"[CAPTURE] abort: no range rows within {args.no_tr_timeout_s:.1f}s after TDMA release",
                                flush=True,
                            )
                            break
                        if time.time() - last_status_at >= 1.0:
                            print_capture_status(
                                capture_start_wall,
                                end_time,
                                tr_rows,
                                targets,
                                tr_seen,
                                expected_freq_by_target,
                            )
                            last_status_at = time.time()
                        continue

                    text = chunk.decode("utf-8", errors="replace")
                    logf.write(text)
                    logf.flush()
                    pending += text

                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        line = line.rstrip("\r")
                        if not line:
                            continue
                        host_epoch_s = round(time.time(), 6)
                        host_elapsed_s = round(host_epoch_s - capture_start_wall, 6)

                        match = CONNECTED_RE.search(line)
                        if match:
                            conn_id = match.group("conn")
                            meta = conn_meta.setdefault(conn_id, {})
                            if match.group("name"):
                                meta["peer_name"] = match.group("name")
                            if match.group("bs"):
                                meta["peer_name"] = match.group("bs")
                            meta["tag_id"] = int(match.group("tag_id"))

                        match = CFG_ASSIGNED_RE.search(line)
                        if match:
                            conn_id = match.group("conn")
                            meta = conn_meta.setdefault(conn_id, {})
                            meta["peer_name"] = match.group("bs")
                            meta["tag_id"] = int(match.group("tag_id"))
                            meta["pmode"] = int(match.group("pmode"))
                            pmode_by_peer[match.group("bs")] = int(match.group("pmode"))

                        for tr in iter_tr_records(line):
                            match = re.search(TAG_NOTIFY_PREFIX_RE, line)
                            conn_id = match.groupdict().get("conn") if match else ""
                            meta = conn_meta.get(conn_id, {}) if conn_id else {}
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            expected_pmode = expected_pmode_by_target.get(peer_name)
                            active_pmode = int(tr["pmode"])
                            if expected_pmode is not None and active_pmode != expected_pmode:
                                skipped_before_target_pmode += 1
                                continue
                            tr_rows.append(
                                {
                                    "conn_id": conn_id,
                                    "host_elapsed_s": host_elapsed_s,
                                    "host_epoch_s": host_epoch_s,
                                    "peer_name": peer_name,
                                    "tag_id": meta.get("tag_id", ""),
                                    "sweep": int(tr["sweep"]),
                                    "plan": tr["plan"],
                                    "pmode": active_pmode,
                                    "anchor_id": int(tr["anchor_id"]),
                                    "raw_mm": int(tr["raw_mm"]),
                                    "range_mm": int(tr["range_mm"]),
                                    "quality_percent": int(tr["quality_percent"]),
                                    "valid": int(tr["valid"]),
                                    "status": tr["status"],
                                    "quality_flag_percent": int(tr.get("quality_flag_percent") or 0),
                                    "first_to_last_us": int(tr.get("first_to_last_us") or 0),
                                    "frame_us": int(tr.get("frame_us") or 0),
                                    "poll_count": int(tr.get("poll_count") or 0),
                                    "tr_version": tr.get("tr_version", ""),
                                    "rx_mask": tr.get("rx_mask", ""),
                                    "air_us": tr.get("air_us", ""),
                                    "post_us": tr.get("post_us", ""),
                                    "cycle_us": tr.get("cycle_us", ""),
                                    "rx_seen": tr.get("rx_seen", ""),
                                    "imu_valid": int(tr.get("imu_valid") or 0),
                                    "imu_n": int(tr.get("imu_n") or 0),
                                    "acc_norm_mean_mg": tr.get("acc_norm_mean_mg", ""),
                                    "acc_norm_std_mg": tr.get("acc_norm_std_mg", ""),
                                    "acc_norm_min_mg": tr.get("acc_norm_min_mg", ""),
                                    "acc_norm_max_mg": tr.get("acc_norm_max_mg", ""),
                                    "imu_skip_count": int(tr.get("imu_skip_count") or 0),
                                    "diag_source": tr.get("diag_source", ""),
                                    "tr_diag_version": tr.get("tr_diag_version", ""),
                                    "anchor_diag_valid": tr.get("anchor_diag_valid", ""),
                                    "anchor_diag_flags": tr.get("anchor_diag_flags", ""),
                                    "anchor_fp_sum_q8": tr.get("anchor_fp_sum_q8", ""),
                                    "anchor_cir_pwr_q8": tr.get("anchor_cir_pwr_q8", ""),
                                    "anchor_rxpacc_q8": tr.get("anchor_rxpacc_q8", ""),
                                    "tag_diag_valid": tr.get("tag_diag_valid", ""),
                                    "tag_diag_flags": tr.get("tag_diag_flags", ""),
                                    "tag_fp_sum_q8": tr.get("tag_fp_sum_q8", ""),
                                    "tag_cir_pwr_q8": tr.get("tag_cir_pwr_q8", ""),
                                    "tag_rxpacc_q8": tr.get("tag_rxpacc_q8", ""),
                                }
                            )
                            if peer_name:
                                tr_seen[peer_name] += 1

                        for rfd in iter_rfd_records(line):
                            match = re.search(TAG_NOTIFY_PREFIX_RE, line)
                            conn_id = match.groupdict().get("conn") if match else ""
                            meta = conn_meta.get(conn_id, {}) if conn_id else {}
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            rfd_rows.append(
                                {
                                    "conn_id": conn_id,
                                    "host_elapsed_s": host_elapsed_s,
                                    "host_epoch_s": host_epoch_s,
                                    "peer_name": peer_name,
                                    "tag_id": meta.get("tag_id", ""),
                                    **rfd,
                                }
                            )

                    if time.time() - last_status_at >= 1.0:
                        print_capture_status(
                            capture_start_wall,
                            end_time,
                            tr_rows,
                            targets,
                            tr_seen,
                            expected_freq_by_target,
                        )
                        last_status_at = time.time()
                    if (
                        no_tr_deadline is not None
                        and not tr_rows
                        and time.time() >= no_tr_deadline
                    ):
                        no_tr_timeout = True
                        logf.write(
                            f"[HOST_ERROR {time.monotonic():.3f}] no range rows within {args.no_tr_timeout_s:.1f}s after TDMA release; abort capture early\n"
                        )
                        logf.flush()
                        print(
                            f"[CAPTURE] abort: no range rows within {args.no_tr_timeout_s:.1f}s after TDMA release",
                            flush=True,
                        )
                        break
            except KeyboardInterrupt:
                interrupted = True
                print("\n[CAPTURE] interrupted by user; writing partial outputs...", file=sys.stderr, flush=True)

            if pending.strip():
                logf.write(pending.rstrip("\r") + "\n")
            if not interrupted and not startup_failed and not controller_lost and not no_tr_timeout:
                ser, cir_full_phase = run_deferred_full_cir_phase(
                    ser,
                    logf,
                    args,
                    session_dir,
                    targets,
                )
            elif str(getattr(args, "tag_cir", "off")).strip().lower() == "full":
                cir_full_phase = {
                    "requested": "full",
                    "attempted": False,
                    "reason": "range_capture_failed_or_interrupted",
                }
            ser, cleanup_result = cleanup_capture_session(ser, logf, args)

    tdma_config_check = build_tdma_config_check(
        raw_log_path,
        targets,
        expected_pmode_by_target,
        expected_freq_by_target,
        allow_scheduler_actual_hz_below_request=args.allow_scheduler_actual_hz_below_request,
    )
    tdma_config_failed = not tdma_config_check.get("match", False)
    tag_id_by_peer = tag_id_fallbacks_from_tdma_config(tdma_config_check)
    backfill_tag_ids(tr_rows, tag_id_by_peer)
    backfill_tag_ids(rfd_rows, tag_id_by_peer)

    tr_by_target: dict[str, list[dict]] = defaultdict(list)
    rfd_by_target: dict[str, list[dict]] = defaultdict(list)

    for row in tr_rows:
        key = row["peer_name"] or f"tag{row['tag_id']}"
        tr_by_target[key].append(row)
    for row in rfd_rows:
        key = row["peer_name"] or f"tag{row['tag_id']}"
        rfd_by_target[key].append(row)

    tr_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "sweep",
        "conn_id",
        "peer_name",
        "tag_id",
        "plan",
        "pmode",
        "anchor_id",
        "raw_mm",
        "range_mm",
        "quality_percent",
        "valid",
        "status",
        "quality_flag_percent",
        "first_to_last_us",
        "frame_us",
        "poll_count",
        "tr_version",
        "rx_mask",
        "air_us",
        "post_us",
        "cycle_us",
        "rx_seen",
        "imu_valid",
        "imu_n",
        "acc_norm_mean_mg",
        "acc_norm_std_mg",
        "acc_norm_min_mg",
        "acc_norm_max_mg",
        "imu_skip_count",
    ]

    rfd_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "sweep",
        "conn_id",
        "peer_name",
        "tag_id",
        "diag_source",
        "tr_diag_version",
        "rfd_version",
        "poll_seq",
        "anchor_id",
        "raw_mm",
        "resp_rx_ts",
        "carrier_integrator",
        "anchor_diag_valid",
        "anchor_diag_flags",
        "anchor_fp_index",
        "anchor_fp1",
        "anchor_fp2",
        "anchor_fp3",
        "anchor_fp_sum",
        "anchor_fp_sum_q8",
        "anchor_cir_pwr",
        "anchor_cir_pwr_q8",
        "anchor_rxpacc",
        "anchor_rxpacc_q8",
        "anchor_std_noise",
        "tag_diag_valid",
        "tag_diag_flags",
        "tag_fp_index",
        "tag_fp1",
        "tag_fp2",
        "tag_fp3",
        "tag_fp_sum",
        "tag_fp_sum_q8",
        "tag_cir_pwr",
        "tag_cir_pwr_q8",
        "tag_rxpacc",
        "tag_rxpacc_q8",
        "tag_std_noise",
    ]

    rfd_join_fields = [
        ("diag_source", "diag_source"),
        ("tr_diag_version", "tr_diag_version"),
        ("rfd_host_elapsed_s", "host_elapsed_s"),
        ("rfd_host_epoch_s", "host_epoch_s"),
        ("rfd_version", "rfd_version"),
        ("rfd_poll_seq", "poll_seq"),
        ("rfd_raw_mm", "raw_mm"),
        ("rfd_resp_rx_ts", "resp_rx_ts"),
        ("rfd_carrier_integrator", "carrier_integrator"),
        ("anchor_diag_valid", "anchor_diag_valid"),
        ("anchor_diag_flags", "anchor_diag_flags"),
        ("anchor_fp_index", "anchor_fp_index"),
        ("anchor_fp1", "anchor_fp1"),
        ("anchor_fp2", "anchor_fp2"),
        ("anchor_fp3", "anchor_fp3"),
        ("anchor_fp_sum", "anchor_fp_sum"),
        ("anchor_fp_sum_q8", "anchor_fp_sum_q8"),
        ("anchor_cir_pwr", "anchor_cir_pwr"),
        ("anchor_cir_pwr_q8", "anchor_cir_pwr_q8"),
        ("anchor_rxpacc", "anchor_rxpacc"),
        ("anchor_rxpacc_q8", "anchor_rxpacc_q8"),
        ("anchor_std_noise", "anchor_std_noise"),
        ("tag_diag_valid", "tag_diag_valid"),
        ("tag_diag_flags", "tag_diag_flags"),
        ("tag_fp_index", "tag_fp_index"),
        ("tag_fp1", "tag_fp1"),
        ("tag_fp2", "tag_fp2"),
        ("tag_fp3", "tag_fp3"),
        ("tag_fp_sum", "tag_fp_sum"),
        ("tag_fp_sum_q8", "tag_fp_sum_q8"),
        ("tag_cir_pwr", "tag_cir_pwr"),
        ("tag_cir_pwr_q8", "tag_cir_pwr_q8"),
        ("tag_rxpacc", "tag_rxpacc"),
        ("tag_rxpacc_q8", "tag_rxpacc_q8"),
        ("tag_std_noise", "tag_std_noise"),
    ]

    write_rows(session_dir / "tr_all.csv", tr_fields, tr_rows)
    write_rows(session_dir / "tag_rf_diag.csv", rfd_fields, rfd_rows)
    range_diag_joined_rows = build_range_diag_joined_rows(
        tr_rows, rfd_rows, tr_fields, rfd_join_fields
    )
    write_rows(
        session_dir / "range_diag_joined.csv",
        tr_fields + ["rfd_joined"] + [field for field, _ in rfd_join_fields],
        range_diag_joined_rows,
    )

    per_tag_summary: dict[str, dict] = {}

    def rows_for_target(mapping: dict[str, list[dict]], target: str) -> list[dict]:
        rows: list[dict] = []
        seen_ids: set[int] = set()
        for alias in target_aliases(target):
            for row in mapping.get(alias, []):
                marker = id(row)
                if marker not in seen_ids:
                    rows.append(row)
                    seen_ids.add(marker)
        return rows

    for target in targets:
        tag_dir = session_dir / target
        tag_dir.mkdir(parents=True, exist_ok=True)
        tr_target_rows = rows_for_target(tr_by_target, target)
        rfd_target_rows = rows_for_target(rfd_by_target, target)
        joined_target_rows = build_range_diag_joined_rows(
            tr_target_rows, rfd_target_rows, tr_fields, rfd_join_fields
        )
        write_rows(tag_dir / "tr.csv", tr_fields, tr_target_rows)
        write_rows(tag_dir / "tag_rf_diag.csv", rfd_fields, rfd_target_rows)
        write_rows(
            tag_dir / "range_diag_joined.csv",
            tr_fields + ["rfd_joined"] + [field for field, _ in rfd_join_fields],
            joined_target_rows,
        )

        per_tag_summary[target] = {
            "tr_rows": len(tr_target_rows),
            "tr_valid_rows": sum(1 for row in tr_target_rows if row["valid"]),
            "tr_diag_rows": sum(1 for row in tr_target_rows if row.get("diag_source")),
            "rfd_rows": len(rfd_target_rows),
            "rfd_joined_rows": sum(1 for row in joined_target_rows if row["rfd_joined"]),
            "sweep_validity": summarize_sweep_validity(tr_target_rows),
            "latest_tr": tr_target_rows[-1] if tr_target_rows else None,
            "anchors_seen": sorted(
                {row["anchor_id"] for row in tr_target_rows if row["valid"]}
            ),
            "tr_status_counts": {
                status: sum(1 for row in tr_target_rows if row["status"] == status)
                for status in sorted({row["status"] for row in tr_target_rows})
            },
        }

    summary = {
        "success": (
            (not interrupted)
            and (not startup_failed)
            and (not controller_lost)
            and (not no_tr_timeout)
            and (not tdma_config_failed)
        ),
        "interrupted": interrupted,
        "startup_failed": startup_failed,
        "startup_fail_targets": startup_fail_targets,
        "controller_lost": controller_lost,
        "no_tr_timeout": no_tr_timeout,
        "controller_recovery_attempts": controller_recovery_attempts,
        "controller_recovery_successes": controller_recovery_successes,
        "tdma_config_failed": tdma_config_failed,
        "tdma_config_check": tdma_config_check,
        "anchor_preflight": anchor_preflight,
        "cleanup": cleanup_result,
        "tag_cir": args.tag_cir,
        "tag_cir_range_phase": tag_cir_range_phase(args.tag_cir),
        "cir_full_phase": cir_full_phase,
        "port": args.port,
        "duration_s": args.duration,
        "elapsed_s": time.time() - start_wall,
        "session_dir": str(session_dir),
        "targets": targets,
        "expected_pmode": expected_pmode_by_target,
        "expected_freq_hz": expected_freq_by_target,
        "skipped_before_target_pmode": skipped_before_target_pmode,
        "freq_hz": {"tr": tr_hz},
        "tr_all": len(tr_rows),
        "tr_valid_all": sum(1 for row in tr_rows if row["valid"]),
        "tr_diag_all": sum(1 for row in tr_rows if row.get("diag_source")),
        "rfd_all": len(rfd_rows),
        "rfd_joined_all": sum(1 for row in range_diag_joined_rows if row["rfd_joined"]),
        "sweep_validity_all": summarize_sweep_validity(tr_rows),
        "connections": conn_meta,
        "per_tag": per_tag_summary,
        "raw_log": str(raw_log_path),
        "tr_all_csv": str(session_dir / "tr_all.csv"),
        "tag_rf_diag_csv": str(session_dir / "tag_rf_diag.csv"),
        "range_diag_joined_csv": str(session_dir / "range_diag_joined.csv"),
    }
    summary_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
