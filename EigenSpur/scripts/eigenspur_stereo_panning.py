#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import math
import os
import queue
import re
import signal
import sys
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    import sounddevice as sd
except Exception as exc:  # pragma: no cover - hardware dependency
    raise SystemExit(
        "Install audio dependencies:\n"
        "  sudo apt install libportaudio2 portaudio19-dev\n"
        "  python3 -m pip install --break-system-packages sounddevice\n"
        f"sounddevice import failed: {exc}"
    ) from exc


NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

TAG_NOTIFY_PREFIX_RE = r"(?:BLE(?:\[(?P<conn>\d+)(?::[^\]]*)?\])?|BS[0-9A-F]{4}|NUS)"
TAG_SUMMARY_PATTERNS = [
    re.compile(
        rf"{TAG_NOTIFY_PREFIX_RE} notify: TagSummary sweep=(?P<sweep>\d+) plan=(?P<plan>\w+) "
        r"(?:pmode=(?P<pmode>\d+) )?(?:qf=(?P<qf>\d+) )?"
        r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) "
        r"rms=(?P<rms>\d+) max=(?P<max>\d+)"
    ),
    re.compile(
        rf"{TAG_NOTIFY_PREFIX_RE} notify: TagSummary s=(?P<sweep>\d+) p=(?P<plan>\w+) "
        r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) "
        r"r=(?P<rms>\d+) m=(?P<max>\d+)"
    ),
    re.compile(
        rf"{TAG_NOTIFY_PREFIX_RE} notify: (?:TS|TagSummary) s=(?P<sweep>\d+) p=(?P<plan>\w+) "
        r"xyz=(?:(?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)|\((?P<x2>-?\d+),(?P<y2>-?\d+),(?P<z2>-?\d+)\)) "
        r"r=(?P<rms>\d+) m=(?P<max>\d+)"
    ),
    re.compile(
        rf"{TAG_NOTIFY_PREFIX_RE} notify: TS;"
        r"(?P<ver>\d+);(?P<sweep>\d+);(?P<plan>[A-Za-z0-9_]+);"
        r"(?P<x>-?\d+);(?P<y>-?\d+);(?P<z>-?\d+);"
        r"(?P<rms>\d+);(?P<max>\d+);"
    ),
]


@dataclass(frozen=True)
class Position:
    x_mm: float
    y_mm: float
    z_mm: float
    source: str
    updated_at: float


class OneEuroFilter:
    def __init__(self, min_cutoff: float = 0.3, beta: float = 0.5, d_cutoff: float = 1.0) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_prev: float | None = None
        self.dx_prev = 0.0
        self.t_prev: float | None = None

    def _alpha(self, cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float | None = None) -> float:
        if t is None:
            t = time.monotonic()
        if self.t_prev is None:
            self.x_prev = x
            self.t_prev = t
            return x
        dt = t - self.t_prev
        if dt <= 0.0 or self.x_prev is None:
            return self.x_prev if self.x_prev is not None else x

        a_d = self._alpha(self.d_cutoff, dt)
        dx = (x - self.x_prev) / dt
        dx = a_d * dx + (1.0 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx)
        a = self._alpha(cutoff, dt)
        x_filt = a * x + (1.0 - a) * self.x_prev
        self.x_prev = x_filt
        self.dx_prev = dx
        self.t_prev = t
        return x_filt


class PositionFilter:
    def __init__(self, args: argparse.Namespace) -> None:
        self.enabled = not args.disable_uwb_filter
        self._x = OneEuroFilter(args.uwb_filter_min_cutoff, args.uwb_filter_beta, args.uwb_filter_d_cutoff)
        self._y = OneEuroFilter(args.uwb_filter_min_cutoff, args.uwb_filter_beta, args.uwb_filter_d_cutoff)
        self._z = OneEuroFilter(args.uwb_filter_min_cutoff, args.uwb_filter_beta, args.uwb_filter_d_cutoff)

    def apply(self, pos: Position) -> Position:
        if not self.enabled:
            return pos
        t = time.monotonic()
        return Position(
            self._x(pos.x_mm, t),
            self._y(pos.y_mm, t),
            self._z(pos.z_mm, t),
            pos.source,
            pos.updated_at,
        )


class SharedState:
    def __init__(self, initial: Position) -> None:
        self._lock = threading.Lock()
        self._position = initial
        self._left_gain = 0.5
        self._right_gain = 0.5
        self._input_rms = 0.0
        self._output_rms_l = 0.0
        self._output_rms_r = 0.0

    def update_position(self, pos: Position) -> None:
        with self._lock:
            self._position = pos

    def snapshot(self) -> tuple[Position, float, float, float, float, float]:
        with self._lock:
            return (
                self._position,
                self._left_gain,
                self._right_gain,
                self._input_rms,
                self._output_rms_l,
                self._output_rms_r,
            )

    def update_gains(self, left: float, right: float) -> None:
        with self._lock:
            self._left_gain = left
            self._right_gain = right

    def update_levels(self, input_rms: float, output_rms_l: float, output_rms_r: float) -> None:
        with self._lock:
            self._input_rms = input_rms
            self._output_rms_l = output_rms_l
            self._output_rms_r = output_rms_r


class CaptureWriter:
    def __init__(self, args: argparse.Namespace) -> None:
        self.enabled = bool(args.capture_id)
        self.root: Path | None = None
        self.wav_path: Path | None = None
        self.tr_path: Path | None = None
        self._audio_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=256)
        self._thread: threading.Thread | None = None
        self._tr_file = None
        self._tr_writer: csv.DictWriter | None = None
        if not self.enabled:
            return
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.capture_id.strip())
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.root = Path(args.capture_root) / f"{safe_id}_{stamp}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.wav_path = self.root / "audio_panned.wav"
        self.tr_path = self.root / "uwb_tr.csv"
        self._tr_file = self.tr_path.open("w", newline="", encoding="utf-8")
        self._tr_writer = csv.DictWriter(
            self._tr_file,
            fieldnames=[
                "host_time_s",
                "source",
                "x_m",
                "y_m",
                "z_m",
                "u_m",
                "v_m",
                "left_gain",
                "right_gain",
            ],
        )
        self._tr_writer.writeheader()
        self._thread = threading.Thread(
            target=self._write_wav,
            args=(args.sample_rate,),
            daemon=True,
        )
        self._thread.start()
        print(f"[capture] saving {self.root}", flush=True)

    def write_audio(self, stereo_i16: np.ndarray) -> None:
        if not self.enabled:
            return
        try:
            self._audio_queue.put_nowait(stereo_i16.copy())
        except queue.Full:
            print("[capture] audio queue full; dropping block", file=sys.stderr)

    def write_position(
        self,
        pos: Position,
        *,
        u_mm: float,
        v_mm: float,
        left_gain: float,
        right_gain: float,
        coord_scale: float,
    ) -> None:
        if not self.enabled or self._tr_writer is None or self._tr_file is None:
            return
        self._tr_writer.writerow(
            {
                "host_time_s": f"{time.time():.6f}",
                "source": pos.source,
                "x_m": f"{pos.x_mm / coord_scale:.6f}",
                "y_m": f"{pos.y_mm / coord_scale:.6f}",
                "z_m": f"{pos.z_mm / coord_scale:.6f}",
                "u_m": f"{u_mm / coord_scale:.6f}",
                "v_m": f"{v_mm / coord_scale:.6f}",
                "left_gain": f"{left_gain:.6f}",
                "right_gain": f"{right_gain:.6f}",
            }
        )
        self._tr_file.flush()

    def _write_wav(self, sample_rate: int) -> None:
        assert self.wav_path is not None
        with wave.open(str(self.wav_path), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            while True:
                block = self._audio_queue.get()
                if block is None:
                    break
                wav.writeframes(block.astype(np.int16, copy=False).tobytes())

    def close(self) -> None:
        if not self.enabled:
            return
        self._audio_queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._tr_file is not None:
            self._tr_file.close()
        print(f"[capture] complete {self.root}", flush=True)


def parse_position_text(text: str, source: str) -> Iterable[Position]:
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith(("TagSummary", "TS", "TS;")):
            fragment = (prefix or "NUS notify: ") + fragment
        for pattern in TAG_SUMMARY_PATTERNS:
            match = pattern.search(fragment)
            if not match:
                continue
            x = match.groupdict().get("x") or match.groupdict().get("x2")
            y = match.groupdict().get("y") or match.groupdict().get("y2")
            z = match.groupdict().get("z") or match.groupdict().get("z2")
            if x is None or y is None or z is None:
                continue
            yield Position(float(x), float(y), float(z), source, time.time())
            break


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def linear_gains(t: float) -> tuple[float, float]:
    t = clamp(t, 0.0, 1.0)
    return 1.0 - t, t


def stage_value(pos: Position, axis: str, flip: bool) -> tuple[float, float]:
    if axis == "ab":
        u_mm, v_mm = pos.x_mm, pos.y_mm
    elif axis == "bc":
        u_mm, v_mm = pos.y_mm, pos.x_mm
    elif axis == "cd":
        u_mm, v_mm = -pos.x_mm, pos.y_mm
    elif axis == "da":
        u_mm, v_mm = -pos.y_mm, pos.x_mm
    else:
        raise ValueError(f"unsupported stage axis: {axis}")
    if flip:
        u_mm = -u_mm
    return u_mm, v_mm


def compute_gains(
    pos: Position,
    *,
    axis: str,
    flip: bool,
    left_mm: float,
    right_mm: float,
) -> tuple[float, float, float, float]:
    u_mm, v_mm = stage_value(pos, axis, flip)
    span = right_mm - left_mm
    if abs(span) < 1e-9:
        t = 0.5
    else:
        t = clamp((u_mm - left_mm) / span, 0.0, 1.0)
    left, right = linear_gains(t)
    return left, right, u_mm, v_mm


def audio_loop(
    args: argparse.Namespace,
    state: SharedState,
    capture: CaptureWriter,
    stop: threading.Event,
) -> None:
    dtype = "int16" if args.dtype == "int16" else "float32"
    input_device = int(args.input_device) if str(args.input_device).isdigit() else args.input_device
    output_device = int(args.output_device) if str(args.output_device).isdigit() else args.output_device
    try:
        input_info = sd.query_devices(input_device, "input")
        output_info = sd.query_devices(output_device, "output")
        print(
            f"[audio] input device: {input_info.get('name')} "
            f"max_in={input_info.get('max_input_channels')}",
            flush=True,
        )
        print(
            f"[audio] output device: {output_info.get('name')} "
            f"max_out={output_info.get('max_output_channels')}",
            flush=True,
        )
    except Exception as exc:
        print(f"[audio] device query warning: {exc}", file=sys.stderr)
    prev_left_gain = 0.5
    prev_right_gain = 0.5

    def callback(indata, outdata, frames, _time_info, status):  # pragma: no cover - realtime callback
        nonlocal prev_left_gain, prev_right_gain
        if status and args.print_audio_status:
            print(f"[audio] {status}", file=sys.stderr)
        pos, _, _, _, _, _ = state.snapshot()
        left, right, _, _ = compute_gains(
            pos,
            axis=args.stage_axis,
            flip=args.flip_axis,
            left_mm=args.speaker_left * args.coord_scale,
            right_mm=args.speaker_right * args.coord_scale,
        )
        state.update_gains(left, right)
        gain_l = np.linspace(prev_left_gain, left, frames, dtype=np.float32)
        gain_r = np.linspace(prev_right_gain, right, frames, dtype=np.float32)
        prev_left_gain = left
        prev_right_gain = right
        if dtype == "int16":
            mono = indata.astype(np.float32).mean(axis=1) / 32768.0
            stereo = np.empty((frames, 2), dtype=np.float32)
            stereo[:, 0] = mono * gain_l * args.output_gain
            stereo[:, 1] = mono * gain_r * args.output_gain
            np.clip(stereo * 32767.0, -32768.0, 32767.0, out=stereo)
            stereo_i16 = stereo.astype(np.int16)
            outdata[:] = stereo_i16
            capture.write_audio(stereo_i16)
        else:
            mono = indata.mean(axis=1)
            outdata[:, 0] = mono * gain_l * args.output_gain
            outdata[:, 1] = mono * gain_r * args.output_gain
            np.clip(outdata, -1.0, 1.0, out=outdata)
            stereo_i16 = np.clip(outdata * 32767.0, -32768.0, 32767.0).astype(np.int16)
            capture.write_audio(stereo_i16)
        input_rms = float(np.sqrt(np.mean(np.square(mono)))) if frames > 0 else 0.0
        state.update_levels(
            input_rms,
            input_rms * float(np.mean(gain_l)) * args.output_gain,
            input_rms * float(np.mean(gain_r)) * args.output_gain,
        )

    with sd.Stream(
        samplerate=args.sample_rate,
        blocksize=args.block_size,
        dtype=dtype,
        channels=(args.input_channels, 2),
        device=(input_device, output_device),
        latency=(args.input_latency, args.output_latency),
        never_drop_input=False,
        callback=callback,
    ):
        print("[audio] stream started", flush=True)
        print("[system] audio_started_waiting_for_uwb", flush=True)
        while not stop.is_set():
            time.sleep(0.05)


def test_tone(args: argparse.Namespace) -> None:
    output_device = int(args.output_device) if str(args.output_device).isdigit() else args.output_device
    try:
        output_info = sd.query_devices(output_device, "output")
        print(
            f"[audio-test] output device: {output_info.get('name')} "
            f"max_out={output_info.get('max_output_channels')}",
            flush=True,
        )
    except Exception as exc:
        print(f"[audio-test] device query warning: {exc}", file=sys.stderr)
    frames = max(1, int(args.sample_rate * args.test_tone_seconds))
    t = np.arange(frames, dtype=np.float32) / float(args.sample_rate)
    tone = (0.18 * np.sin(2.0 * math.pi * args.test_tone_frequency * t)).astype(np.float32)
    stereo = np.column_stack((tone, tone))
    sd.play(stereo, samplerate=args.sample_rate, device=output_device, blocking=True)
    print("[audio-test] tone complete", flush=True)


def serial_reader(
    args: argparse.Namespace,
    state: SharedState,
    capture: CaptureWriter,
    stop: threading.Event,
    pos_filter: PositionFilter,
) -> None:
    try:
        import serial
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SystemExit("Install serial dependency: python3 -m pip install pyserial") from exc

    pending = ""
    seen_position = False
    while not stop.is_set():
        try:
            with serial.Serial(args.serial_port, args.serial_baud, timeout=0.2) as ser:
                print(f"[uwb] CDC connected: {args.serial_port}", flush=True)
                while not stop.is_set():
                    chunk = ser.read(ser.in_waiting or 1)
                    if not chunk:
                        continue
                    pending += chunk.decode("utf-8", errors="replace")
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        for pos in parse_position_text(line.rstrip("\r"), "cdc"):
                            pos = pos_filter.apply(pos)
                            state.update_position(pos)
                            left, right, u_mm, v_mm = compute_gains(
                                pos,
                                axis=args.stage_axis,
                                flip=args.flip_axis,
                                left_mm=args.speaker_left * args.coord_scale,
                                right_mm=args.speaker_right * args.coord_scale,
                            )
                            capture.write_position(
                                pos,
                                u_mm=u_mm,
                                v_mm=v_mm,
                                left_gain=left,
                                right_gain=right,
                                coord_scale=args.coord_scale,
                            )
                            if not seen_position:
                                seen_position = True
                                print("[system] normal", flush=True)
        except Exception as exc:
            print(f"[uwb] CDC waiting: {exc}", file=sys.stderr)
            stop.wait(1.0)


async def ble_reader_async(
    args: argparse.Namespace,
    state: SharedState,
    stop: threading.Event,
    pos_filter: PositionFilter,
) -> None:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SystemExit("Install BLE dependency: python3 -m pip install bleak") from exc

    pending = ""

    def on_notify(_sender, data: bytearray) -> None:
        nonlocal pending
        pending += bytes(data).decode("utf-8", errors="replace")
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            text = line.rstrip("\r")
            if "notify:" not in text and text.startswith(("TagSummary", "TS", "TS;")):
                text = "NUS notify: " + text
            for pos in parse_position_text(text, "ble"):
                pos = pos_filter.apply(pos)
                state.update_position(pos)
        if pending and any(marker in pending for marker in ("TagSummary", "TS;")):
            text = pending.strip()
            if "notify:" not in text and text.startswith(("TagSummary", "TS", "TS;")):
                text = "NUS notify: " + text
            positions = list(parse_position_text(text, "ble"))
            if positions:
                for pos in positions:
                    pos = pos_filter.apply(pos)
                    state.update_position(pos)
                pending = ""

    while not stop.is_set():
        try:
            target = args.ble_address
            if args.ble_name:
                device = await BleakScanner.find_device_by_filter(
                    lambda dev, _adv: dev.name == args.ble_name,
                    timeout=args.scan_timeout,
                )
                if device is None:
                    raise RuntimeError(f"BLE name not found: {args.ble_name}")
                target = device
            elif args.ble_address:
                device = await BleakScanner.find_device_by_address(
                    args.ble_address,
                    timeout=args.scan_timeout,
                )
                target = device or args.ble_address
            if target is None:
                raise RuntimeError("set --ble-address or --ble-name")

            async with BleakClient(target, timeout=args.scan_timeout + 4.0) as client:
                print(f"[uwb] BLE connected: {args.ble_name or args.ble_address}")
                await client.start_notify(args.ble_notify_uuid, on_notify)
                if args.ble_command:
                    await client.write_gatt_char(
                        args.ble_write_uuid,
                        (args.ble_command.rstrip() + "\n").encode("ascii"),
                        response=False,
                    )
                while not stop.is_set() and client.is_connected:
                    await asyncio.sleep(0.05)
                try:
                    await client.stop_notify(args.ble_notify_uuid)
                except Exception:
                    pass
        except Exception as exc:
            print(f"[uwb] BLE waiting: {exc}", file=sys.stderr)
            await asyncio.sleep(1.0)


def ble_reader(
    args: argparse.Namespace,
    state: SharedState,
    stop: threading.Event,
    pos_filter: PositionFilter,
) -> None:
    asyncio.run(ble_reader_async(args, state, stop, pos_filter))


def printer(args: argparse.Namespace, state: SharedState, stop: threading.Event) -> None:
    while not stop.wait(args.print_interval):
        pos, left, right, input_rms, output_l, output_r = state.snapshot()
        age = time.time() - pos.updated_at
        u_mm, v_mm = stage_value(pos, args.stage_axis, args.flip_axis)
        print(
            f"x={pos.x_mm / args.coord_scale:8.3f} "
            f"y={pos.y_mm / args.coord_scale:8.3f} "
            f"z={pos.z_mm / args.coord_scale:8.3f} "
            f"axis={args.stage_axis}{'-flip' if args.flip_axis else ''} "
            f"u={u_mm / args.coord_scale:8.3f} "
            f"v={v_mm / args.coord_scale:8.3f} "
            f"L={left:5.3f} R={right:5.3f} "
            f"in={input_rms:5.3f} outL={output_l:5.3f} outR={output_r:5.3f} "
            f"source={pos.source} age={age:4.1f}s",
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Realtime EigenSpur UWB-driven stereo panning demo."
    )
    p.add_argument("--input-device", default="default", help="sounddevice input device index/name")
    p.add_argument(
        "--output-device",
        default="default",
        help="sounddevice output device index/name",
    )
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--sample-rate", type=int, default=48000)
    p.add_argument("--block-size", type=int, default=128)
    p.add_argument("--dtype", choices=["int16", "float32"], default="int16")
    p.add_argument("--input-channels", type=int, default=2)
    p.add_argument("--input-latency", default="low")
    p.add_argument("--output-latency", default="low")
    p.add_argument("--output-gain", type=float, default=1.0, help="software output gain after panning")
    p.add_argument("--print-audio-status", action="store_true")
    p.add_argument("--test-tone-seconds", type=float, default=0.0)
    p.add_argument("--test-tone-frequency", type=float, default=440.0)

    p.add_argument("--source", choices=["ble", "serial", "none"], default="serial")
    p.add_argument("--ble-address", help="DWM1001C/BioSpur tag BLE address")
    p.add_argument("--ble-name", help="DWM1001C/BioSpur tag BLE name, e.g. BSF66F")
    p.add_argument("--ble-notify-uuid", default=NUS_TX_UUID)
    p.add_argument("--ble-write-uuid", default=NUS_RX_UUID)
    p.add_argument("--ble-command", default="", help="optional NUS command sent after connect")
    p.add_argument("--scan-timeout", type=float, default=8.0)
    p.add_argument(
        "--serial-port",
        default="/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00",
        help="Master_Tag CDC serial port",
    )
    p.add_argument("--serial-baud", type=int, default=115200)

    p.add_argument("--stage-axis", choices=["ab", "bc", "cd", "da"], default="ab")
    p.add_argument("--flip-axis", action="store_true")
    p.add_argument("--coord-scale", type=float, default=1000.0, help="1 meter in incoming units")
    p.add_argument("--speaker-left", type=float, default=-1.0, help="left speaker u coordinate in meters")
    p.add_argument("--speaker-right", type=float, default=1.0, help="right speaker u coordinate in meters")
    p.add_argument("--initial-x", type=float, default=0.0)
    p.add_argument("--initial-y", type=float, default=0.0)
    p.add_argument("--initial-z", type=float, default=0.0)
    p.add_argument("--print-interval", type=float, default=0.25)
    p.add_argument("--uwb-filter-min-cutoff", type=float, default=0.3)
    p.add_argument("--uwb-filter-beta", type=float, default=0.5)
    p.add_argument("--uwb-filter-d-cutoff", type=float, default=1.0)
    p.add_argument("--disable-uwb-filter", action="store_true")
    p.add_argument("--capture-id", default="", help="optional capture ID; records WAV and UWB CSV until stopped")
    p.add_argument(
        "--capture-root",
        default=os.path.expanduser("~/Documents/EigenSpur_captures"),
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_devices:
        print(sd.query_devices())
        return 0
    if args.test_tone_seconds > 0.0:
        test_tone(args)
        return 0
    if args.source == "serial" and not args.serial_port:
        raise SystemExit("--serial-port is required when --source serial")
    if args.source == "ble" and not (args.ble_address or args.ble_name):
        raise SystemExit("--ble-address or --ble-name is required when --source ble")

    stop = threading.Event()
    initial = Position(
        args.initial_x * args.coord_scale,
        args.initial_y * args.coord_scale,
        args.initial_z * args.coord_scale,
        "initial",
        time.time(),
    )
    state = SharedState(initial)
    capture = CaptureWriter(args)
    pos_filter = PositionFilter(args)

    def handle_signal(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threads = [
        threading.Thread(target=audio_loop, args=(args, state, capture, stop), daemon=True),
        threading.Thread(target=printer, args=(args, state, stop), daemon=True),
    ]
    if args.source == "ble":
        threads.append(threading.Thread(target=ble_reader, args=(args, state, stop, pos_filter), daemon=True))
    elif args.source == "serial":
        threads.append(
            threading.Thread(target=serial_reader, args=(args, state, capture, stop, pos_filter), daemon=True)
        )
    else:
        print("[uwb] disabled; audio only", flush=True)

    for thread in threads:
        thread.start()
    while not stop.is_set():
        if not all(thread.is_alive() for thread in threads):
            stop.set()
            break
        time.sleep(0.1)
    for thread in threads:
        thread.join(timeout=2.0)
    capture.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
