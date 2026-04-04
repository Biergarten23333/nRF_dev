#!/usr/bin/env python3
import argparse
import os
import select
import sys
import termios
import time
import tty


def configure_serial(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = termios.IGNPAR
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def read_line(fd: int, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    data = bytearray()
    while time.time() < deadline:
        remaining = max(0.0, deadline - time.time())
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            break
        chunk = os.read(fd, 1)
        if not chunk:
            continue
        if chunk == b"\n":
            return data.decode("utf-8", errors="replace").strip()
        if chunk != b"\r":
            data.extend(chunk)
    raise TimeoutError("timed out waiting for Central response")


def send_line(fd: int, line: str) -> None:
    os.write(fd, line.encode("utf-8") + b"\n")


def expect_ok(fd: int, timeout: float = 20.0) -> str:
    while True:
        line = read_line(fd, timeout)
        print(line)
        if line.startswith("OK "):
            return line
        if line.startswith("ERR "):
            raise RuntimeError(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a TX image through BioSpur Central BLE OTA bridge.")
    parser.add_argument("image", nargs="?", help="Path to TX zephyr.signed.bin")
    parser.add_argument("--port", required=True, help="Central data CDC port, preferably ...-if02")
    parser.add_argument("--target", default="BSGR_TX01", help="Target TX BLE name")
    parser.add_argument("--chunk-size", type=int, default=128, help="Upload chunk size, max 128")
    parser.add_argument("--reboot", action="store_true", help="Only connect to the TX target and reboot it")
    args = parser.parse_args()

    if args.chunk_size <= 0 or args.chunk_size > 128:
        raise SystemExit("--chunk-size must be between 1 and 128")

    if not args.reboot and not args.image:
        raise SystemExit("image is required unless --reboot is used")

    image_path = os.path.abspath(args.image) if args.image else None
    image_size = os.path.getsize(image_path) if image_path else 0

    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_SYNC)
    try:
        configure_serial(fd)
        if args.reboot:
            send_line(fd, f"CONNECT {args.target}")
            expect_ok(fd, 25.0)
            send_line(fd, "REBOOT")
            expect_ok(fd, 15.0)
            return 0

        send_line(fd, f"BEGIN {args.target} {image_size}")
        expect_ok(fd, 25.0)

        sent = 0
        with open(image_path, "rb") as image_file:
            while True:
                chunk = image_file.read(args.chunk_size)
                if not chunk:
                    break
                send_line(fd, f"CHUNK {len(chunk)}")
                os.write(fd, chunk)
                expect_ok(fd, 30.0)
                sent += len(chunk)
                print(f"progress {sent}/{image_size}")

        send_line(fd, "END")
        expect_ok(fd, 30.0)
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
