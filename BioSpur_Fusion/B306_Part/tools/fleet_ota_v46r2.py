#!/usr/bin/env python3
"""OTA the nine remaining boards to v46r2-prod. One continuous batch.

WHY THE VERIFICATION IS A CONTENT CHECK, NOT A MARKER CHECK
-----------------------------------------------------------
`b306-imu-relay-v45` was byte-identical across v46-val, v46r2-val and
v46r2-prod. Every marker-keyed check in this pipeline -- the updater's
B306_OTA_MARKER, the transaction's --source/--target-marker, and
confirm_b306_v32.py's B306_MARKER -- therefore read the same value before and
after a deployment. Demonstrated on BSF6C53 tonight: the payload uploaded, its
hash verified, `prepared=0`, and the pipeline reported the target already
deployed. On nine boards that reports success while changing nothing.

So a board counts as updated only if it answers `V45 GUARD`, a command that
exists ONLY in v46r2. That is a property of the running image, not of a string
the pipeline was told to expect.

NO PER-BOARD GATES. The one-first-then-nine pattern is barred: it cost a full
round in relay8.3 and gave false confidence twice because the single board was
the anomalous one. A board that fails is recorded and the batch continues.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
B306 = ROOT / "B306_Part"
TOOLS = B306 / "tools"
TC = "/home/zekaixiao/ncs/toolchains/b81a7cd864"

NINE = ["BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF8BC4",
        "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35"]

PAYLOAD = B306 / "builds/b306-v46r2-prod/firmware/zephyr/zephyr.signed.bin"
TARGET_MARKER = "b306-imu-relay-v46"
SOURCE_MARKER = "b306-imu-relay-v44"
PREFIX = "dk-ota-b306-v46r2p-"
OUT = B306 / "logs/v46r2_20260809/FLEET_OTA"
PREFLIGHT = B306 / "logs/v46r2_20260809/fleetpre/target_only_result.json"

RESTORE = dict(
    build="dk-fusion-imu-relay-v36-a",
    marker="dk-fusion-imu-relay-v36",
    merged="7a7d02cdae13b4450ffea0cb2a46607d481f3760a95e6c38d4c9dd03a2290b56",
    binsha="59bd57b80d762f5c3d9af9b0d0d303d288584f6f06f5baf5349a3cf3c5628b47",
)


def sh(cmd, log, env=None, timeout=1800):
    with open(log, "wb") as fh:
        return subprocess.run(cmd, cwd=str(ROOT), stdout=fh,
                              stderr=subprocess.STDOUT, env=env,
                              timeout=timeout).returncode


def patch(action):
    return subprocess.run([str(B306 / "firmware/patches/sdk_patch.sh"), action],
                          capture_output=True, text=True).stdout.strip()


def build_updater(node, sha, log):
    import os
    env = dict(os.environ)
    env.update({
        "B306_OTA_TARGET_NAME": node,
        "B306_OTA_MARKER": TARGET_MARKER,
        "B306_OTA_IMAGE": str(PAYLOAD),
        "B306_OTA_IMAGE_SHA256": sha,
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": f"{TC}/usr/local/lib/python3.12/site-packages",
        "ZEPHYR_BASE": "/home/zekaixiao/ncs/v2.8.0/zephyr",
        "ZEPHYR_TOOLCHAIN_VARIANT": "zephyr",
        "ZEPHYR_SDK_INSTALL_DIR": f"{TC}/opt/zephyr-sdk",
    })
    d = B306 / "builds" / f"{PREFIX}{node}"
    return sh([f"{TC}/usr/local/bin/python3", "-m", "west", "build",
               "--pristine=always", "-b", "nrf52840dk/nrf52840",
               "-s", str(B306 / "host/dk_ota"), "-d", str(d)], log, env), d


def content_check(node, outdir):
    """A board is updated only if it answers V45 GUARD -- v46r2-only."""
    log = outdir / f"content_{node}.log"
    rc = sh([sys.executable, str(TOOLS / "v45_bench.py"),
             "--outdir", str(outdir / f"content_{node}"), "--node", node,
             "--label", f"c_{node}", "cmd", "V45 GUARD", "STATUS",
             "--observe-before", "10", "--observe-after", "3"], log, timeout=300)
    txt = log.read_text(errors="replace")
    has_guard = "V45 GUARD rcv=" in txt
    on_v46 = f"fw={TARGET_MARKER}" in txt
    return {"rc": rc, "v45_guard_answers": has_guard,
            "marker_v46": on_v46, "updated": has_guard}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    import hashlib
    sha = hashlib.sha256(PAYLOAD.read_bytes()).hexdigest()
    ledger = {"payload": str(PAYLOAD), "payload_sha256": sha,
              "target_marker": TARGET_MARKER, "boards": {}}

    print(f"payload {PAYLOAD.name} sha={sha[:16]} marker={TARGET_MARKER}", flush=True)

    # Updaters build against a PRISTINE SDK: the v45/v46 #error guards make the
    # shared install unbuildable for any project without bsf_v45_trace.h on its
    # include path, and dk_ota is exactly that. Revert once for all nine.
    print("revert:", patch("revert"), flush=True)
    updaters = {}
    for node in NINE:
        rc, d = build_updater(node, sha, OUT / f"updater_{node}.log")
        m = d / "merged.hex"
        updaters[node] = hashlib.sha256(m.read_bytes()).hexdigest() if (rc == 0 and m.exists()) else None
        print(f"  updater {node}: {'ok' if updaters[node] else 'FAILED'}", flush=True)
    print("apply:", patch("apply"), flush=True)
    print("verify:", patch("verify"), flush=True)

    for node in NINE:
        row = {"updater_sha": updaters[node]}
        if not updaters[node]:
            row["status"] = "UPDATER_BUILD_FAILED"
            ledger["boards"][node] = row
            continue
        d = OUT / node
        if d.exists():
            import shutil; shutil.rmtree(d)
        t0 = time.monotonic()
        rc = sh([sys.executable, str(TOOLS / "v32_ota_board_transaction.py"),
                 "--node", node, "--out-dir", str(d), "--deployment-only",
                 "--source-marker", SOURCE_MARKER, "--target-marker", TARGET_MARKER,
                 "--build-prefix", PREFIX, "--updater-sha", updaters[node],
                 "--master-marker", RESTORE["marker"],
                 "--restore-build", RESTORE["build"],
                 "--restore-marker", RESTORE["marker"],
                 "--restore-merged-sha", RESTORE["merged"],
                 "--restore-bin-sha", RESTORE["binsha"],
                 "--skip-preflight", "--fleet-preflight-result", str(PREFLIGHT),
                 "--preflight-require", "target-only"],
                OUT / f"txn_{node}.log", timeout=1800)
        row["txn_rc"] = rc
        row["elapsed_s"] = round(time.monotonic() - t0, 1)
        row["content"] = content_check(node, OUT)
        row["status"] = "OK" if (rc == 0 and row["content"]["updated"]) else "FAILED"
        ledger["boards"][node] = row
        print(f"{node}: rc={rc} updated={row['content']['updated']} "
              f"{row['elapsed_s']}s -> {row['status']}", flush=True)
        (OUT / "ledger.json").write_text(json.dumps(ledger, indent=2))

    (OUT / "ledger.json").write_text(json.dumps(ledger, indent=2))
    ok = [n for n, r in ledger["boards"].items() if r.get("status") == "OK"]
    print(f"\nFLEET OTA DONE: {len(ok)}/{len(NINE)} updated -> {sorted(ok)}", flush=True)
    bad = [n for n in NINE if n not in ok]
    if bad:
        print(f"FAILED: {sorted(bad)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
