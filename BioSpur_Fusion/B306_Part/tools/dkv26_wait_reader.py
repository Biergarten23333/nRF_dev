#!/usr/bin/env python3
"""Read-only CDC drain while DK-v26 waits at the operator token gate."""

from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from coldstart_fusion_control import decode_guard
from fusion_session import LineChannel, resolve_fusion_port


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    stop = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    with args.log.open("x", encoding="utf-8", buffering=1) as log:
        channel = LineChannel(
            resolve_fusion_port(args.fusion_port), log, "FUSION"
        )
        try:
            guard = decode_guard(channel, 15.0)
            print(f"DKV26_WAIT_READER_READY port={channel.port} guard={guard}",
                  flush=True)
            while not stop:
                channel.read(time.monotonic() + 0.5)
        finally:
            channel.close()
    print("DKV26_WAIT_READER_STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
