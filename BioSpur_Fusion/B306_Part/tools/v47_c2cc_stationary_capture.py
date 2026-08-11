#!/usr/bin/env python3
"""Fail-closed BSFC2CC-only held-out stationary capture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from current_room_autopos_sw100 import (
    DIRECT_VERSION_RE,
    EXPECTED_PORT_NAME,
    EXPECTED_UUIDS,
    collect_text,
    parse_latest_mstat,
    port_identity,
    safe_open,
    send_read_only,
)
from fusion_session import parse_fields, parse_reply, resolve_fusion_port
from listener_array_run import wait_listener_preflight
from v47_afternoon_capture import POLL_RECEIVERS, listener_coverage


ROOT = Path(__file__).resolve().parents[2]
LISTENER = ROOT / "B306_Part/host/listener_array_collector.py"
GEOMETRY = ROOT / "B306_Part/deployments/current_room_autopos_20260811_183541/CAPTURE_BOUND_GEOMETRY_MANIFEST.json"
S2_DIR = ROOT / "B306_Part/logs/v47_full_system_30m_20260811_130843/analysis_fusion_dataset_exhaustion_v1"
S2_MANIFEST = S2_DIR / "S2_PARAMETER_MANIFEST.json"
S2_CODE = ROOT / "B306_Part/tools/v47_s2_fusion.py"
NODE = "BSFC2CC"
MASTER = "dk-fusion-imu-relay-v36"
MARKER = "b306-imu-relay-v47"
FWID = "f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed"
IMAGE = "90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98"
EXPECTED_ANCHORS = {
    "A": "BS1FFC", "B": "BS592A", "C": "BS5380", "D": "BS20AC",
    "E": "BS4B52", "F": "BS928B", "G": "BSEC88", "H": "BS506D",
}


def wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, value: object) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def evaluate_inventory(master: dict[str, str], aggregate: dict[str, str],
                       peers: list[dict[str, str]], pong: dict[str, str],
                       confirm: dict[str, str]) -> list[str]:
    failures: list[str] = []
    names = [row.get("name") for row in peers]
    if master.get("marker") != MASTER:
        failures.append("master_marker")
    for label, fields in (("master", master), ("list", aggregate)):
        if fields.get("count") != "1" or fields.get("ready") != "1":
            failures.append(f"{label}_count_ready")
    if names != [NODE]:
        failures.append("unexpected_or_duplicate_peer")
    if len(peers) == 1 and (peers[0].get("connected") != "1" or peers[0].get("subscribed") != "1"):
        failures.append("peer_not_connected_subscribed")
    expected = {"name": NODE, "fw": MARKER, "fwid": FWID, "image_sha": IMAGE}
    if any(pong.get(key) != value for key, value in expected.items()):
        failures.append("pong_identity")
    if confirm.get("confirmed") != "1":
        failures.append("not_confirmed")
    return failures


def anchor_preflight(out: Path) -> dict[str, object]:
    port = Path("/dev/serial/by-id") / EXPECTED_PORT_NAME
    identity = port_identity(port)
    ser = safe_open(str(port), 10.0)
    transcript_path = out / "anchor_master_read_only.log"
    try:
        spontaneous = collect_text(ser, 7.0)
        (out / "anchor_decode_guard.log").write_text(spontaneous, encoding="utf-8")
        if not parse_latest_mstat(spontaneous):
            raise RuntimeError("Anchor Master decode-before-send guard failed; zero TX")
        with transcript_path.open("x", encoding="utf-8", buffering=1) as transcript:
            status = send_read_only(ser, "status", 1.5, transcript)
            amap = send_read_only(ser, "autopos map show", 1.5, transcript)
            versions = send_read_only(ser, "anchor version all", 32.0, transcript)
            tail = collect_text(ser, 6.0); transcript.write(tail)
    finally:
        ser.close()
    combined = versions + tail
    rows: dict[str, dict[str, str]] = {}
    for match in DIRECT_VERSION_RE.finditer(combined):
        row = match.groupdict(); rows[row["label"]] = row
    maps = dict(__import__("re").findall(r"AUTOPOS map ([A-H])=([0-9A-F]{32})", amap))
    mstats = parse_latest_mstat(spontaneous + status + combined)
    legacy_ro = {k for k,row in rows.items() if row.get("role") is None and row.get("fw") == "anchor-freeze-clean-20260716"}
    checks = {
        "exact_map": maps == EXPECTED_UUIDS,
        "exact_labels": set(rows) == set(EXPECTED_ANCHORS),
        "exact_bs_identity": all(rows.get(k, {}).get("bs") == v for k, v in EXPECTED_ANCHORS.items()),
        "exact_uuid_identity": all(rows.get(k, {}).get("uuid") == EXPECTED_UUIDS[k] for k in EXPECTED_UUIDS),
        "responder_or_legacy_read_only_role_evidence": all(
            (rows.get(k, {}).get("role") or "").lower().startswith("res") or k in legacy_ro
            for k in EXPECTED_ANCHORS
        ),
        "eight_connected_ready": set(mstats) == set(range(8)) and all(x["connected"] and x["ready"] for x in mstats.values()),
    }
    result = {"status": "PASS" if all(checks.values()) else "BLOCKED", "identity": identity,
              "checks": checks, "anchors": rows, "legacy_ro_requires_live_range_witness": sorted(legacy_ro),
              "peer_state": [mstats[k] for k in sorted(mstats)],
              "commands": ["status", "autopos map show", "anchor version all"], "mutation": False}
    atomic(out / "ANCHOR_PREFLIGHT.json", result)
    if result["status"] != "PASS":
        raise RuntimeError(f"Anchor preflight blocked: {checks}")
    return result


def start_listener(out: Path, duration_s: float):
    log = (out.parent / f"{out.name}_collector.stdout.log").open("x", encoding="utf-8", buffering=1)
    proc = subprocess.Popen([sys.executable, str(LISTENER), "--out-dir", str(out),
        "--duration", str(duration_s), "--baud", "460800", "--require-kind", "LSTAT"],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True)
    return proc, log


def stop_listener(proc, log) -> tuple[int, dict[str, object] | None]:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    try:
        rc = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.terminate(); rc = proc.wait(timeout=10)
    log.close()
    summary_path = Path(proc.args[proc.args.index("--out-dir") + 1]) / "summary.json"
    return rc, json.loads(summary_path.read_text()) if summary_path.exists() else None


def query_sample(ch: ThreadedLineChannel, sample_index: int) -> tuple[dict[str, object], list[str]]:
    started = time.monotonic()
    for command in ("MASTER STATUS", "LIST", f"{NODE} PING", f"{NODE} BOOT CONFIRM STATUS"):
        ch.send(command)
    deadline = started + .95; lines: list[str] = []
    while time.monotonic() < deadline:
        line = ch.read(deadline)
        if line: lines.append(line)
    master_rows = [parse_fields(x) for x in lines if x.startswith("FUSION_MASTER_STATUS ")]
    list_rows = [parse_fields(x) for x in lines if x.startswith("FUSION_LIST ")]
    peers = [parse_fields(x) for x in lines if x.startswith("FUSION_PEER ")]
    pong = confirm = {}
    for line in lines:
        reply = parse_reply(line)
        if not reply: continue
        fields = parse_fields(reply.text)
        if reply.text.startswith("PONG "): pong = fields
        elif reply.text.startswith("BOOT CONFIRM STATUS "): confirm = fields
    failures = (["missing_master_status"] if not master_rows else [])
    failures += (["missing_list"] if not list_rows else [])
    failures += evaluate_inventory(master_rows[-1] if master_rows else {}, list_rows[-1] if list_rows else {}, peers, pong, confirm)
    uwb = [parse_fields(x) for x in lines if x.startswith("FUSION_UWB ") and parse_fields(x).get("name") == NODE]
    imu = [parse_fields(x) for x in lines if x.startswith("FUSION_IMU ") and parse_fields(x).get("name") == NODE]
    if not uwb or not imu: failures.append("stream_not_advancing")
    for row in uwb:
        if not all(len(row.get(key, "").split(",")) == 8 for key in
                   ("anchor_id", "rank", "range_mm", "t_round_us", "quality", "cfo_ppm_q8")):
            failures.append("uwb_not_eight_range")
    if any(x.startswith(("FUSION_DISCONNECTED ", "FUSION_CONNECTED ")) or "RESET" in x for x in lines):
        failures.append("disconnect_or_reset")
    valid_union=0
    for row in uwb: valid_union |= int(row.get("valid_mask",row.get("valid","0")),0)
    ended=time.monotonic()
    return {"sample": sample_index, "started_monotonic": started, "ended_monotonic":ended,
            "sample_duration_s":ended-started, "wall": wall(),
            "master": master_rows[-1] if master_rows else {}, "list": list_rows[-1] if list_rows else {},
            "peers": peers, "pong": pong, "confirm": confirm,
            "uwb_first": uwb[0] if uwb else {}, "uwb_last": uwb[-1] if uwb else {},
            "imu_first": imu[0] if imu else {}, "imu_last": imu[-1] if imu else {},
            "uwb_records": len(uwb), "uwb_valid_union":valid_union, "imu_records": len(imu),
            "imu_samples": sum(int(x.get("n", "0"), 0) for x in imu), "failures": sorted(set(failures))}, lines


def run_preflight(root: Path) -> dict[str, object]:
    out = root / "preflight"; out.mkdir(parents=True)
    anchor = anchor_preflight(out)
    listener_dir = out / "listener_capture"; lp, llog = start_listener(listener_dir, 150)
    ch = None; result: dict[str, object] = {"status": "IN_PROGRESS", "started_wall": wall(), "anchor": anchor}
    with (out / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as cdc:
        try:
            result["listener_lstat"] = wait_listener_preflight(listener_dir, lp, 30)
            ch = ThreadedLineChannel(resolve_fusion_port(None), cdc, "FUSION", decoded_queue_records=262144,
                backlog_red_records=32768, raw_backlog_red_bytes=32768, stall_red_s=2)
            ch.transport_mode = "binary"; ch.text_pending.clear(); result["decode_guard"] = decode_guard(ch, 15)
            open_boundary_health=ch.health_snapshot();result["fusion_open_boundary_health"]=open_boundary_health
            time.sleep(.5);result["stability_boundary"]=ch.discard_pending("preflight_stability_start")
            samples=[]; all_lines=[]; next_sample=time.monotonic()
            for index in range(1, 11):
                now=time.monotonic()
                if now < next_sample: time.sleep(next_sample-now)
                sample, lines=query_sample(ch,index); samples.append(sample); all_lines += lines; next_sample += 1.0
                peer_names=[x.get("name") for x in sample["peers"]]
                if any(name != NODE for name in peer_names):
                    result.update(status="PREFLIGHT_BLOCKED_UNEXPECTED_PEER", samples=samples)
                    raise RuntimeError(f"unexpected Fusion peer: {peer_names}")
                if sample["failures"]: raise RuntimeError(f"stability sample {index}: {sample['failures']}")
            first_u,last_u=samples[0]["uwb_first"],samples[-1]["uwb_last"]
            first_i,last_i=samples[0]["imu_first"],samples[-1]["imu_last"]
            uwb_count=sum(x["uwb_records"] for x in samples); imu_count=sum(x["imu_samples"] for x in samples)
            elapsed=sum(x["sample_duration_s"] for x in samples)
            rates={"uwb_hz":uwb_count/elapsed,"imu_hz":imu_count/elapsed}
            result.update(samples=samples,rates=rates)
            if not 7.8 <= rates["uwb_hz"] <= 8.8 or not 190 <= rates["imu_hz"] <= 210:
                raise RuntimeError(f"preflight rates non-nominal: {rates}")
            if first_u.get("sweep") == last_u.get("sweep") or first_i.get("seq") == last_i.get("seq"):
                raise RuntimeError("stale sequence evidence")
            if __import__("functools").reduce(int.__or__, (x["uwb_valid_union"] for x in samples), 0) != 0xff:
                raise RuntimeError("not all eight Anchor slots produced a valid range during stability gate")
            mapping={NODE:{"logical_tag_id":int(first_u["logical"],0),
                           "tag_short_address":f"0x{0xB100+int(first_u['logical'],0):04X}"}}
            deadline=time.monotonic()+20; coverage={}; errors={}
            while time.monotonic()<deadline:
                coverage,errors=listener_coverage(listener_dir,mapping,0)
                poll_seen=coverage[NODE]["poll_count"]
                if poll_seen: break
                time.sleep(.5)
            health=ch.health_snapshot()
            bad_health={k:health.get(k,0)-open_boundary_health.get(k,0) for k in ("decoded_queue_drops","log_queue_drops","raw_queue_drops",
                "frame_crc_decode_errors","payload_decode_errors","red_markers","reader_exceptions") if health.get(k,0)-open_boundary_health.get(k,0)}
            if errors or bad_health or not poll_seen: raise RuntimeError(f"preflight infrastructure errors listener={errors} host={bad_health} poll_seen={poll_seen}")
            result.update(status="PREFLIGHT_PASS", samples=samples, rates=rates, mapping=mapping,
                          listener_coverage=coverage, listener_errors=errors, host_health=health)
        except Exception as exc:
            if not str(result.get("status", "")).startswith("PREFLIGHT_BLOCKED_UNEXPECTED"):
                result["status"]="PREFLIGHT_BLOCKED"
            result["error"]=f"{type(exc).__name__}: {exc}"
        finally:
            if ch: ch.close()
            rc,summary=stop_listener(lp,llog); result["listener_rc"]=rc; result["listener_summary"]=summary
            result["ended_wall"]=wall(); atomic(root / "PREFLIGHT_RESULT.json", result)
    return result


def formal_capture(root: Path, preflight: dict[str, object], duration_s: float) -> dict[str, object]:
    out=root/"formal_capture"; out.mkdir(parents=True)
    lp,llog=start_listener(out/"listener_capture",duration_s+180)
    ch=None; stop=False
    def sig(_s,_f):
        nonlocal stop; stop=True
    signal.signal(signal.SIGINT,sig); signal.signal(signal.SIGTERM,sig)
    ledger={"status":"STARTING","started_wall":wall(),"events":[]}
    cdc=(out/"fusion_cdc.log").open("x",encoding="utf-8",buffering=1)
    raw=(out/"fusion_host_raw.cobs.bin").open("xb",buffering=0)
    try:
        ledger["listener_lstat"]=wait_listener_preflight(out/"listener_capture",lp,30)
        ch=ThreadedLineChannel(resolve_fusion_port(None),cdc,"FUSION",decoded_queue_records=1048576,
            backlog_red_records=131072,raw_backlog_red_bytes=131072,stall_red_s=2,raw_file=raw)
        ch.transport_mode="binary";ch.text_pending.clear();ledger["decode_guard"]=decode_guard(ch,15)
        mapping=preflight["mapping"]; coverage_deadline=time.monotonic()+20; coverage={}; errors={}
        while time.monotonic()<coverage_deadline:
            ch.read(min(coverage_deadline,time.monotonic()+.05))
            coverage,errors=listener_coverage(out/"listener_capture",mapping,0)
            if coverage[NODE]["poll_count"]:break
        if errors or not coverage or not coverage[NODE]["poll_count"]:
            raise RuntimeError(f"formal Listener readiness failed: {coverage} {errors}")
        boundary=ch.discard_pending("formal_t0"); baseline=ch.health_snapshot()
        t0=time.monotonic();t0_ns=time.monotonic_ns();t0_wall=wall();hard=t0+duration_s
        manifest={"schema":"biospur-v47-c2cc-stationary-heldout-v1","git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
          "node":NODE,"master":MASTER,"t0_wall":t0_wall,"t0_monotonic":t0,"t0_monotonic_ns":t0_ns,
          "planned_duration_s":duration_s,"pre_t0_boundary":boundary,"formal_health_baseline":baseline,
          "frozen_inputs":json.loads((root/"FROZEN_INPUT_HASHES.json").read_text()),"mapping":mapping,
          "commands_after_t0":[],"mutation":False,"listener_roles":{"poll_receivers":sorted(POLL_RECEIVERS),"LCG":"beacon subscriber","LHIGH":"beacon transmitter"}}
        atomic(out/"RUN_MANIFEST.json",manifest)
        print(f"T0 wall={t0_wall} monotonic={t0:.9f} — stationary capture active",flush=True)
        counts={"imu_records":0,"imu_samples":0,"uwb_records":0};last={"imu":t0,"uwb":t0};failure_onset=None
        initial={};final={};end_target=hard
        while not stop and time.monotonic()<end_target:
            now=time.monotonic();line=ch.read(min(end_target,now+.25));now=time.monotonic()
            if line:
                fields=parse_fields(line)
                if fields.get("name", NODE)==NODE and line.startswith(("FUSION_IMU ","FUSION_UWB ")):
                    pass
                elif line.startswith(("FUSION_IMU ","FUSION_UWB ")) and fields.get("name") not in (None,NODE):
                    ledger["events"].append({"type":"UNEXPECTED_PEER_STREAM","monotonic":now,"name":fields.get("name")})
                if fields.get("name")==NODE:
                    if line.startswith("FUSION_IMU "):
                        counts["imu_records"]+=1;counts["imu_samples"]+=int(fields.get("n","0"),0);last["imu"]=now
                        initial.setdefault("imu_seq",fields.get("seq"));final["imu_seq"]=fields.get("seq");final["imu_base_us"]=fields.get("base_us")
                    elif line.startswith("FUSION_UWB "):
                        counts["uwb_records"]+=1;last["uwb"]=now;initial.setdefault("uwb_sweep",fields.get("sweep"));final["uwb_sweep"]=fields.get("sweep");final["node_ms"]=fields.get("node_ms")
                if line.startswith(("FUSION_DISCONNECTED ","FUSION_CONNECTED ")) or "RESET" in line:
                    ledger["events"].append({"monotonic":now,"wall":wall(),"line":line})
            if failure_onset is None and now>t0+2 and (now-last["imu"]>2 or now-last["uwb"]>2):
                failure_onset=now;end_target=max(hard,now+120);ledger["events"].append({"type":"STREAM_FAILURE","monotonic":now,"last":last,"tail_until":end_target})
            health=ch.health_snapshot()
            if lp.poll() is not None or not ch._reader.is_alive() or health["reader_exceptions"]:
                ledger["events"].append({"type":"FATAL_INFRASTRUCTURE","monotonic":now,"health":health,"listener_rc":lp.poll()});break
        t1=time.monotonic();t1_wall=wall();reason="STOPPED_BY_OPERATOR" if stop else "PLANNED_DURATION_COMPLETE" if t1>=hard and failure_onset is None else "FAILURE_TAIL_COMPLETE" if failure_onset and t1>=end_target else "INFRASTRUCTURE_STOP"
        ledger.update(status="CAPTURE_COMPLETE",stop_reason=reason,t0_wall=t0_wall,t0_monotonic=t0,
                      t1_wall=t1_wall,t1_monotonic=t1,duration_s=t1-t0,counts=counts,initial=initial,final=final,
                      listener_pre_t0_coverage=coverage)
    except Exception as exc:
        ledger.update(status="CAPTURE_FAILED",stop_reason="INFRASTRUCTURE_STOP",error=f"{type(exc).__name__}: {exc}")
    finally:
        if ch:
            ledger["decoded_close_drain"]=ch.quiesce_reader_and_drain("planned_close")
            ch.close();ledger["fusion_health_final"]=ch.health_snapshot()
        rc,summary=stop_listener(lp,llog);ledger["listener_rc"]=rc;ledger["listener_summary"]=summary
        raw.close();cdc.close();ledger["raw_sha256"]=sha256(out/"fusion_host_raw.cobs.bin") if (out/"fusion_host_raw.cobs.bin").exists() else None
        ledger["finalized_wall"]=wall();atomic(out/"PROCESS_LEDGER.json",ledger)
    return ledger


def main() -> int:
    ap=argparse.ArgumentParser();ap.add_argument("--out-dir",type=Path,required=True);ap.add_argument("--duration-s",type=float,default=600.0)
    args=ap.parse_args();args.out_dir.mkdir(parents=True,exist_ok=False)
    frozen_copy=args.out_dir/"FROZEN_S2_PARAMETER_MANIFEST.json";shutil.copyfile(S2_MANIFEST,frozen_copy)
    status=subprocess.check_output(["git","status","--porcelain=v1","-z"],cwd=ROOT)
    frozen={"git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
      "source_status_sha256":hashlib.sha256(status).hexdigest(),"geometry":{"path":str(GEOMETRY.relative_to(ROOT)),"sha256":sha256(GEOMETRY)},
      "s2_parameter_manifest":{"source":str(S2_MANIFEST.relative_to(ROOT)),"copy":"FROZEN_S2_PARAMETER_MANIFEST.json","sha256":sha256(frozen_copy)},
      "s2_code":{"path":str(S2_CODE.relative_to(ROOT)),"sha256":sha256(S2_CODE)},"capture_tool_sha256":sha256(Path(__file__)),
      "storage_free_bytes":shutil.disk_usage(args.out_dir).free,"recorded_wall":wall(),"recorded_monotonic":time.monotonic()}
    atomic(args.out_dir/"FROZEN_INPUT_HASHES.json",frozen)
    preflight=run_preflight(args.out_dir)
    if preflight.get("status")!="PREFLIGHT_PASS":
        print(preflight["status"],flush=True);return 2
    print("PREFLIGHT_PASS — BSFC2CC ONLY — DO NOT MOVE OR TOUCH THE UNIT",flush=True)
    ledger=formal_capture(args.out_dir,preflight,args.duration_s)
    print(ledger.get("stop_reason"),flush=True)
    return 0 if ledger.get("status")=="CAPTURE_COMPLETE" else 2


if __name__=="__main__":raise SystemExit(main())
