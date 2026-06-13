#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import serial

try:
    import yaml
except ImportError:  # pragma: no cover - handled at runtime
    yaml = None

if __package__:
    from .parse_emg import emg_record_from_line
    from .parse_glove import GLOVE_COLUMNS, is_glove_header, parse_glove_csv_line
    from .ports import (
        B120_BY_ID_DEFAULT,
        GLOVE_PORT_HINT,
        format_ports,
        resolve_b120_port,
        resolve_glove_port,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from parse_emg import emg_record_from_line
    from parse_glove import GLOVE_COLUMNS, is_glove_header, parse_glove_csv_line
    from ports import (
        B120_BY_ID_DEFAULT,
        GLOVE_PORT_HINT,
        format_ports,
        resolve_b120_port,
        resolve_glove_port,
    )


DATASET_ROOT_DEFAULT = Path("/mnt/DatenBankHDD/datasets/BioSpur_GR")
ACTION_CONFIG_DEFAULT = (
    Path(__file__).resolve().parents[1] / "configs" / "actions_basic.yaml"
)


@dataclass
class Counters:
    lock: threading.Lock = field(default_factory=threading.Lock)
    emg_lines: int = 0
    emg_recv_hex: int = 0
    emg_rate1000: int = 0
    glove_lines: int = 0
    glove_samples: int = 0
    glove_unparsed: int = 0
    events: int = 0
    errors: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "emg_lines": self.emg_lines,
                "emg_recv_hex": self.emg_recv_hex,
                "emg_rate1000": self.emg_rate1000,
                "glove_lines": self.glove_lines,
                "glove_samples": self.glove_samples,
                "glove_unparsed": self.glove_unparsed,
                "events": self.events,
                "errors": list(self.errors),
            }

    def add_error(self, message: str) -> None:
        with self.lock:
            self.errors.append(message)


class SessionFiles:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.log_lock = threading.Lock()
        self.emg_lock = threading.Lock()
        self.glove_lock = threading.Lock()
        self.event_lock = threading.Lock()

        self.collector_log = (session_dir / "collector.log").open("a", buffering=1)
        self.emg_raw = (session_dir / "emg_raw.jsonl").open("a", buffering=1)
        self.glove_raw = (session_dir / "glove_raw.csv").open(
            "a", newline="", buffering=1
        )
        self.events = (session_dir / "events.jsonl").open("a", buffering=1)

        self.glove_writer = csv.DictWriter(
            self.glove_raw, fieldnames=["host_time_ns"] + GLOVE_COLUMNS
        )
        if self.glove_raw.tell() == 0:
            self.glove_writer.writeheader()

    def close(self) -> None:
        for f in (self.collector_log, self.emg_raw, self.glove_raw, self.events):
            f.flush()
            f.close()

    def log(self, message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
        with self.log_lock:
            self.collector_log.write(line + "\n")
        print(line, flush=True)

    def write_emg_record(self, record: dict[str, Any]) -> None:
        with self.emg_lock:
            self.emg_raw.write(json.dumps(record, separators=(",", ":")) + "\n")

    def write_glove_row(self, row: dict[str, Any]) -> None:
        with self.glove_lock:
            self.glove_writer.writerow(row)

    def write_event(self, counters: Counters, event: dict[str, Any]) -> None:
        event = dict(event)
        event.setdefault("host_time_ns", time.time_ns())
        with self.event_lock:
            self.events.write(json.dumps(event, separators=(",", ":")) + "\n")
        with counters.lock:
            counters.events += 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_attached_channels(value: str | None) -> list[int]:
    if not value:
        return []
    channels: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        channels.append(int(part))
    return channels


def make_session_id(raw_dir: Path, subject: str, explicit: str | None) -> str:
    if explicit:
        return explicit

    date_prefix = datetime.now().strftime("%Y-%m-%d")
    subject_dir = raw_dir / subject
    existing = set()
    if subject_dir.exists():
        for p in subject_dir.iterdir():
            if p.is_dir() and p.name.startswith(date_prefix + "_s"):
                existing.add(p.name)

    for idx in range(1, 1000):
        candidate = f"{date_prefix}_s{idx:03d}"
        if candidate not in existing:
            return candidate
    raise RuntimeError(f"Too many sessions for {date_prefix} in {subject_dir}")


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required for guided action mode")
    return yaml.safe_load(path.read_text()) or {}


def make_manifest(args: argparse.Namespace, session_id: str, session_dir: Path) -> dict[str, Any]:
    attached_channels = parse_attached_channels(args.attached_channels)
    return {
        "dataset_version": args.dataset_version,
        "session_id": session_id,
        "subject_id": args.subject,
        "created_utc": utc_now_iso(),
        "session_dir": str(session_dir),
        "emg": {
            "enabled": not args.no_emg,
            "sample_rate_sps": args.emg_rate,
            "channels_enabled_mask": "0xFF",
            "channels_physically_attached": attached_channels,
            "b120_port": args.b120_port or B120_BY_ID_DEFAULT,
            "baud": args.b120_baud,
        },
        "glove": {
            "enabled": not args.no_glove,
            "sample_rate_hz": args.glove_rate,
            "port": args.glove_port or GLOVE_PORT_HINT,
            "baud": args.glove_baud,
            "columns": ["host_time_ns"] + GLOVE_COLUMNS,
        },
        "collection": {
            "collector": "dataset_pipeline/acquire/collect_session.py",
            "sync_method": "pc_host_time_ns_first_pass",
            "duration_s": args.duration,
            "guided": args.guided,
            "action_config": str(args.action_config) if args.action_config else None,
        },
        "notes": args.notes or "",
        "status": "running",
    }


def write_manifest(session_dir: Path, manifest: dict[str, Any]) -> None:
    path = session_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def emg_reader(
    args: argparse.Namespace,
    files: SessionFiles,
    counters: Counters,
    stop_event: threading.Event,
) -> None:
    try:
        port = resolve_b120_port(args.b120_port)
        files.log(f"EMG opening B120 port={port} baud={args.b120_baud}")
        ser = serial.Serial(port, args.b120_baud, timeout=0.2)
    except Exception as exc:
        message = f"EMG open failed: {exc}"
        counters.add_error(message)
        files.log(message)
        if args.require_emg:
            stop_event.set()
        return

    try:
        time.sleep(0.8)
        ser.reset_input_buffer()
        for command in ("status", "rx"):
            ser.write((command + "\n").encode())
            ser.flush()
            files.log(f"EMG command sent: {command}")
            time.sleep(0.2)

        while not stop_event.is_set():
            raw = ser.readline()
            if not raw:
                continue
            host_time_ns = time.time_ns()
            line = raw.decode(errors="replace").strip()
            if not line:
                continue

            record = emg_record_from_line(host_time_ns, line)
            files.write_emg_record(record)

            with counters.lock:
                counters.emg_lines += 1
                if line.startswith("RECV_HEX"):
                    counters.emg_recv_hex += 1
                if record.get("sample_rate_sps") == 1000:
                    counters.emg_rate1000 += 1
    except Exception as exc:
        message = f"EMG read failed: {exc}"
        counters.add_error(message)
        files.log(message)
        if args.require_emg:
            stop_event.set()
    finally:
        try:
            ser.write(b"disconnect\n")
            ser.flush()
            files.log("EMG command sent: disconnect")
        except Exception as exc:
            files.log(f"EMG disconnect command failed: {exc}")
        try:
            ser.close()
        except Exception:
            pass
        files.log("EMG reader stopped")


def glove_reader(
    args: argparse.Namespace,
    files: SessionFiles,
    counters: Counters,
    stop_event: threading.Event,
) -> None:
    try:
        port = resolve_glove_port(args.glove_port)
        files.log(f"Glove opening port={port} baud={args.glove_baud}")
        ser = serial.Serial(port, args.glove_baud, timeout=0.2)
    except Exception as exc:
        message = f"Glove open failed: {exc}"
        counters.add_error(message)
        files.log(message)
        if args.require_glove:
            stop_event.set()
        return

    try:
        time.sleep(0.8)
        ser.reset_input_buffer()
        while not stop_event.is_set():
            raw = ser.readline()
            if not raw:
                continue
            host_time_ns = time.time_ns()
            line = raw.decode(errors="replace").strip()
            if not line:
                continue

            with counters.lock:
                counters.glove_lines += 1

            if is_glove_header(line):
                files.log("Glove header received")
                continue

            parsed = parse_glove_csv_line(line)
            if parsed is None:
                with counters.lock:
                    counters.glove_unparsed += 1
                files.log(f"Glove unparsed line: {line}")
                continue

            row = {"host_time_ns": host_time_ns, **parsed}
            files.write_glove_row(row)
            with counters.lock:
                counters.glove_samples += 1
    except Exception as exc:
        message = f"Glove read failed: {exc}"
        counters.add_error(message)
        files.log(message)
        if args.require_glove:
            stop_event.set()
    finally:
        try:
            ser.close()
        except Exception:
            pass
        files.log("Glove reader stopped")


def stdin_marker_loop(
    files: SessionFiles,
    counters: Counters,
    stop_event: threading.Event,
) -> None:
    files.log("Marker input ready: trial ACTION | phase NAME | event NAME | end | q")
    trial_id = 0
    current_action: str | None = None

    while not stop_event.is_set():
        try:
            line = sys.stdin.readline()
        except Exception:
            return
        if line == "":
            return
        text = line.strip()
        if not text:
            continue

        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        value = parts[1] if len(parts) > 1 else ""

        if command in {"q", "quit", "stop"}:
            files.write_event(counters, {"event": "manual_stop"})
            stop_event.set()
            return
        if command == "trial" and value:
            trial_id += 1
            current_action = value
            files.write_event(
                counters,
                {"event": "trial_start", "trial": trial_id, "action": current_action},
            )
            continue
        if command == "phase" and value:
            files.write_event(
                counters,
                {
                    "event": "phase",
                    "trial": trial_id if trial_id else None,
                    "action": current_action,
                    "phase": value,
                },
            )
            continue
        if command == "end":
            files.write_event(
                counters,
                {"event": "trial_end", "trial": trial_id, "action": current_action},
            )
            current_action = None
            continue
        if command in {"event", "mark", "marker"} and value:
            files.write_event(counters, {"event": "marker", "label": value})
            continue

        files.write_event(counters, {"event": "marker", "label": text})


def run_guided_protocol(
    args: argparse.Namespace,
    files: SessionFiles,
    counters: Counters,
    stop_event: threading.Event,
) -> None:
    config = load_yaml(Path(args.action_config))
    all_actions = list(config.get("actions") or [])
    if args.actions:
        requested = [a.strip() for a in args.actions.split(",") if a.strip()]
        actions = [a for a in requested if a in all_actions]
        missing = sorted(set(requested) - set(actions))
        if missing:
            files.log(f"Guided actions ignored unknown actions: {missing}")
    else:
        actions = all_actions

    if not actions:
        raise RuntimeError("No guided actions selected")

    timing = config.get("trial_timing_s") or {}
    phases = ["rest_pre", "move", "hold", "release", "rest_post"]
    phase_durations = {phase: float(timing.get(phase, 2.0)) for phase in phases}
    trials_per_action = int(args.trials_per_action or 1)

    trial_id = 0
    files.log(
        f"Guided protocol start actions={actions} trials_per_action={trials_per_action}"
    )
    for action in actions:
        for _ in range(trials_per_action):
            if stop_event.is_set():
                return
            trial_id += 1
            files.write_event(
                counters,
                {"event": "trial_start", "trial": trial_id, "action": action},
            )
            files.log(f"TRIAL {trial_id} action={action} start")

            for phase in phases:
                if stop_event.is_set():
                    return
                files.write_event(
                    counters,
                    {
                        "event": "phase",
                        "trial": trial_id,
                        "action": action,
                        "phase": phase,
                    },
                )
                files.log(
                    f"TRIAL {trial_id} action={action} phase={phase} "
                    f"duration_s={phase_durations[phase]:.2f}"
                )
                sleep_until = time.monotonic() + phase_durations[phase]
                while time.monotonic() < sleep_until and not stop_event.is_set():
                    time.sleep(0.05)

            files.write_event(
                counters,
                {"event": "trial_end", "trial": trial_id, "action": action},
            )
            files.log(f"TRIAL {trial_id} action={action} end")

    files.log("Guided protocol complete")
    stop_event.set()


def progress_loop(
    files: SessionFiles,
    counters: Counters,
    stop_event: threading.Event,
    duration: float | None,
) -> None:
    start = time.monotonic()
    next_progress = start + 10.0
    while not stop_event.is_set():
        now = time.monotonic()
        if duration is not None and now - start >= duration:
            files.log(f"Duration reached duration_s={duration}")
            stop_event.set()
            return
        if now >= next_progress:
            snap = counters.snapshot()
            remain = None if duration is None else max(0.0, duration - (now - start))
            parts = ["PROGRESS"]
            if remain is not None:
                parts.append(f"remaining_s={remain:.1f}")
            parts.extend(
                [
                    f"emg_recv_hex={snap['emg_recv_hex']}",
                    f"emg_rate1000={snap['emg_rate1000']}",
                    f"glove_samples={snap['glove_samples']}",
                    f"events={snap['events']}",
                ]
            )
            files.log(" ".join(parts))
            next_progress += 10.0
        time.sleep(0.1)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect synchronized raw EMG and mechanical glove data."
    )
    parser.add_argument("--dataset-root", type=Path, default=DATASET_ROOT_DEFAULT)
    parser.add_argument("--dataset-version", default="v001")
    parser.add_argument("--subject", default="subject_zkx")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--attached-channels",
        default="",
        help="Comma-separated physically attached EMG channel numbers, e.g. 1,2,3,4.",
    )

    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--guided", action="store_true")
    parser.add_argument("--action-config", type=Path, default=ACTION_CONFIG_DEFAULT)
    parser.add_argument("--actions", default="")
    parser.add_argument("--trials-per-action", type=int, default=1)
    parser.add_argument("--no-stdin-events", action="store_true")

    parser.add_argument("--no-emg", action="store_true")
    parser.add_argument("--require-emg", action="store_true")
    parser.add_argument("--b120-port", default=None)
    parser.add_argument("--b120-baud", type=int, default=115200)
    parser.add_argument("--emg-rate", type=int, default=1000)

    parser.add_argument("--no-glove", action="store_true")
    parser.add_argument("--require-glove", action="store_true")
    parser.add_argument("--glove-port", default=None)
    parser.add_argument("--glove-baud", type=int, default=230400)
    parser.add_argument("--glove-rate", type=int, default=100)

    parser.add_argument("--list-ports", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.list_ports:
        print(format_ports())
        return 0

    raw_dir = args.dataset_root / "raw"
    session_id = make_session_id(raw_dir, args.subject, args.session_id)
    session_dir = raw_dir / args.subject / session_id
    session_dir.mkdir(parents=True, exist_ok=False)

    counters = Counters()
    files = SessionFiles(session_dir)
    stop_event = threading.Event()
    threads: list[threading.Thread] = []
    manifest = make_manifest(args, session_id, session_dir)
    write_manifest(session_dir, manifest)

    try:
        files.log(f"Session start session_id={session_id} dir={session_dir}")
        files.write_event(
            counters,
            {
                "event": "session_start",
                "session_id": session_id,
                "subject_id": args.subject,
            },
        )

        if not args.no_emg:
            t = threading.Thread(
                target=emg_reader, args=(args, files, counters, stop_event), daemon=True
            )
            threads.append(t)
            t.start()
        if not args.no_glove:
            t = threading.Thread(
                target=glove_reader,
                args=(args, files, counters, stop_event),
                daemon=True,
            )
            threads.append(t)
            t.start()

        if args.guided:
            run_guided_protocol(args, files, counters, stop_event)
        else:
            if not args.no_stdin_events and sys.stdin.isatty():
                t = threading.Thread(
                    target=stdin_marker_loop,
                    args=(files, counters, stop_event),
                    daemon=True,
                )
                threads.append(t)
                t.start()
            progress_loop(files, counters, stop_event, args.duration)

    except KeyboardInterrupt:
        files.log("KeyboardInterrupt received")
        files.write_event(counters, {"event": "keyboard_interrupt"})
        stop_event.set()
    except Exception as exc:
        counters.add_error(str(exc))
        files.log(f"Collector failed: {exc}")
        stop_event.set()
    finally:
        stop_event.set()
        for t in threads:
            if t.is_alive():
                t.join(timeout=3.0)

        files.write_event(
            counters,
            {
                "event": "session_end",
                "session_id": session_id,
                "subject_id": args.subject,
            },
        )
        summary = counters.snapshot()
        if args.require_emg and summary["emg_recv_hex"] == 0:
            summary["errors"].append("required EMG stream produced zero RECV_HEX frames")
        if args.require_glove and summary["glove_samples"] == 0:
            summary["errors"].append("required glove stream produced zero samples")
        status = "completed" if not summary["errors"] else "partial"
        manifest["status"] = status
        manifest["ended_utc"] = utc_now_iso()
        manifest["summary"] = summary
        write_manifest(session_dir, manifest)
        files.log(f"Session end status={status} summary={summary}")
        files.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
