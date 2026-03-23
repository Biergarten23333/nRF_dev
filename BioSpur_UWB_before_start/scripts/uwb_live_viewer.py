#!/usr/bin/env python3
import argparse
import os
import queue
import re
import sys
import threading
import time
import warnings
from collections import deque
from dataclasses import dataclass

warnings.filterwarnings(
    "ignore",
    message="Unable to import Axes3D.*",
    module="matplotlib.projections",
)

import matplotlib

matplotlib.use("TkAgg")

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import serial
import tkinter as tk
from tkinter import ttk


USB_TAG_RE = re.compile(
    r"Tag motion summary sweep=(?P<sweep>\d+).*?"
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) mm "
    r"rms=(?P<rms>\d+) mm max=(?P<max>\d+) mm"
)

USB_TAG_POS_RE = re.compile(
    r"Tag pos sweep=(?P<sweep>\d+).*?"
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) mm "
    r"rms=(?P<rms>\d+) mm max=(?P<max>\d+) mm"
)

BLE_TAG_RE = re.compile(
    r"(?:BLE|NUS) notify: TagSummary sweep=(?P<sweep>\d+) plan=(?P<plan>\w+) "
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) "
    r"rms=(?P<rms>\d+) max=(?P<max>\d+)"
    r"(?: anchors=\[[^\]]*\])?"
    r"(?: motion_dt=(?P<motion_dt>\d+))?"
    r"(?: disp=(?P<disp>\d+) speed=(?P<speed>\d+))?"
)


@dataclass
class DataPoint:
    timestamp: float
    sweep: int
    x_mm: int
    y_mm: int
    z_mm: int
    rms_mm: int
    max_mm: int
    source: str


class SerialReader(threading.Thread):
    def __init__(self, name, port, snr, baudrate, parser, out_queue):
        super().__init__(daemon=True)
        self.name = name
        self.port = port
        self.snr = snr
        self.baudrate = baudrate
        self.parser = parser
        self.out_queue = out_queue
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def resolve_port(self):
        if self.port and os.path.exists(self.port):
            return self.port
        if self.snr:
            by_id = f"/dev/serial/by-id/usb-SEGGER_J-Link_000{self.snr}-if00"
            if os.path.exists(by_id):
                self.port = by_id
                return by_id
        return self.port

    def run(self):
        pending = ""
        while not self.stop_event.is_set():
            port = self.resolve_port()
            if not port or not os.path.exists(port):
                label = port or f"SN {self.snr}"
                self.out_queue.put(("status", self.name, f"waiting for {label}"))
                time.sleep(0.5)
                continue

            try:
                with serial.Serial(port, self.baudrate, timeout=0.2) as ser:
                    self.out_queue.put(("status", self.name, f"connected {port}"))
                    while not self.stop_event.is_set():
                        chunk = ser.read(ser.in_waiting or 1)
                        if not chunk:
                            continue
                        pending += chunk.decode("utf-8", errors="replace")
                        while "\n" in pending:
                            line, pending = pending.split("\n", 1)
                            text = line.rstrip("\r")
                            if not text:
                                continue
                            self.out_queue.put(("raw", self.name, text))
                            point = self.parser(text, self.name)
                            if point is not None:
                                self.out_queue.put(("point", self.name, point))
            except (serial.SerialException, OSError) as exc:
                self.out_queue.put(("status", self.name, f"waiting for {port}: {exc}"))
                time.sleep(0.5)


def parse_usb_line(text, source_name):
    match = USB_TAG_RE.search(text) or USB_TAG_POS_RE.search(text)
    if not match:
        return None
    return DataPoint(
        timestamp=time.time(),
        sweep=int(match.group("sweep")),
        x_mm=int(match.group("x")),
        y_mm=int(match.group("y")),
        z_mm=int(match.group("z")),
        rms_mm=int(match.group("rms")),
        max_mm=int(match.group("max")),
        source=source_name,
    )


def parse_ble_line(text, source_name):
    match = BLE_TAG_RE.search(text)
    if not match:
        return None
    return DataPoint(
        timestamp=time.time(),
        sweep=int(match.group("sweep")),
        x_mm=int(match.group("x")),
        y_mm=int(match.group("y")),
        z_mm=int(match.group("z")),
        rms_mm=int(match.group("rms")),
        max_mm=int(match.group("max")),
        source=source_name,
    )


class PlotPane(ttk.Frame):
    def __init__(self, master, title):
        super().__init__(master)
        self.figure = Figure(figsize=(4.5, 3.2), dpi=100)
        self.figure.tight_layout()
        self.axes = {
            "xy": self.figure.add_subplot(221),
            "xz": self.figure.add_subplot(222),
            "yz": self.figure.add_subplot(223),
            "iso": self.figure.add_subplot(224),
        }
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.axes["xy"].set_title(f"{title} XY")
        self.axes["xz"].set_title(f"{title} XZ")
        self.axes["yz"].set_title(f"{title} YZ")
        self.axes["iso"].set_title(f"{title} 3D-like")

        self.axes["xy"].set_xlabel("X (mm)")
        self.axes["xy"].set_ylabel("Y (mm)")
        self.axes["xz"].set_xlabel("X (mm)")
        self.axes["xz"].set_ylabel("Z (mm)")
        self.axes["yz"].set_xlabel("Y (mm)")
        self.axes["yz"].set_ylabel("Z (mm)")
        self.axes["iso"].set_xlabel("X + 0.5Y")
        self.axes["iso"].set_ylabel("Z + 0.3Y")

        for ax in self.axes.values():
            ax.grid(True, alpha=0.25)

    def update_points(self, points):
        for ax in self.axes.values():
            ax.cla()
            ax.grid(True, alpha=0.25)

        self.axes["xy"].set_title("XY")
        self.axes["xz"].set_title("XZ")
        self.axes["yz"].set_title("YZ")
        self.axes["iso"].set_title("3D-like")
        self.axes["xy"].set_xlabel("X (mm)")
        self.axes["xy"].set_ylabel("Y (mm)")
        self.axes["xz"].set_xlabel("X (mm)")
        self.axes["xz"].set_ylabel("Z (mm)")
        self.axes["yz"].set_xlabel("Y (mm)")
        self.axes["yz"].set_ylabel("Z (mm)")
        self.axes["iso"].set_xlabel("X + 0.5Y")
        self.axes["iso"].set_ylabel("Z + 0.3Y")

        if not points:
            self.canvas.draw_idle()
            return

        xs = [p.x_mm for p in points]
        ys = [p.y_mm for p in points]
        zs = [p.z_mm for p in points]
        iso_x = [x + 0.5 * y for x, y in zip(xs, ys)]
        iso_y = [z + 0.3 * y for z, y in zip(zs, ys)]

        self.axes["xy"].plot(xs, ys, color="#1f77b4", linewidth=1.4)
        self.axes["xy"].scatter(xs[-1:], ys[-1:], color="#d62728", s=18)
        self.axes["xy"].set_aspect("equal", adjustable="box")

        self.axes["xz"].plot(xs, zs, color="#1f77b4", linewidth=1.4)
        self.axes["xz"].scatter(xs[-1:], zs[-1:], color="#d62728", s=18)
        self.axes["xz"].set_aspect("equal", adjustable="box")

        self.axes["yz"].plot(ys, zs, color="#1f77b4", linewidth=1.4)
        self.axes["yz"].scatter(ys[-1:], zs[-1:], color="#d62728", s=18)
        self.axes["yz"].set_aspect("equal", adjustable="box")

        self.axes["iso"].plot(iso_x, iso_y, color="#1f77b4", linewidth=1.4)
        self.axes["iso"].scatter(iso_x[-1:], iso_y[-1:], color="#d62728", s=18)
        self.axes["iso"].set_aspect("equal", adjustable="box")

        self.canvas.draw_idle()


class SourcePanel(ttk.Frame):
    def __init__(self, master, title, trail_seconds, relative_view=False):
        super().__init__(master, padding=8)
        self.trail_seconds = trail_seconds
        self.relative_view = relative_view
        self.points = deque()
        self.raw_lines = deque(maxlen=300)

        self.title_var = tk.StringVar(value=title)
        self.status_var = tk.StringVar(value="disconnected")
        self.metrics_var = tk.StringVar(value="no data")

        ttk.Label(self, textvariable=self.title_var, font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(self, textvariable=self.status_var).pack(anchor="w")
        ttk.Label(self, textvariable=self.metrics_var).pack(anchor="w", pady=(0, 6))

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=4)
        body.rowconfigure(1, weight=2)

        self.plot_pane = PlotPane(body, title)
        self.plot_pane.grid(row=0, column=0, sticky="nsew")

        raw_frame = ttk.Frame(body)
        raw_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        raw_frame.columnconfigure(0, weight=1)
        raw_frame.rowconfigure(1, weight=1)

        ttk.Label(raw_frame, text="Raw data").grid(row=0, column=0, sticky="w")
        self.raw_text = tk.Text(raw_frame, height=14, wrap="none")
        self.raw_text.grid(row=1, column=0, sticky="nsew")
        self.raw_text.configure(state="disabled")

    def set_status(self, text):
        self.status_var.set(text)

    def add_raw_line(self, text):
        stamp = time.strftime("%H:%M:%S")
        self.raw_lines.append(f"[{stamp}] {text}")
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", tk.END)
        self.raw_text.insert(tk.END, "\n".join(self.raw_lines))
        self.raw_text.see(tk.END)
        self.raw_text.configure(state="disabled")

    def add_point(self, point):
        self.points.append(point)
        self._trim_points()
        self._refresh()

    def _trim_points(self):
        cutoff = time.time() - self.trail_seconds
        while self.points and self.points[0].timestamp < cutoff:
            self.points.popleft()

    def _refresh(self):
        pts = list(self.points)
        if not pts:
            self.metrics_var.set("no data")
            self.plot_pane.update_points([])
            return

        last = pts[-1]
        display_pts = pts
        if self.relative_view and pts:
            mean_x = sum(p.x_mm for p in pts) / len(pts)
            mean_y = sum(p.y_mm for p in pts) / len(pts)
            mean_z = sum(p.z_mm for p in pts) / len(pts)
            display_pts = [
                DataPoint(
                    timestamp=p.timestamp,
                    sweep=p.sweep,
                    x_mm=int(p.x_mm - mean_x),
                    y_mm=int(p.y_mm - mean_y),
                    z_mm=int(p.z_mm - mean_z),
                    rms_mm=p.rms_mm,
                    max_mm=p.max_mm,
                    source=p.source,
                )
                for p in pts
            ]

        self.metrics_var.set(
            f"sweep={last.sweep}  xyz=({last.x_mm},{last.y_mm},{last.z_mm}) mm  "
            f"rms={last.rms_mm}  max={last.max_mm}  trail={len(pts)} pts / {self.trail_seconds:.0f}s"
        )
        self.plot_pane.update_points(display_pts)


class App:
    def __init__(
        self,
        usb_port,
        usb_snr,
        ble_port,
        ble_snr,
        baudrate,
        usb_trail_seconds,
        ble_trail_seconds,
    ):
        self.root = tk.Tk()
        self.root.title("UWB Live Viewer")
        self.root.geometry("1800x980")
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.queue = queue.Queue()
        self.usb_panel = SourcePanel(
            self.root, "Ref Tag 115", usb_trail_seconds, relative_view=False
        )
        self.ble_panel = SourcePanel(
            self.root, "BLE Rotating Tag", ble_trail_seconds, relative_view=True
        )
        self.usb_panel.grid(row=0, column=0, sticky="nsew")
        self.ble_panel.grid(row=0, column=1, sticky="nsew")

        self.usb_reader = SerialReader("usb", usb_port, usb_snr, baudrate, parse_usb_line, self.queue)
        self.ble_reader = SerialReader("ble", ble_port, ble_snr, baudrate, parse_ble_line, self.queue)

        self.usb_reader.start()
        self.ble_reader.start()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self.drain_queue)

    def drain_queue(self):
        while True:
            try:
                event_type, source, payload = self.queue.get_nowait()
            except queue.Empty:
                break

            panel = self.usb_panel if source == "usb" else self.ble_panel
            if event_type == "status":
                panel.set_status(payload)
            elif event_type == "raw":
                panel.add_raw_line(payload)
            elif event_type == "point":
                panel.add_point(payload)

        self.root.after(50, self.drain_queue)

    def close(self):
        self.usb_reader.stop()
        self.ble_reader.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(
        description="Live UWB viewer for USB tag data and BLE TagSummary data."
    )
    parser.add_argument(
        "--usb-port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00",
        help="Serial port for the direct USB UWB tag.",
    )
    parser.add_argument(
        "--usb-snr",
        default="760186115",
        help="SEGGER serial number for the USB UWB tag. Used for auto-detect.",
    )
    parser.add_argument(
        "--ble-port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00",
        help="Serial port for the BLE master board.",
    )
    parser.add_argument(
        "--ble-snr",
        default="683234364",
        help="SEGGER serial number for the BLE master board. Used for auto-detect.",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=115200,
        help="Serial baud rate for both inputs.",
    )
    parser.add_argument(
        "--trail-seconds",
        type=float,
        default=5.0,
        help="How many seconds of recent points to keep on the USB tag plots.",
    )
    parser.add_argument(
        "--ble-trail-seconds",
        type=float,
        default=20.0,
        help="How many seconds of recent points to keep on the BLE rotating tag plots.",
    )
    args = parser.parse_args()

    app = App(
        usb_port=args.usb_port,
        usb_snr=args.usb_snr,
        ble_port=args.ble_port,
        ble_snr=args.ble_snr,
        baudrate=args.baudrate,
        usb_trail_seconds=args.trail_seconds,
        ble_trail_seconds=args.ble_trail_seconds,
    )
    app.run()


if __name__ == "__main__":
    raise SystemExit(main())
