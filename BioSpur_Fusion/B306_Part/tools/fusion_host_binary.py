#!/usr/bin/env python3
"""Versioned COBS/CRC decoder for Fusion Master USB CDC records."""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = 0x5342
VERSION = 1
HEADER = struct.Struct("<HBBHHIQ")
CRC = struct.Struct("<H")

KIND_UWB = 1
KIND_TELEMETRY = 2
KIND_IMU = 3
KIND_REPLY = 4
KIND_TEXT = 5
KIND_QUEUE_COUNTERS = 6
KIND_QOS = 7
IMU_BATCH_MIN = 1
IMU_BATCH_MAX = 10
BSL_FLAG_SUPERFRAME_SHIFT = 3
BSL_FLAG_SUPERFRAME_MASK = 0x0F << BSL_FLAG_SUPERFRAME_SHIFT
BSL_FLAG_SUPERFRAME_VALID = 1 << 7


class FrameError(ValueError):
    pass


def decode_superframe_flags(flags: int) -> tuple[bool, int | None]:
    """Decode relay8's epoch label without changing legacy flag semantics."""
    valid = bool(flags & BSL_FLAG_SUPERFRAME_VALID)
    if not valid:
        return False, None
    return True, (flags & BSL_FLAG_SUPERFRAME_MASK) >> BSL_FLAG_SUPERFRAME_SHIFT


def resolve_superframe_mod16(
    fitted_epoch: int, carried_mod16: int | None, valid: bool
) -> int | None:
    """Choose the absolute epoch nearest a fit using a valid mod-16 label."""
    if not valid or carried_mod16 is None:
        return None
    if not 0 <= carried_mod16 <= 15:
        raise ValueError("carried superframe index is not modulo 16")
    base = fitted_epoch & ~0x0F
    candidates = (
        base + carried_mod16 - 16,
        base + carried_mod16,
        base + carried_mod16 + 16,
    )
    return min(candidates, key=lambda value: (abs(value - fitted_epoch), value))


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    output = bytearray(b"\x00")
    code_index = 0
    code = 1
    for value in data:
        if value == 0:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1
        else:
            output.append(value)
            code += 1
            if code == 0xFF:
                output[code_index] = code
                code_index = len(output)
                output.append(0)
                code = 1
    output[code_index] = code
    output.append(0)
    return bytes(output)


def cobs_decode(encoded: bytes) -> bytes:
    output = bytearray()
    index = 0
    while index < len(encoded):
        code = encoded[index]
        index += 1
        if code == 0 or index + code - 1 > len(encoded):
            raise FrameError("invalid COBS record")
        output.extend(encoded[index : index + code - 1])
        index += code - 1
        if code != 0xFF and index < len(encoded):
            output.append(0)
    return bytes(output)


@dataclass(frozen=True)
class HostFrame:
    kind: int
    node_id: int
    sequence: int
    master_arrival_ms: int
    payload: bytes

    @property
    def node_name(self) -> str:
        return f"BSF{self.node_id:04X}" if self.node_id else "-"


def encode_frame(frame: HostFrame) -> bytes:
    header = HEADER.pack(
        MAGIC,
        VERSION,
        frame.kind,
        frame.node_id,
        len(frame.payload),
        frame.sequence,
        frame.master_arrival_ms,
    )
    raw = header + frame.payload
    return cobs_encode(raw + CRC.pack(crc16_ccitt_false(raw)))


def decode_frame(encoded: bytes) -> HostFrame:
    raw = cobs_decode(encoded)
    if len(raw) < HEADER.size + CRC.size:
        raise FrameError("short host frame")
    expected_crc = CRC.unpack_from(raw, len(raw) - CRC.size)[0]
    body = raw[:-CRC.size]
    if crc16_ccitt_false(body) != expected_crc:
        raise FrameError("host frame CRC mismatch")
    magic, version, kind, node_id, payload_len, sequence, master_ms = HEADER.unpack_from(body)
    if magic != MAGIC or version != VERSION:
        raise FrameError(f"unsupported host frame magic/version {magic:04x}/{version}")
    payload = body[HEADER.size:]
    if len(payload) != payload_len:
        raise FrameError(f"host payload length {len(payload)} != {payload_len}")
    return HostFrame(kind, node_id, sequence, master_ms, payload)


class FrameStreamDecoder:
    def __init__(self) -> None:
        self.pending = bytearray()
        self.errors = 0

    def feed(self, data: bytes) -> list[HostFrame]:
        self.pending.extend(data)
        frames: list[HostFrame] = []
        while True:
            boundary = self.pending.find(0)
            if boundary < 0:
                break
            encoded = bytes(self.pending[:boundary])
            del self.pending[: boundary + 1]
            if not encoded:
                continue
            try:
                frames.append(decode_frame(encoded))
            except FrameError:
                self.errors += 1
        return frames


class _Cursor:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def take(self, fmt: str):
        parser = struct.Struct("<" + fmt)
        if self.offset + parser.size > len(self.data):
            raise FrameError("short record payload")
        values = parser.unpack_from(self.data, self.offset)
        self.offset += parser.size
        return values[0] if len(values) == 1 else values


def _decode_telemetry(frame: HostFrame) -> str:
    c = _Cursor(frame.payload)
    version, kind, declared = c.take("BBH")
    if (
        kind != 2
        or declared not in (235, 239, 243)
        or len(frame.payload) not in (declared, declared + 24)
    ):
        raise FrameError("invalid telemetry record")
    names_u32_a = (
        "node_ms", "bytes", "frames", "crc", "header", "ring_drop",
        "sweep_drop", "duplicate", "reorder", "notify_ok", "drop_unsub",
        "drop_err",
    )
    values: dict[str, int] = {name: c.take("I") for name in names_u32_a}
    values["notify_errno"] = c.take("i")
    values["uart_restarts"] = c.take("I")
    values["uart_err"] = c.take("i")
    for name in (
        "last_sweep", "rise_n", "fall_n", "boot_discard", "edge_qdrop",
        "orphan_strobe", "orphan_edge", "orphan_frame", "near_window",
    ):
        values[name] = c.take("I")
    for name in ("have", "subscribed", "capture_flags", "timer", "timer_bits"):
        values[name] = c.take("B")
    values["window_us"] = c.take("H")
    for name in (
        "timer_wraps", "watchdog_feeds", "reset_reason", "imu_pulls",
        "imu_dup", "imu_i2c_err", "imu_records", "ctrl_rx", "ctrl_bad_bsf",
        "relay_tx", "relay_ack", "relay_timeout",
    ):
        values[name] = c.take("I")
    values["imu_rate"] = c.take("H")
    for name in (
        "imu_batch", "imu_active", "imu_health_class", "imu_health_active",
        "imu_health_latched", "imu_ext",
    ):
        values[name] = c.take("B")
    for name in (
        "imu_hreset", "imu_hfrozen", "imu_hrate", "imu_hcanary",
        "imu_hplaus", "imu_hdead", "imu_hident", "imu_hi2c",
        "imu_hrecover_ok", "imu_hrecover_fail", "imu_pull_legacy_us",
        "imu_pull_extended_us",
    ):
        values[name] = c.take("I")
    for name in ("imu_last_good_us", "imu_fault_us", "imu_recovered_us"):
        values[name] = c.take("Q")
    values["imu_missed_deadlines"] = c.take("I") if declared >= 239 else 0
    values["imu_pull_late_max_us"] = c.take("H") if declared >= 243 else 0
    values["imu_pull_dur_max_us"] = c.take("H") if declared >= 243 else 0
    if len(frame.payload) == declared + 24:
        for name in (
            "master_rx", "malformed", "logger_drop", "master_rx_total",
            "malformed_total", "logger_drop_total",
        ):
            values[name] = c.take("I")
    if c.offset != len(frame.payload):
        raise FrameError("telemetry payload has trailing bytes")
    values["imu_health"] = (
        f"{values.pop('imu_health_class')}/"
        f"{values.pop('imu_health_active')}/"
        f"{values.pop('imu_health_latched')}"
    )
    fields = " ".join(f"{key}={value}" for key, value in values.items())
    return f"FUSION_TELEMETRY proto={version} name={frame.node_name} {fields}"


def _decode_imu(frame: HostFrame) -> str:
    if len(frame.payload) < 14:
        raise FrameError("short IMU record")
    version, count, sequence, base_us, temperature = struct.unpack_from(
        "<BBHQh", frame.payload
    )
    expected = 14 + count * 14
    if count not in range(IMU_BATCH_MIN, IMU_BATCH_MAX + 1) or len(frame.payload) != expected:
        raise FrameError("invalid IMU record length")
    samples = []
    offset = 14
    for _ in range(count):
        sample = struct.unpack_from("<Hhhhhhh", frame.payload, offset)
        samples.append(",".join(str(value) for value in sample))
        offset += 14
    return (
        f"FUSION_IMU proto={version} name={frame.node_name} "
        f"master_ms={frame.master_arrival_ms} seq={sequence} base_us={base_us} "
        f"n={count} temp_raw={temperature} samples={';'.join(samples)}"
    )


def _decode_reply(frame: HostFrame) -> str:
    if len(frame.payload) < 7:
        raise FrameError("short reply record")
    version, kind, declared, source, correlation = struct.unpack_from(
        "<BBHBH", frame.payload
    )
    text = frame.payload[7:].decode("utf-8", errors="replace")
    source_name = "TAG" if source == 1 else "B306"
    return (
        f"FUSION_REPLY proto={version} name={frame.node_name} "
        f"master_ms={frame.master_arrival_ms} source={source_name} "
        f"correlation={correlation} text={text}"
    )


def _decode_queue_counters(frame: HostFrame) -> str:
    if len(frame.payload) != 74:
        raise FrameError("invalid queue-counter record length")
    (
        version,
        kind,
        declared,
        node_ms,
        q_drop_imu,
        q_drop_uwb,
        q_drop_ctl,
        q_hwm_imu,
        q_hwm_uwb,
        q_hwm_ctl,
        publisher_count,
        publisher_max_us,
        enq_imu,
        enq_uwb,
        enq_ctl,
        abort_imu,
        abort_uwb,
        abort_ctl,
    ) = struct.unpack("<BBHIIIIHHHIIIIIIII", frame.payload[:58])
    (
        delivered_imu,
        delivered_uwb,
        delivered_ctl,
        imu_epoch_defer_drop,
    ) = struct.unpack("<IIII", frame.payload[58:])
    if kind != 5 or declared != 58:
        raise FrameError("invalid queue-counter header")
    return (
        f"FUSION_QUEUE proto={version} name={frame.node_name} "
        f"master_ms={frame.master_arrival_ms} node_ms={node_ms} "
        f"q_drop_imu={q_drop_imu} q_drop_uwb={q_drop_uwb} "
        f"q_drop_ctl={q_drop_ctl} q_hwm_imu={q_hwm_imu} "
        f"q_hwm_uwb={q_hwm_uwb} q_hwm_ctl={q_hwm_ctl} "
        f"publisher_count={publisher_count} "
        f"publisher_max_us={publisher_max_us} "
        f"enq_imu={enq_imu} enq_uwb={enq_uwb} enq_ctl={enq_ctl} "
        f"abort_imu={abort_imu} abort_uwb={abort_uwb} "
        f"abort_ctl={abort_ctl} delivered_imu={delivered_imu} "
        f"delivered_uwb={delivered_uwb} delivered_ctl={delivered_ctl} "
        f"imu_epoch_defer_drop={imu_epoch_defer_drop}"
    )


def _decode_qos(frame: HostFrame) -> str:
    if len(frame.payload) != 138:
        raise FrameError("invalid QoS record length")
    values = struct.unpack("<BBH14I2H37H", frame.payload)
    (
        version,
        spacing_mode,
        conn_handle,
        window_start_ms,
        window_duration_ms,
        spacing_us,
        spacing_generation,
        report_count,
        event_counter_gap_count,
        crc_ok_count,
        crc_error_count,
        nak_count,
        rx_timeout_count,
        imu_epoch_defer_drop,
        delivered_imu,
        delivered_uwb,
        delivered_ctl,
        first_event_counter,
        last_event_counter,
        *channels,
    ) = values
    if version != 1 or spacing_mode not in (0, 1):
        raise FrameError("invalid QoS record header")
    channel_text = ",".join(str(value) for value in channels)
    return (
        f"FUSION_QOS version={version} name={frame.node_name} "
        f"master_ms={frame.master_arrival_ms} "
        f"spacing={'ON' if spacing_mode else 'OFF'} "
        f"spacing_us={spacing_us} spacing_generation={spacing_generation} "
        f"handle={conn_handle} window_start_ms={window_start_ms} "
        f"window_ms={window_duration_ms} reports={report_count} "
        f"event_gaps={event_counter_gap_count} crc_ok={crc_ok_count} "
        f"crc_error={crc_error_count} nak={nak_count} "
        f"rx_timeout={rx_timeout_count} first_event={first_event_counter} "
        f"last_event={last_event_counter} "
        f"imu_epoch_defer_drop={imu_epoch_defer_drop} "
        f"delivered_imu={delivered_imu} delivered_uwb={delivered_uwb} "
        f"delivered_ctl={delivered_ctl} channels={channel_text}"
    )


def _decode_uwb(frame: HostFrame) -> str:
    if len(frame.payload) != 184:
        raise FrameError("invalid UWB record length")
    version, kind, declared, node_sequence, node_ms = struct.unpack_from(
        "<BBHII", frame.payload
    )
    if kind != 1 or declared != 184:
        raise FrameError("invalid UWB header")
    body = frame.payload[12:102]
    capture = frame.payload[102:]
    sweep = struct.unpack_from("<I", body, 0)[0]
    poll_tx = int.from_bytes(body[4:9], "little")
    identity = struct.unpack_from("<H", body, 9)[0]
    logical = body[11]
    anchor_ids = body[16:24]
    ranges = struct.unpack_from("<8H", body, 32)
    valid, flags = body[88], body[89]
    sf_valid, sf_mod16 = decode_superframe_flags(flags)
    timestamps = struct.unpack_from("<5Q", capture, 0)
    counts = struct.unpack_from("<9I", capture, 40)
    window, verdict, edge, candidates, capture_flags = struct.unpack_from(
        "<HBBBB", capture, 76
    )
    verdict_names = ("healthy", "b306_missed_edge", "tag_no_poll", "contradiction")
    edge_names = ("none", "active_high", "active_low", "rising_only", "falling_only")
    ranges_text = ",".join(
        f"{anchor}:{distance}"
        for anchor, distance in zip(anchor_ids, ranges)
        if anchor != 0xFF
    ) or "-"
    absent = (1 << 64) - 1
    def ts(value: int) -> str:
        return "-" if value == absent else str(value)
    return (
        f"FUSION_UWB proto={version} name={frame.node_name} "
        f"master_ms={frame.master_arrival_ms} node_ms={node_ms} pkt={node_sequence} "
        f"sweep={sweep} identity={identity:04X} logical={logical} poll_tx={poll_tx:010X} "
        f"frame_us={timestamps[0]} strobe_us={ts(timestamps[1])} "
        f"rise_us={ts(timestamps[2])} fall_us={ts(timestamps[3])} "
        f"pair_dt_us={'-' if counts[0] == 0xFFFFFFFF else counts[0]} "
        f"verdict={verdict_names[verdict] if verdict < len(verdict_names) else 'invalid'} "
        f"edge={edge_names[edge] if edge < len(edge_names) else 'invalid'} "
        f"candidates={candidates} window_us={window} valid=0x{valid:02x} flags=0x{flags:02x} "
        f"sf_valid={int(sf_valid)} sf_mod16={'-' if sf_mod16 is None else sf_mod16} "
        f"strobe_sent={int(bool(flags & 1))} rise_n={counts[1]} fall_n={counts[2]} "
        f"boot_discard={counts[3]} edge_qdrop={counts[4]} orphan_strobe={counts[5]} "
        f"orphan_edge={counts[6]} orphan_frame={counts[7]} near_window={counts[8]} "
        f"last_orphan_us={ts(timestamps[4])} capture_flags=0x{capture_flags:02x} "
        f"ranges={ranges_text}"
    )


def frame_to_line(frame: HostFrame) -> str:
    if frame.kind == KIND_TEXT:
        return frame.payload.decode("utf-8", errors="replace").strip("\r\n")
    if frame.kind == KIND_UWB:
        return _decode_uwb(frame)
    if frame.kind == KIND_TELEMETRY:
        return _decode_telemetry(frame)
    if frame.kind == KIND_IMU:
        return _decode_imu(frame)
    if frame.kind == KIND_REPLY:
        return _decode_reply(frame)
    if frame.kind == KIND_QUEUE_COUNTERS:
        return _decode_queue_counters(frame)
    if frame.kind == KIND_QOS:
        return _decode_qos(frame)
    raise FrameError(f"unknown host record kind {frame.kind}")
