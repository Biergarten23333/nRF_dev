#!/usr/bin/env python3
"""Flash a read-only DK inspector per BSF, capture image-state, restore v36."""
from __future__ import annotations

import argparse, hashlib, json, re, subprocess, time
from datetime import datetime, timezone
from pathlib import Path

import cbor2
import pylink

SNR = 683234364
DEVICE = "NRF52840_XXAA"
NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
         "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def flash(image: Path, verify_bin: Path, work: Path, label: str) -> None:
    script = work / f"{label}.jlink"
    log = work / f"{label}_flash.log"
    load = f"loadfile {image}" if image.suffix == ".hex" else f"loadbin {image},0x00000000"
    script.write_text("r\nh\nerase\n" + load + "\n" +
                      f"verifybin {verify_bin},0x00000000\nr\ng\nq\n")
    proc = subprocess.run([
        "/usr/bin/JLinkExe", "-NoGui", "1", "-SelectEmuBySN", str(SNR),
        "-Device", DEVICE, "-If", "SWD", "-Speed", "4000",
        "-CommanderScript", str(script)], capture_output=True, text=True)
    log.write_text(proc.stdout + proc.stderr)
    if proc.returncode or "O.K." not in proc.stdout:
        raise RuntimeError(f"{label} flash/verify failed rc={proc.returncode}")


def capture(node: str, out: Path, timeout_s: float) -> dict:
    jl = pylink.JLink(); raw = bytearray(); started = time.monotonic()
    try:
        jl.open(serial_no=SNR)
        jl.set_tif(pylink.enums.JLinkInterfaces.SWD)
        jl.connect(DEVICE, speed=4000, verbose=False)
        jl.rtt_start()
        while time.monotonic() - started < timeout_s:
            data = jl.rtt_read(0, 4096)
            if data:
                raw.extend(bytes(data))
                text = raw.decode(errors="replace")
                match = re.search(r"OTA IMG_STATE rsp bytes:((?: [0-9a-f]{2})+)\r?\n", text)
                if match:
                    payload = bytes.fromhex(match.group(1))
                    decoded = cbor2.loads(payload)
                    (out / f"{node}_rtt.txt").write_text(text)
                    (out / f"{node}_cbor.bin").write_bytes(payload)
                    return {"node": node, "status": "READ_OK", "images": decoded.get("images", []),
                            "payload_sha256": hashlib.sha256(payload).hexdigest()}
            time.sleep(.01)
        raise RuntimeError("image-state CBOR not observed before timeout")
    finally:
        (out / f"{node}_rtt.txt").write_bytes(bytes(raw))
        try: jl.rtt_stop(); jl.close()
        except Exception: pass


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--timeout-s", type=float, default=45.0)
    ap.add_argument("--nodes", nargs="+", choices=NODES, default=list(NODES))
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    builds = root / "B306_Part/builds"
    base = builds / "dk-ota-b306-v31-verify-retry1-BSF8BC4"
    compile_text = (base / "dk_ota/compile_commands.json").read_text()
    required = ("APP_MASTER_OTA_UPLOAD_ENABLE=0", "APP_MASTER_OTA_VERIFY_ONLY=1",
                "APP_MASTER_OTA_RESET_ONLY=0")
    if not all(token in compile_text for token in required):
        raise RuntimeError("base inspector is not compile-time read-only")
    restore_hex = builds / "dk-fusion-imu-relay-v36-a/merged.hex"
    restore_bin = builds / "dk-fusion-imu-relay-v36-a/fusion_master/zephyr/zephyr.bin"
    result = {"schema": "biospur-b306-slot-inventory-v1", "started": datetime.now(timezone.utc).isoformat(),
              "probe": SNR, "inspector_flags": list(required), "restore_sha256": sha(restore_hex),
              "nodes": []}
    try:
        for node in args.nodes:
            image = builds / f"dk-slot-inventory-{node}/slot_inventory.bin"
            if image.read_bytes().count(node.encode()) != 1:
                raise RuntimeError(f"{node}: patched target identity count is not one")
            flash(image, image, args.out_dir, f"flash_{node}")
            try: row = capture(node, args.out_dir, args.timeout_s)
            except Exception as exc: row = {"node": node, "status": "READ_FAILED", "error": str(exc)}
            row["inspector_sha256"] = sha(image); result["nodes"].append(row)
            (args.out_dir / "result.json").write_text(json.dumps(result, indent=2, default=lambda x: x.hex()))
    finally:
        flash(restore_hex, restore_bin, args.out_dir, "restore_v36")
        result["ended"] = datetime.now(timezone.utc).isoformat(); result["master_restored"] = True
        (args.out_dir / "result.json").write_text(json.dumps(result, indent=2, default=lambda x: x.hex()))
    return 0 if all(row.get("status") == "READ_OK" for row in result["nodes"]) else 2


if __name__ == "__main__": raise SystemExit(main())
