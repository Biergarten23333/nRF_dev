#!/usr/bin/env python3
"""4.3 — OTA b306-imu-relay-v41 to all ten, one batch, no board goes first.

Each node gets one fresh no-retry transaction through
`v32_ota_board_transaction.py`. A failure quarantines that node and the batch
continues; nothing here waits on a fleet-wide condition (trap 6.3).

Pool occupancy is sampled between every transaction, so the rollout's own
Master-plane interruptions are observed as they happen rather than only at the
end. The canonical DK is restored by the transaction tool after every updater.
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
TOOLS = ROOT / "B306_Part/tools"
EVID = ROOT / "B306_Part/logs/v43_selfcapture_20260807/B3_OTA"
OPS = ROOT / "UWB_Part/logs/deploy_20260806/b_fusion_ops.py"

SOURCE_MARKER_DEFAULT = "b306-imu-relay-v41"
# Observed end to end by the 4.2 PING gate at 20:34:47, not assumed.
SOURCE_MARKER = {
    # BSFC2CC never took v41 -- it was wedged through the N6 rollout. It jumps
    # v38 -> v43, five generations, and is this batch's stress sample. The other
    # eight are a clean v41 -> v43 control group. Read end to end by the PING
    # gate at 01:26, not assumed.
    "BSFC2CC": "b306-imu-relay-v38",
}
TARGET_MARKER = "b306-imu-relay-v43"
BUILD_PREFIX = "dk-ota-b306-v43-"
CONFIRM_TOOL = "confirm_b306_v43.py"
MASTER_MARKER = "dk-fusion-imu-relay-v35"
RESTORE_BUILD = "dk-fusion-imu-relay-v35-a"
RESTORE_MERGED_SHA = "dcf0d639b8fef7a575e2b3c384dc84babac224edfade92c4d139187e602cc2b9"
RESTORE_BIN_SHA = "d91bbfd5b6675dfdc0db5e7e243fa23b1c7cc156487655f21acf95d04d0b4b73"
# Trap 6.6: the outer bound must exceed every deadline it wraps.
CAPTURE_BOUND_S = 417.874

# merged.hex SHA-256 of each node-addressed v43 updater, read from the build
# manifest rather than pasted, so the table and the artifacts cannot drift.
UPDATER_SHA = {}
for _line in (ROOT / "B306_Part/logs/v43_selfcapture_20260807/A2_UPDATER_BUILDS/BUILD_HASHES.txt").read_text().splitlines():
    _m = re.match(r"(\S+) merged=(\w+) ", _line)
    if _m:
        UPDATER_SHA[_m.group(1)] = _m.group(2)
# One batch, identical images, one session — no canary and no staged rollout.
# BSFC2CC did not answer the 4.2 PING gate and is not connected; the two v36
# boards are the next most likely to be quarantined. All three are placed last
# only so that, if their bounded gates burn time, the healthy boards are already
# upgraded. Every one is still attempted, and its own per-target PING gate will
# quarantine it before any write occurs.
# BSFAA61 is absent because it is ALREADY on v43: it was the Stage 2 canary and
# is confirmed=1. Re-running its transaction would re-open a boot-confirm
# rollback window on a board that is already correct, for nothing.
# BSF44AD is attempted last: it did not answer the 01:26 PING gate after the
# operator's power cycle, so its own per-target gate will quarantine it before
# any write, and placing it last means a healthy board never waits behind it.
ORDER = ("BSF6C53", "BSF1120", "BSF31CC", "BSFEC35", "BSFB165",
         "BSF3C79", "BSF8BC4", "BSFC2CC", "BSF44AD")


def wall():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def phase_times(out_dir):
    """Phase boundaries from artifact mtimes.

    capture_jlink_rtt.py does not timestamp individual RTT lines, so the pure
    upload interval is not separately resolvable. What these give is the
    updater phase (scan + upload + post-reset verify) and the restore phase,
    which are directly comparable across boards because every board runs the
    identical sequence.
    """
    def mt(name):
        p = out_dir / name
        return p.stat().st_mtime if p.exists() else None

    flash, rtt, restore = mt("flash_updater_jlink.log"), mt("updater_rtt.log"), \
        mt("restore_v28_jlink.log")
    res = {}
    if flash and rtt:
        res["updater_phase_s"] = round(rtt - flash, 3)
    if rtt and restore:
        res["restore_phase_s"] = round(restore - rtt, 3)
    return res


def pool_sample(tag, duration_s=8.0):
    d = EVID / f"pool_{tag}"
    try:
        subprocess.run(
            [sys.executable, str(OPS), "pool-sample", str(d),
             "--duration-s", str(duration_s), "--guard-s", "25"],
            cwd=ROOT, timeout=duration_s + 90, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        r = json.loads((d / "result.json").read_text())
        return {"records": r.get("pool_sample_count"),
                "sources": r.get("pool_sources"),
                "floor": r.get("pool_floor")}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def main():
    preflight = Path(sys.argv[1])
    EVID.mkdir(parents=True, exist_ok=True)
    summary = {"status": "RUNNING", "started_wall": wall(),
               "target_marker": TARGET_MARKER, "source_marker": "per-node",
               "capture_bound_s": CAPTURE_BOUND_S,
               "preflight": str(preflight), "boards": {}, "pool_series": {}}

    summary["pool_series"]["pre_rollout"] = pool_sample("00_pre")
    print(f"pre-rollout pool sample: {summary['pool_series']['pre_rollout']['records']} records",
          flush=True)

    t_batch = time.monotonic()
    for i, node in enumerate(ORDER, 1):
        out_dir = EVID / node
        print(f"\n=== [{i}/{len(ORDER)}] {node} — OTA transaction ===", flush=True)
        row = {"index": i, "started_wall": wall()}
        t0 = time.monotonic()
        cmd = [
            sys.executable, str(TOOLS / "v32_ota_board_transaction.py"),
            "--node", node, "--out-dir", str(out_dir), "--deployment-only",
            "--source-marker", SOURCE_MARKER.get(node, SOURCE_MARKER_DEFAULT),
            "--target-marker", TARGET_MARKER,
            "--build-prefix", BUILD_PREFIX, "--updater-sha", UPDATER_SHA[node],
            "--confirm-tool", CONFIRM_TOOL, "--master-marker", MASTER_MARKER,
            "--restore-build", RESTORE_BUILD,
            "--restore-merged-sha", RESTORE_MERGED_SHA,
            "--restore-bin-sha", RESTORE_BIN_SHA,
            "--skip-preflight", "--fleet-preflight-result", str(preflight),
            "--preflight-require", "target-only",
            "--capture-timeout-s", str(CAPTURE_BOUND_S),
        ]
        try:
            proc = subprocess.run(cmd, cwd=ROOT, timeout=CAPTURE_BOUND_S + 600,
                                  capture_output=True, text=True)
            row["rc"] = proc.returncode
            row["stdout_tail"] = proc.stdout[-800:]
            row["stderr_tail"] = proc.stderr[-800:]
        except subprocess.TimeoutExpired:
            row["rc"] = -1
            row["error"] = "orchestrator timeout around the transaction"
        row["transaction_wall_s"] = round(time.monotonic() - t0, 3)
        row.update(phase_times(out_dir))

        tj = out_dir / "transaction.json"
        if tj.exists():
            t = json.loads(tj.read_text())
            row["transaction_status"] = t.get("status")
            row["updater_capture"] = t.get("updater_capture")
            row["deployment_readback"] = t.get("deployment_readback")
            row["emergency_master_restore"] = t.get("emergency_master_restore")
            row["transaction_error"] = t.get("error")
        row["verdict"] = "PASS" if row.get("transaction_status") == "PASS" else "QUARANTINE"
        row["ended_wall"] = wall()
        summary["boards"][node] = row
        print(f"  {node}: {row['verdict']} rc={row.get('rc')} "
              f"wall={row['transaction_wall_s']}s "
              f"updater_phase={row.get('updater_phase_s')}s", flush=True)

        summary["pool_series"][f"after_{i:02d}_{node}"] = pool_sample(f"{i:02d}_{node}")
        (EVID / "BATCH_SUMMARY.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n")

    summary["batch_wall_s"] = round(time.monotonic() - t_batch, 3)
    summary["passed"] = sorted(n for n, r in summary["boards"].items()
                               if r["verdict"] == "PASS")
    summary["quarantined"] = sorted(n for n, r in summary["boards"].items()
                                    if r["verdict"] != "PASS")
    summary["status"] = "COMPLETE"
    summary["ended_wall"] = wall()
    (EVID / "BATCH_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"\n=== BATCH DONE passed={len(summary['passed'])}/{len(ORDER)} "
          f"quarantined={summary['quarantined']} wall={summary['batch_wall_s']}s ===",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
