#!/usr/bin/env python3
import argparse
import sys
import threading
import time
from pathlib import Path

import serial


def _emit(prefix: str, text: str, lock: threading.Lock) -> None:
    if not text:
      return
    with lock:
        print(f'[{prefix}] {text}', flush=True)


def _tail_serial(prefix: str, port: str, baud: int, stop: threading.Event, lock: threading.Lock) -> None:
    pending = ''
    while not stop.is_set():
        try:
            with serial.Serial(port, baud, timeout=0.2) as ser:
                _emit(prefix, f'connected {port}', lock)
                while not stop.is_set():
                    chunk = ser.read(ser.in_waiting or 1)
                    if not chunk:
                        continue
                    pending += chunk.decode('utf-8', errors='replace')
                    while '\n' in pending:
                        line, pending = pending.split('\n', 1)
                        _emit(prefix, line.rstrip('\r'), lock)
                break
        except (serial.SerialException, OSError) as exc:
            _emit(prefix, f'[error] {exc}', lock)
            time.sleep(0.5)

    if pending.strip():
        _emit(prefix, pending.rstrip('\r'), lock)


def main() -> int:
    parser = argparse.ArgumentParser(description='Tail live serial output from ref/motion tags without recording to disk.')
    parser.add_argument('--ref-port', required=True)
    parser.add_argument('--motion-port', required=True)
    parser.add_argument('--baud', type=int, default=115200)
    args = parser.parse_args()

    stop = threading.Event()
    lock = threading.Lock()
    threads = [
        threading.Thread(target=_tail_serial, args=('ref', args.ref_port, args.baud, stop, lock), daemon=True),
        threading.Thread(target=_tail_serial, args=('motion', args.motion_port, args.baud, stop, lock), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=1.0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
