#!/usr/bin/env python3
"""Shared harness for the closeout-status contract.

Every capture/audit tool here initialises `status` to RUNNING, assigns a
terminal status at the end of its `try`, and writes result.json from a
`finally`. If a tool leaves the try by any path that does not assign a terminal
status, the closeout faithfully writes RUNNING and a finished run looks live.

This module runs each tool against stubbed hardware and a fast clock, forces a
chosen exit path, and returns the status that reached disk. It also reconstructs
pre-fix copies by exact reversal, so the same assertions can be pointed at the
old code to prove they actually catch the defect.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]

# Expected status on disk after each forced exit path.
#   interrupt -> SIGINT inside the tool's main loop
#   error     -> an unexpected RuntimeError inside the tool's main loop
EXPECTED = {
    "t1_passive_specimen_monitor.py": {"interrupt": "INTERRUPTED", "error": "FAILED"},
    "r1_fleet_stall_read.py":         {"interrupt": "INTERRUPTED", "error": "FAILED"},
    "v36_d2_preflight.py":            {"interrupt": "INTERRUPTED", "error": "FAILED"},
    "v37_post_rollout_audit.py":      {"interrupt": "INTERRUPTED", "error": "FAILED"},
    "r1_three_board_scan.py":         {"interrupt": "INTERRUPTED", "error": "FAILED"},
    "v35_verify.py":                  {"interrupt": "INTERRUPTED", "error": "FAILED"},
    # This one already mapped generic exceptions to FAIL and deliberately does
    # not re-raise; that contract is preserved. KeyboardInterrupt is not an
    # Exception subclass, so only the SIGINT path needed closing.
    "v36_stall_read_inventory.py":    {"interrupt": "INTERRUPTED", "error": "FAIL"},
}

# What the same paths produced before the fix.
EXPECTED_PREFIX = {
    name: {"interrupt": "RUNNING",
           "error": "FAIL" if name == "v36_stall_read_inventory.py" else "RUNNING"}
    for name in EXPECTED
}


# --- reconstruction of the pre-fix sources, by exact reversal ---------------
# Each entry is (text_after_fix, text_before_fix). Both halves are asserted to
# be present/absent, so a future edit that invalidates a reversal fails loudly
# rather than silently testing the wrong thing.

def _block(terminal, closeout, comment_extra="", pre_extra=""):
    """The uniform 1-space-indented patch shape used by six of the tools."""
    after = (f"{terminal}\n"
             " except KeyboardInterrupt:\n"
             "  out['status']='INTERRUPTED';out['stop_reason']='KeyboardInterrupt'\n"
             " except BaseException as exc:\n"
             "  out['status']='FAILED';out['stop_reason']=f'{type(exc).__name__}: {exc}';raise\n"
             " finally:\n"
             "  # The writer runs on every exit path, so a status left at RUNNING would make\n"
             f"  # a stopped run look live.{comment_extra} No exit path may leave RUNNING on disk.\n"
             "  if out['status']=='RUNNING':out['status']='INTERRUPTED';out.setdefault('stop_reason','closeout reached without a terminal status')\n"
             f"{pre_extra}"
             f"{closeout}\n")
    before = f"{terminal}\n finally:\n{closeout}\n"
    return after, before


REVERSALS = {
    "r1_fleet_stall_read.py": [
        _block("  out['status']='COMPLETE'",
               "  out['health']=ch.health_snapshot();ch.close()"),
    ],
    "v37_post_rollout_audit.py": [
        _block("  out['status']='COMPLETE'",
               "  out['host_health']=ch.health_snapshot();ch.close()"),
    ],
    "r1_three_board_scan.py": [
        _block("  out['status']='COMPLETE'",
               "  out['ended']=wall();out['health']=ch.health_snapshot();ch.close();(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\\n')"),
    ],
    "v35_verify.py": [
        _block("  print(f'=== V35 P8 WINDOW CLOSED === mono={time.monotonic():.6f} wall={wall()}',flush=True)",
               "  out['ended']=wall();dump(root/'result.json',out);ch.close();log.close()"),
    ],
    # Not built from _block: this one carries an extra comment sentence and an
    # elapsed_s default, so the reversal is spelled out in full.
    "v36_d2_preflight.py": [(
        "  out['status']='PASS';out['elapsed_s']=time.time()-out['started'];out['health']=ch.health_snapshot()\n"
        " except KeyboardInterrupt:\n"
        "  out['status']='INTERRUPTED';out['stop_reason']='KeyboardInterrupt'\n"
        " except BaseException as exc:\n"
        "  out['status']='FAILED';out['stop_reason']=f'{type(exc).__name__}: {exc}';raise\n"
        " finally:\n"
        "  # The writer runs on every exit path, so a status left at RUNNING would make\n"
        "  # a stopped run look live. A raised contract failure above must read FAILED,\n"
        "  # never RUNNING. No exit path may leave RUNNING on disk.\n"
        "  if out['status']=='RUNNING':out['status']='INTERRUPTED';out.setdefault('stop_reason','closeout reached without a terminal status')\n"
        "  out.setdefault('elapsed_s',time.time()-out['started'])\n"
        "  ch.close();(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\\n')\n",
        "  out['status']='PASS';out['elapsed_s']=time.time()-out['started'];out['health']=ch.health_snapshot()\n"
        " finally:\n"
        "  ch.close();(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\\n')\n",
    )],
    "v36_stall_read_inventory.py": [(
        '        out["status"] = "PASS"\n'
        '    except KeyboardInterrupt:\n'
        '        # Not an Exception subclass, so the handler below never saw it and the\n'
        '        # closeout wrote the initial RUNNING.\n'
        '        out["status"] = "INTERRUPTED"; out["stop_reason"] = "KeyboardInterrupt"\n'
        '    except Exception as exc:\n'
        '        out["status"] = "FAIL"; out["error"] = f"{type(exc).__name__}: {exc}"\n'
        '    finally:\n'
        '        # The writer runs on every exit path, so a status left at RUNNING would\n'
        '        # make a stopped run look live. No exit path may leave RUNNING on disk.\n'
        '        if out["status"] == "RUNNING":\n'
        '            out["status"] = "INTERRUPTED"\n'
        '            out.setdefault("stop_reason", "closeout reached without a terminal status")\n'
        '        out["host_health"] = ch.health_snapshot(); ch.close()\n',
        '        out["status"] = "PASS"\n'
        '    except Exception as exc:\n'
        '        out["status"] = "FAIL"; out["error"] = f"{type(exc).__name__}: {exc}"\n'
        '    finally:\n'
        '        out["host_health"] = ch.health_snapshot(); ch.close()\n',
    )],
    "t1_passive_specimen_monitor.py": [
        ("        started = None\n        try:\n", "        try:\n"),
        ('            result["status"] = "COMPLETE"\n'
         '        except KeyboardInterrupt:\n'
         '            result["status"] = "INTERRUPTED"\n'
         '            result["stop_reason"] = "KeyboardInterrupt"\n'
         '        except BaseException as exc:\n'
         '            result["status"] = "FAILED"\n'
         '            result["stop_reason"] = f"{type(exc).__name__}: {exc}"\n'
         '            raise\n'
         '        finally:\n'
         '            # The closeout writer runs on every exit path, so a status left at\n'
         '            # RUNNING would make a stopped capture look live to any later\n'
         '            # reader. Nothing below may leave RUNNING in the file.\n'
         '            if result["status"] == "RUNNING":\n'
         '                result["status"] = "INTERRUPTED"\n'
         '                result.setdefault("stop_reason", "closeout reached without a terminal status")\n'
         '            if started is not None:\n'
         '                result["duration_actual_s"] = time.monotonic() - started\n'
         '            result["ended_wall"] = wall_time()\n',
         '            result["status"] = "COMPLETE"\n'
         '            result["duration_actual_s"] = time.monotonic() - started\n'
         '        finally:\n'
         '            result["ended_wall"] = wall_time()\n'),
    ],
}


def build_prefix_copies(dest: Path) -> dict:
    """Write pre-fix copies of every tool into `dest`. Returns {name: path}."""
    dest.mkdir(parents=True, exist_ok=True)
    made = {}
    for name, pairs in REVERSALS.items():
        src = (TOOLS / name).read_text()
        for after, before in pairs:
            assert after in src, f"{name}: post-fix block not found — reversal is stale"
            src = src.replace(after, before, 1)
        assert "INTERRUPTED" not in src, f"{name}: reversal left INTERRUPTED behind"
        assert "may leave RUNNING" not in src, f"{name}: reversal left the guard behind"
        assert "'status':'RUNNING'" in src or '"status": "RUNNING"' in src \
            or '"status":"RUNNING"' in src, f"{name}: lost its RUNNING initialiser"
        compile(src, str(dest / name), "exec")
        (dest / name).write_text(src)
        made[name] = dest / name
    return made


# --- stubbed hardware and a fast clock -------------------------------------

class StubChannel:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.transport_mode = None
        self.text_pending = set()
        self.closed = False

    def read(self, deadline=None):
        if self.behaviour == "interrupt":
            raise KeyboardInterrupt
        if self.behaviour == "error":
            raise RuntimeError("serial link collapsed")
        return ""

    def send(self, _line):
        return None

    def health_snapshot(self):
        return {"red_markers": 0}

    def close(self):
        self.closed = True


class FastClock:
    """Stands in for `time`; monotonic jumps so hardcoded deadlines expire."""

    def __init__(self, real, step=7.0):
        self._real, self._step, self._t = real, step, 0.0

    def monotonic(self):
        self._t += self._step
        return self._t

    def time(self):
        return self._real.time()

    def sleep(self, _s):
        return None


class _Rate:
    delivered_rate_hz = 0.0
    flags = ()


def _install_stubs(behaviour, channels):
    """Put stub dependency modules into sys.modules; returns the originals."""
    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    def make_channel(*_a, **_k):
        ch = StubChannel(behaviour)
        channels.append(ch)
        return ch

    import time as real_time
    stubs = {
        "async_line_channel": mod("async_line_channel", ThreadedLineChannel=make_channel),
        "coldstart_fusion_control": mod("coldstart_fusion_control",
                                        decode_guard=lambda ch, n: {"ok": True}),
        "d1_blind_disturbance": mod("d1_blind_disturbance", SLOTS=("BSF3C79", "BSF44AD")),
        "fusion_session": mod("fusion_session",
                              parse_fields=lambda line: {},
                              parse_reply=lambda line: None,
                              resolve_fusion_port=lambda p: "/dev/null"),
        "capacity_ramp": mod("capacity_ramp",
                             b306_command=lambda ch, n, c, p: {"reply": ""}),
        "delivered_rate": mod("delivered_rate", delivered_rate=lambda *a, **k: _Rate()),
        "time": FastClock(real_time),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def run_tool(path: Path, behaviour: str) -> dict:
    """Execute one tool against stubs, forcing `behaviour`. Returns result.json."""
    channels = []
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "run"
        saved_argv = sys.argv
        saved = _install_stubs(behaviour, channels)
        sys.argv = ["tool", str(out_dir), "--duration-s", "60"]
        try:
            glb = {"__name__": "__main__", "__file__": str(path)}
            try:
                exec(compile(path.read_text(), str(path), "exec"), glb)
            except (SystemExit, KeyboardInterrupt, RuntimeError):
                pass          # terminal paths re-raise by design
        finally:
            _restore(saved)
            sys.argv = saved_argv
        result_file = out_dir / "result.json"
        assert result_file.exists(), f"{path.name}: no result.json was written"
        assert channels and all(c.closed for c in channels), \
            f"{path.name}: channel was not closed"
        return json.loads(result_file.read_text())
