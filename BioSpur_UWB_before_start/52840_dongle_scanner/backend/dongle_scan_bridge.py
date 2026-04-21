#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(description="Relay BioSpur dongle scanner JSON over stdout.")
    ap.add_argument("--port", required=True, help="CDC ACM port for BS-BLE-SCANNER")
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    try:
        import serial
    except ImportError as exc:
        print(json.dumps({"type": "error", "message": "pyserial not installed"}))
        return 1

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except Exception as exc:
        print(json.dumps({"type": "error", "message": f"open failed: {exc}"}))
        return 1

    print(json.dumps({"type": "status", "state": "connected", "port": args.port}), flush=True)
    try:
        while True:
            line = ser.readline()
            if not line:
                continue
            try:
                text = line.decode("utf-8", "ignore").strip()
            except Exception:
                continue
            if not text:
                continue
            if not text.startswith("{"):
                continue
            # Pass JSON through unchanged.
            try:
                json.loads(text)
            except Exception:
                continue
            print(text, flush=True)
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
