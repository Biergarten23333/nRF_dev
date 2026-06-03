#!/usr/bin/env python3
import argparse
import csv
import glob
import math
import re
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import serial


ANCHORS = "ABCDEFGH"
STOP = False


def on_stop(_signum, _frame):
    global STOP
    STOP = True


def anchor_label(anchor_id: int) -> str:
    return ANCHORS[anchor_id] if 0 <= anchor_id < len(ANCHORS) else f"A{anchor_id}"


def parse_port_spec(spec: str):
    if "=" in spec:
        label, path = spec.split("=", 1)
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "USB"
        return label, path
    return "USB", spec


def find_record(line: str, prefix: str):
    idx = line.find(prefix)
    if idx < 0:
        return None
    return line[idx:].split(";")


def parse_meta(line: str):
    parts = find_record(line, "ACIRM;")
    if parts is not None and len(parts) >= 15 and parts[0] == "ACIRM" and parts[1] == "1":
        try:
            return {
                "stream": "anchor",
                "seq": int(parts[2]),
                "receiver_anchor_id": int(parts[3]),
                "source_kind": parts[4],
                "source_id": int(parts[5]),
                "source_addr": parts[6],
                "raw_distance_mm": int(parts[7]),
                "rx_timestamp": int(parts[8]),
                "carrier_integrator": int(parts[9]),
                "first_path": int(parts[10]),
                "fp_amp_sum": int(parts[11]),
                "max_growth_cir": int(parts[12]),
                "std_noise": int(parts[13]),
                "acc_len": int(parts[14]),
            }
        except ValueError:
            return None

    parts = find_record(line, "CIRM;")
    if parts is not None and len(parts) >= 12 and parts[0] == "CIRM" and parts[1] == "1":
        try:
            return {
                "stream": "tag",
                "seq": int(parts[2]),
                "receiver_anchor_id": int(parts[3]),
                "source_kind": "T",
                "source_id": -1,
                "source_addr": "",
                "raw_distance_mm": int(parts[4]),
                "rx_timestamp": int(parts[5]),
                "carrier_integrator": int(parts[6]),
                "first_path": int(parts[7]),
                "fp_amp_sum": int(parts[8]),
                "max_growth_cir": int(parts[9]),
                "std_noise": int(parts[10]),
                "acc_len": int(parts[11]),
            }
        except ValueError:
            return None

    return None


def parse_chunk(line: str):
    parts = find_record(line, "ACIRD;")
    if parts is not None and len(parts) >= 9 and parts[0] == "ACIRD" and parts[1] == "1":
        try:
            return {
                "stream": "anchor",
                "seq": int(parts[2]),
                "receiver_anchor_id": int(parts[3]),
                "source_kind": parts[4],
                "source_id": int(parts[5]),
                "offset": int(parts[6]),
                "length": int(parts[7]),
                "hex": parts[8].strip(),
            }
        except ValueError:
            return None

    parts = find_record(line, "CIRD;")
    if parts is not None and len(parts) >= 7 and parts[0] == "CIRD" and parts[1] == "1":
        try:
            return {
                "stream": "tag",
                "seq": int(parts[2]),
                "receiver_anchor_id": int(parts[3]),
                "source_kind": "T",
                "source_id": -1,
                "offset": int(parts[4]),
                "length": int(parts[5]),
                "hex": parts[6].strip(),
            }
        except ValueError:
            return None

    return None


def parse_done(line: str):
    parts = find_record(line, "ACIRE;")
    if parts is not None and len(parts) >= 7 and parts[0] == "ACIRE" and parts[1] == "1":
        try:
            return {
                "stream": "anchor",
                "seq": int(parts[2]),
                "receiver_anchor_id": int(parts[3]),
                "source_kind": parts[4],
                "source_id": int(parts[5]),
                "acc_len": int(parts[6]),
            }
        except ValueError:
            return None

    parts = find_record(line, "CIRE;")
    if parts is not None and len(parts) >= 5 and parts[0] == "CIRE" and parts[1] == "1":
        try:
            return {
                "stream": "tag",
                "seq": int(parts[2]),
                "receiver_anchor_id": int(parts[3]),
                "source_kind": "T",
                "source_id": -1,
                "acc_len": int(parts[4]),
            }
        except ValueError:
            return None

    return None


def frame_key(port_label: str, record: dict):
    return (
        port_label,
        record["stream"],
        record["seq"],
        record["receiver_anchor_id"],
        record["source_kind"],
        record["source_id"],
    )


def int16_le(buf: bytes, offset: int) -> int:
    value = buf[offset] | (buf[offset + 1] << 8)
    return value - 65536 if value & 0x8000 else value


def downsample_accumulator(buf: bytes, bins: int = 96):
    sample_count = len(buf) // 4
    if sample_count <= 0:
        return []
    mags = []
    for i in range(sample_count):
        off = i * 4
        i_val = int16_le(buf, off)
        q_val = int16_le(buf, off + 2)
        mags.append(math.sqrt(i_val * i_val + q_val * q_val))
    out = []
    for b in range(bins):
        lo = int(b * sample_count / bins)
        hi = max(lo + 1, int((b + 1) * sample_count / bins))
        out.append(max(mags[lo:hi]))
    peak = max(out) if out else 1.0
    if peak <= 0:
        return [0 for _ in out]
    return [int(round(v * 1000.0 / peak)) for v in out]


def append_session_note(notes_path: Path, capture_id: str, capture_dir: Path, target: str, duration: float, frames: int):
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    if not notes_path.exists():
        notes_path.write_text("timestamp,id,type,path,notes\n", encoding="utf-8")
    with notes_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            capture_id,
            "cir_raw_full",
            str(capture_dir),
            f"target={target}; duration_s={duration:.0f}; frames={frames}",
        ])


class CaptureState:
    def __init__(self, args, capture_dir: Path, raw_log, writer, meta_f, bin_dir: Path):
        self.args = args
        self.capture_dir = capture_dir
        self.raw_log = raw_log
        self.writer = writer
        self.meta_f = meta_f
        self.bin_dir = bin_dir
        self.frames = {}
        self.frame_count = 0
        self.lock = threading.Lock()

    def log_raw(self, port_label: str, line: str):
        with self.lock:
            self.raw_log.write(f"[{port_label}] {line}\n")
            self.raw_log.flush()

    def put_meta(self, port_label: str, meta: dict):
        key = frame_key(port_label, meta)
        with self.lock:
            self.frames[key] = {
                "meta": meta,
                "buf": bytearray(meta["acc_len"]),
                "got": 0,
            }

    def put_chunk(self, port_label: str, chunk: dict):
        key = frame_key(port_label, chunk)
        try:
            data = bytes.fromhex(chunk["hex"])
        except ValueError:
            return
        if len(data) != chunk["length"]:
            return
        with self.lock:
            frame = self.frames.get(key)
            if frame is None:
                return
            offset = chunk["offset"]
            end_off = min(offset + chunk["length"], len(frame["buf"]))
            if offset < 0 or offset >= len(frame["buf"]) or end_off <= offset:
                return
            frame["buf"][offset:end_off] = data[: end_off - offset]
            frame["got"] += end_off - offset

    def finish_frame(self, port_label: str, done: dict):
        key = frame_key(port_label, done)
        with self.lock:
            frame = self.frames.pop(key, None)
            if frame is not None:
                self.frame_count += 1
                frame_index = self.frame_count
        if frame is None:
            return

        meta = frame["meta"]
        acc_len = done["acc_len"]
        buf = bytes(frame["buf"][:acc_len])
        rx_label = anchor_label(meta["receiver_anchor_id"])
        src_id = meta["source_id"]
        src_label = anchor_label(src_id) if src_id >= 0 else self.args.target
        stream = meta["stream"]
        bin_name = (
            f"{stream}_{port_label}_frame_{frame_index:08d}_seq_{meta['seq']:08d}_"
            f"rx{rx_label}_src{src_label}.bin"
        )
        bin_path = self.bin_dir / bin_name
        bin_path.write_bytes(buf)

        row = {
            "host_time_iso": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "target": self.args.target,
            "port_label": port_label,
            "stream": stream,
            "seq": meta["seq"],
            "receiver_anchor_id": meta["receiver_anchor_id"],
            "receiver_anchor": rx_label,
            "source_kind": meta["source_kind"],
            "source_id": meta["source_id"],
            "source_anchor": src_label,
            "source_addr": meta["source_addr"],
            "raw_distance_mm": meta["raw_distance_mm"],
            "rx_timestamp": meta["rx_timestamp"],
            "carrier_integrator": meta["carrier_integrator"],
            "first_path": meta["first_path"],
            "fp_amp_sum": meta["fp_amp_sum"],
            "max_growth_cir": meta["max_growth_cir"],
            "std_noise": meta["std_noise"],
            "acc_len": acc_len,
            "bin_path": str(bin_path),
        }
        wave = downsample_accumulator(buf)
        with self.lock:
            self.writer.writerow(row)
            self.meta_f.flush()
            print(
                "CIRP;1;{seq};{anchor};{raw};{fp};{amp};{peak};{noise};{length};{wave}".format(
                    seq=meta["seq"],
                    anchor=meta["receiver_anchor_id"],
                    raw=meta["raw_distance_mm"],
                    fp=meta["first_path"],
                    amp=meta["fp_amp_sum"],
                    peak=meta["max_growth_cir"],
                    noise=meta["std_noise"],
                    length=acc_len,
                    wave=",".join(str(v) for v in wave),
                ),
                flush=True,
            )


def capture_port(port_label: str, port_path: str, state: CaptureState, end_time: float):
    if not Path(port_path).exists():
        print(f"[CIRRAW] skip missing port {port_label}={port_path}", flush=True)
        return
    try:
        with serial.Serial(port_path, state.args.baud, timeout=0.2, write_timeout=2, exclusive=True) as ser:
            ser.reset_input_buffer()
            print(f"[CIRRAW] port start {port_label}={port_path}", flush=True)
            while time.monotonic() < end_time and not STOP:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").rstrip()
                state.log_raw(port_label, line)

                meta = parse_meta(line)
                if meta is not None:
                    state.put_meta(port_label, meta)
                    continue

                chunk = parse_chunk(line)
                if chunk is not None:
                    state.put_chunk(port_label, chunk)
                    continue

                done = parse_done(line)
                if done is not None:
                    state.finish_frame(port_label, done)
    except Exception as exc:
        print(f"[CIRRAW] port error {port_label}={port_path}: {exc}", flush=True)


def control_anchor_cir_mode(control_port: str, mode: str, wait_s: float):
    if not control_port:
        return
    if not Path(control_port).exists():
        candidates = []
        for pattern in (
            "/dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_*-if00",
            "/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_*-if00",
        ):
            candidates.extend(sorted(glob.glob(pattern)))
        if candidates:
            print(
                f"[CIRRAW] control fallback: {control_port} -> {candidates[0]}",
                flush=True,
            )
            control_port = candidates[0]
        else:
            print(f"[CIRRAW] control warning: missing Master_Anchor CDC {control_port}", flush=True)
            return
    cmd = f"anchor role all matrix cir {mode}"
    try:
        with serial.Serial(control_port, 115200, timeout=0.2, write_timeout=2, exclusive=True) as ser:
            ser.reset_input_buffer()
            print(f"[CIRRAW] control >>> {cmd}", flush=True)
            ser.write((cmd + "\n").encode("utf-8"))
            ser.flush()
            end = time.monotonic() + wait_s
            while time.monotonic() < end and not STOP:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").rstrip()
                print(f"[CIRRAW_CTRL] {line}", flush=True)
                if "anchor role rc=0 target=all" in line:
                    break
    except Exception as exc:
        print(f"[CIRRAW] control warning: {exc}", flush=True)


def main():
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", action="append", required=True, help="USB CDC port, optionally LABEL=/dev/...")
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--capture-root", required=True)
    ap.add_argument("--target", default="BSF66F")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--control-port", default="", help="Master_Anchor CDC control port used to switch anchors into CIR=FULL")
    ap.add_argument("--control-wait-s", type=float, default=18.0)
    args = ap.parse_args()

    port_specs = [parse_port_spec(spec) for spec in args.port]
    existing = [(label, path) for label, path in port_specs if Path(path).exists()]
    if not existing:
        raise SystemExit("[CIRRAW] no requested USB CDC ports exist")

    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.target).strip("_") or "tag"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_id = f"CIRRAW_{safe_target}_{stamp}"
    capture_root = Path(args.capture_root)
    capture_dir = capture_root / capture_id
    raw_log = capture_dir / "cir_full_raw_serial.log"
    meta_csv = capture_dir / "cir_full_meta.csv"
    bin_dir = capture_dir / "accumulator_bin"
    notes_path = capture_root / "session_notes.csv"

    capture_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    fields = [
        "host_time_iso",
        "target",
        "port_label",
        "stream",
        "seq",
        "receiver_anchor_id",
        "receiver_anchor",
        "source_kind",
        "source_id",
        "source_anchor",
        "source_addr",
        "raw_distance_mm",
        "rx_timestamp",
        "carrier_integrator",
        "first_path",
        "fp_amp_sum",
        "max_growth_cir",
        "std_noise",
        "acc_len",
        "bin_path",
    ]

    print(f"[CIRRAW] target={args.target} duration_s={args.seconds:.0f}", flush=True)
    print(f"[CIRRAW] save_dir={capture_dir}", flush=True)
    for label, path in port_specs:
        print(f"[CIRRAW] request port {label}={path}", flush=True)

    control_anchor_cir_mode(args.control_port, "full", args.control_wait_s)
    end_time = time.monotonic() + args.seconds
    try:
        with raw_log.open("a", encoding="utf-8") as raw_f, \
                meta_csv.open("w", encoding="utf-8", newline="") as meta_f:
            writer = csv.DictWriter(meta_f, fieldnames=fields)
            writer.writeheader()
            meta_f.flush()
            state = CaptureState(args, capture_dir, raw_f, writer, meta_f, bin_dir)
            print("[CIRRAW] capture start", flush=True)
            threads = [
                threading.Thread(
                    target=capture_port,
                    args=(label, path, state, end_time),
                    daemon=True,
                )
                for label, path in port_specs
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                remaining = max(0.0, end_time - time.monotonic() + 1.0)
                thread.join(timeout=remaining)
    finally:
        control_anchor_cir_mode(args.control_port, "0", max(6.0, args.control_wait_s / 2.0))

    frame_count = state.frame_count if state is not None else 0
    append_session_note(notes_path, capture_id, capture_dir, args.target, args.seconds, frame_count)
    print(f"[CIRRAW] capture done frames={frame_count}", flush=True)
    print(f"[CIRRAW] saved_meta={meta_csv}", flush=True)
    print(f"[CIRRAW] saved_raw={raw_log}", flush=True)


if __name__ == "__main__":
    main()
