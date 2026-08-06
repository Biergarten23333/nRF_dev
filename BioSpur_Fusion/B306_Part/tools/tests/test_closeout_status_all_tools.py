#!/usr/bin/env python3
"""No capture tool may leave result.json reading status=RUNNING after it stops.

Covers all seven tools that write a result.json from a `finally`. Each is run
against stubbed hardware with a forced SIGINT and a forced unexpected
exception, and the status that actually reached disk is asserted.

Run with `--prefix` to point the same assertions at reconstructed pre-fix
copies, which proves the test catches the real defect instead of restating the
implementation.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from closeout_harness import (          # noqa: E402
    EXPECTED, EXPECTED_PREFIX, TOOLS, build_prefix_copies, run_tool,
)

prefix_mode = "--prefix" in sys.argv
expected = EXPECTED_PREFIX if prefix_mode else EXPECTED

with tempfile.TemporaryDirectory() as tmp:
    if prefix_mode:
        paths = build_prefix_copies(Path(tmp) / "prefix")
        print(f"=== reconstructed PRE-FIX copies ({len(paths)} tools) ===")
    else:
        paths = {name: TOOLS / name for name in EXPECTED}
        print(f"=== live POST-FIX tools ({len(paths)} tools) ===")

    failures = []
    for name in sorted(expected):
        row = []
        for behaviour in ("interrupt", "error"):
            want = expected[name][behaviour]
            got = run_tool(paths[name], behaviour).get("status")
            ok = got == want
            row.append(f"{behaviour}={got}" + ("" if ok else f" (want {want})"))
            if not ok:
                failures.append(f"{name} [{behaviour}]: got {got!r}, want {want!r}")
            # The contract itself, independent of the per-tool table.
            if not prefix_mode and got == "RUNNING":
                failures.append(f"{name} [{behaviour}]: left RUNNING on disk")
        print(f"  {'FAIL' if any('want' in c for c in row) else 'ok  '}  "
              f"{name:<34} {'  '.join(row)}")

if failures:
    print("\n".join(f"FAILURE: {f}" for f in failures))
    raise SystemExit(1)

if prefix_mode:
    print("\npre-fix copies reproduce the defect exactly as tabulated: PASS")
else:
    print("\nall seven tools write a terminal status on every exit path: PASS")
