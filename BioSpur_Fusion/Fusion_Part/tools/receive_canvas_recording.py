#!/usr/bin/env python3
"""Receive one browser-rendered canvas recording on localhost."""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MAX_RECORDING_BYTES = 256 * 1024 * 1024


def write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument("--output", type=Path)
    output_group.add_argument("--frame-dir", type=Path)
    parser.add_argument("--frame-count", type=int)
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    if args.frame_dir is not None and not args.frame_count:
        parser.error("--frame-count is required with --frame-dir")
    output = args.output.resolve() if args.output is not None else None
    frame_dir = args.frame_dir.resolve() if args.frame_dir is not None else None
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
    else:
        frame_dir.mkdir(parents=True, exist_ok=False)
    state = {"saved": False, "frame_indexes": set()}

    class Handler(BaseHTTPRequestHandler):
        def cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.cors()
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            frame_match = re.fullmatch(r"/frame/(\d{6})\.png", self.path)
            valid_single = output is not None and self.path == "/upload"
            valid_frame = frame_dir is not None and frame_match is not None
            if (not valid_single and not valid_frame) or state["saved"]:
                self.send_error(409)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_RECORDING_BYTES:
                self.send_error(413)
                return
            payload = self.rfile.read(length)
            if len(payload) != length:
                self.send_error(400)
                return
            if valid_single:
                destination = output
                state["saved"] = True
            else:
                frame_index = int(frame_match.group(1))
                if frame_index >= args.frame_count or frame_index in state["frame_indexes"]:
                    self.send_error(409)
                    return
                destination = frame_dir / f"frame_{frame_index:06d}.png"
            write_new(destination, payload)
            if valid_frame:
                state["frame_indexes"].add(frame_index)
                state["saved"] = len(state["frame_indexes"]) == args.frame_count
            response = json.dumps(
                {
                    "saved": str(destination),
                    "bytes": length,
                    "received": len(state["frame_indexes"]),
                }
            ).encode()
            self.send_response(201)
            self.cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            print(format % args, flush=True)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.timeout = 1.0
    while not state["saved"]:
        server.handle_request()
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
