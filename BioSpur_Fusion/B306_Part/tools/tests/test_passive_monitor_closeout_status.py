#!/usr/bin/env python3
"""A stopped capture must never leave result.json reading status=RUNNING.

The closeout writer lives in a `finally`, so it runs on every exit path. Before
the fix, only normal loop exit assigned COMPLETE: a SIGINT reached the writer
with the initial RUNNING still in place and the finished capture looked live.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))


class StubChannel:
    """Stands in for ThreadedLineChannel; `read` drives the exit path."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.transport_mode = None
        self.text_pending = set()
        self.closed = False

    def read(self, deadline):
        if self.behaviour == "interrupt":
            raise KeyboardInterrupt
        if self.behaviour == "error":
            raise RuntimeError("serial link collapsed")
        return ""  # normal: yield nothing until the deadline expires

    def health_snapshot(self):
        return {"red_markers": 0}

    def close(self):
        self.closed = True


def run(behaviour, duration_s):
    """Run main() against a stubbed channel and return the written result.json."""
    import t1_passive_specimen_monitor as mon

    channel = StubChannel(behaviour)
    mon.ThreadedLineChannel = lambda *a, **k: channel
    mon.resolve_fusion_port = lambda _: "/dev/null"

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "run"
        argv = sys.argv
        sys.argv = ["t1_passive_specimen_monitor.py", str(out),
                    "--duration-s", str(duration_s)]
        try:
            mon.main()
        except RuntimeError as exc:          # the FAILED path re-raises, by design
            assert "serial link collapsed" in str(exc)
        finally:
            sys.argv = argv
        assert channel.closed, f"{behaviour}: channel was not closed"
        return json.loads((out / "result.json").read_text())


# --- SIGINT during the loop: the case that produced the misleading artefact ---
r = run("interrupt", 60.0)
assert r["status"] == "INTERRUPTED", f"SIGINT wrote status={r['status']!r}"
assert r["stop_reason"] == "KeyboardInterrupt"
assert "duration_actual_s" in r, "interrupted run lost its actual duration"
assert "ended_wall" in r

# --- an unexpected fault must also be distinguishable, and must re-raise ---
r = run("error", 60.0)
assert r["status"] == "FAILED", f"exception wrote status={r['status']!r}"
assert "RuntimeError" in r["stop_reason"]
assert "duration_actual_s" in r

# --- the normal path is unchanged ---
r = run("normal", 0.05)
assert r["status"] == "COMPLETE", f"clean run wrote status={r['status']!r}"
assert "stop_reason" not in r
assert r["duration_actual_s"] >= 0.0

# --- the invariant itself: no exit path may leave RUNNING on disk ---
for behaviour, dur in (("interrupt", 60.0), ("error", 60.0), ("normal", 0.05)):
    assert run(behaviour, dur)["status"] != "RUNNING"

print("passive monitor closeout status: PASS")
