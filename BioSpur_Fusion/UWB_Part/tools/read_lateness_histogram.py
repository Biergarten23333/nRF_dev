#!/usr/bin/env python3
"""Read and validate the Tag's paged lateness histogram over the Tag Master.

Every page request is correlated with the matching ``BSLLATEH`` response and
retried when the BLE command or notification is lost.  A read is accepted only
when two consecutive complete eight-page rounds are byte-for-byte identical.
That is deliberately strict: with the current live, cumulative Tag histogram,
the hot page changes at 10 Hz and this program must fail instead of labelling a
non-atomic set of pages as a snapshot.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import serial


HIST_RE = re.compile(
    r"BSLLATEH;1;page=(?P<page>\d+);start_tick=(?P<start>-?\d+);"
    r"counts=(?P<counts>[0-9,]+)"
)
SUMMARY_RE = re.compile(r"BSLLATE;1;[^\r\n]*")


@dataclass(frozen=True)
class Page:
    number: int
    start_tick: int
    counts: tuple[int, ...]


class MasterPort:
    def __init__(self, port: str, baud: int, log: TextIO) -> None:
        self.log = log
        self.started = time.monotonic()
        self.buffer = bytearray()
        self.handle = serial.Serial()
        self.handle.port = port
        self.handle.baudrate = baud
        self.handle.timeout = 0.05
        self.handle.write_timeout = 2.0
        self.handle.exclusive = True
        self.handle.dtr = False
        self.handle.rts = False
        self.handle.open()
        self._record(f"OPEN {port} resolved={Path(port).resolve()} DTR=0 RTS=0")

    def close(self) -> None:
        self.handle.close()

    def _record(self, text: str) -> None:
        self.log.write(f"[{time.monotonic() - self.started:9.3f}] {text}\n")
        self.log.flush()

    def command(self, command: str) -> None:
        self._record(f">>> {command}")
        self.handle.write((command + "\n").encode("utf-8"))
        self.handle.flush()

    def lines_until(self, deadline: float):
        while time.monotonic() < deadline:
            data = self.handle.read(self.handle.in_waiting or 1)
            if not data:
                continue
            self.buffer.extend(data)
            while b"\n" in self.buffer:
                raw, _, remainder = self.buffer.partition(b"\n")
                self.buffer = bytearray(remainder)
                line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
                self._record(f"RECV {line}")
                yield line

    def wait_regex(self, pattern: re.Pattern[str], timeout: float):
        for line in self.lines_until(time.monotonic() + timeout):
            match = pattern.search(line)
            if match:
                return match
        return None


def read_summary(master: MasterPort, timeout: float, retries: int) -> str:
    for attempt in range(1, retries + 1):
        master.command("cmd BSL_LATE_SUMMARY")
        match = master.wait_regex(SUMMARY_RE, timeout)
        if match:
            return match.group(0)
        master._record(f"SUMMARY_RETRY attempt={attempt} reason=no_matching_response")
    raise RuntimeError(f"no BSLLATE summary after {retries} attempts")


def read_page(
    master: MasterPort, page_number: int, timeout: float, retries: int
) -> Page:
    for attempt in range(1, retries + 1):
        master.command(f"cmd BSL_LATE_HIST {page_number}")
        deadline = time.monotonic() + timeout
        for line in master.lines_until(deadline):
            match = HIST_RE.search(line)
            if not match or int(match.group("page")) != page_number:
                continue
            counts = tuple(int(value) for value in match.group("counts").split(","))
            if len(counts) != 24:
                master._record(
                    f"PAGE_RETRY page={page_number} attempt={attempt} "
                    f"reason=count_length_{len(counts)}"
                )
                break
            return Page(page_number, int(match.group("start")), counts)
        else:
            master._record(
                f"PAGE_RETRY page={page_number} attempt={attempt} "
                "reason=no_matching_response"
            )
    raise RuntimeError(f"page {page_number} missing after {retries} attempts")


def read_round(
    master: MasterPort, timeout: float, retries: int
) -> tuple[str, tuple[Page, ...], str]:
    before = read_summary(master, timeout, retries)
    pages = tuple(read_page(master, page, timeout, retries) for page in range(8))
    after = read_summary(master, timeout, retries)
    return before, pages, after


def page_payload(pages: tuple[Page, ...]) -> tuple[tuple[int, tuple[int, ...]], ...]:
    return tuple((page.start_tick, page.counts) for page in pages)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-rounds", type=int, default=3)
    args = parser.parse_args()

    if args.timeout <= 0 or args.retries < 1 or args.max_rounds < 2:
        parser.error("timeout must be positive, retries >= 1, and max-rounds >= 2")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    rounds: list[dict[str, object]] = []
    accepted: tuple[Page, ...] | None = None
    with args.log.open("w", encoding="utf-8") as log:
        master = MasterPort(args.port, args.baud, log)
        try:
            previous: tuple[Page, ...] | None = None
            for round_number in range(1, args.max_rounds + 1):
                try:
                    before, pages, after = read_round(
                        master, args.timeout, args.retries
                    )
                except RuntimeError as exc:
                    master._record(f"HIST_READ_FAIL round={round_number} reason={exc}")
                    return 2
                rounds.append(
                    {
                        "round": round_number,
                        "summary_before": before,
                        "summary_after": after,
                        "pages": [
                            {
                                "page": page.number,
                                "start_tick": page.start_tick,
                                "counts": list(page.counts),
                            }
                            for page in pages
                        ],
                    }
                )
                if previous is not None and page_payload(previous) == page_payload(pages):
                    accepted = pages
                    master._record(
                        f"HIST_ATOMIC_PASS rounds={round_number - 1},{round_number}"
                    )
                    break
                if previous is not None:
                    master._record(
                        f"HIST_ROUND_CHANGED previous={round_number - 1} "
                        f"current={round_number}"
                    )
                previous = pages
        finally:
            master.close()

    result = {
        "atomic": accepted is not None,
        "rounds": rounds,
        "reason": (
            "two consecutive complete rounds identical"
            if accepted is not None
            else "no two consecutive complete rounds identical"
        ),
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    if accepted is None:
        print(
            "HIST_ATOMIC_FAIL: all requested rounds were complete, but no two "
            "consecutive rounds were identical"
        )
        return 3
    print("HIST_ATOMIC_PASS: two consecutive complete rounds were identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
