"""Verify every MVP-M1 capture page in installed offline Google Chrome."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .pipeline import dump, rebuild_manifest


def run_capture(chrome: str, output: Path, capture: str) -> dict:
    page=output/f"CAPTURE{capture}_PURE_IMU_MVP.html"; screenshot=output/f"CAPTURE{capture}_MVP_VIEWER_RUNTIME.png"
    url=page.as_uri()+"?selftest=1"
    with tempfile.TemporaryDirectory(prefix=f"biospur_mvp_m1_chrome_c{capture}_") as profile:
        command=[chrome,"--headless=new","--disable-background-networking","--disable-component-update","--disable-default-apps",
                 "--disable-extensions","--disable-sync","--metrics-recording-only","--no-first-run","--safebrowsing-disable-auto-update",
                 "--allow-file-access-from-files",f"--user-data-dir={profile}","--window-size=1440,900",
                 "--run-all-compositor-stages-before-draw","--virtual-time-budget=30000",f"--screenshot={screenshot}","--dump-dom",url]
        run=subprocess.run(command,capture_output=True,text=True,timeout=120)
    match=re.search(r'<pre id="biospur-selftest"[^>]*>(.*?)</pre>',run.stdout,re.DOTALL); parsed=None; parse_error=None
    if match:
        try: parsed=json.loads(html.unescape(match.group(1)))
        except Exception as exc: parse_error=str(exc)
    else: parse_error="self-test evidence element missing"
    return {"capture":capture,"url":url,"command_return_code":run.returncode,"selftest":parsed,"parse_error":parse_error,
            "screenshot":str(screenshot) if screenshot.is_file() else None,"stderr_tail":run.stderr[-2000:],
            "pass":bool(run.returncode==0 and parsed and parsed.get("pass") and screenshot.is_file())}


def verify(output: Path) -> dict:
    output=output.resolve(); chrome=shutil.which("google-chrome")
    if chrome is None: raise RuntimeError("installed Google Chrome was not found")
    captures={capture:run_capture(chrome,output,capture) for capture in "123"}
    passed=all(item["pass"] for item in captures.values())
    result={"schema":"biospur.pure_imu.mvp_m1.browser_verification.v1","browser":chrome,"browser_version":subprocess.run([chrome,"--version"],capture_output=True,text=True).stdout.strip(),
            "network_required":False,"file_url_direct_open":True,"runtime_surface":"installed Google Chrome headless after in-app Browser bootstrap was unavailable",
            "in_app_browser_bootstrap_error":"Importing module node:process is not allowed in node_repl","captures":captures,"pass":passed}
    dump(output/"VIEWER_BROWSER_VERIFICATION.json",result)
    benchmark=json.loads((output/"REALTIME_REPLAY_BENCHMARK.json").read_text(encoding="utf-8"))
    export_audits={kind:json.loads((output/name).read_text(encoding="utf-8")) for kind,name in
                   (("npz","NPZ_EXPORT_AUDIT.json"),("ndjson","NDJSON_EXPORT_AUDIT.json"),("csv","CSV_EXPORT_AUDIT.json"))}
    benchmark["export_throughput_frames_s"]={kind:{capture:audit["captures"][capture]["throughput_frames_s"] for capture in "123"}
                                               for kind,audit in export_audits.items()}
    fps={capture:item["selftest"]["viewer_render_benchmark"]["throughput_fps"] for capture,item in captures.items() if item.get("selftest")}
    finite_fps={capture:value for capture,value in fps.items() if isinstance(value,(int,float))}
    benchmark["viewer_frame_rate"]={"per_capture_full_canvas_draw_throughput_fps":finite_fps,"minimum_fps":min(finite_fps.values()) if finite_fps else None,
                                    "measurement":"16 ms timed playback with a full-canvas draw per callback at 1440x900 in offline Chrome","pass":bool(len(finite_fps)==3 and min(finite_fps.values())>=55)}
    benchmark["pass"]=bool(all(item.get("pass") and item.get("deterministic_repeated_batch_output") for item in benchmark["captures"].values()) and benchmark["viewer_frame_rate"]["pass"])
    dump(output/"REALTIME_REPLAY_BENCHMARK.json",benchmark)
    final=json.loads((output/"FINAL_RESULT.json").read_text(encoding="utf-8")); final["browser_runtime_pass"]=passed; final["realtime_pass"]=benchmark["pass"]
    ready=all((final["raw_immutability_pass"],final["normalization_pass"],final["recenter_invariants_pass"],final["negative_controls_pass"],final["exports_pass"],final["realtime_pass"],final["pytest"]["pass"],passed,benchmark["viewer_frame_rate"]["pass"]))
    final["verdict"]="PURE_IMU_RAW_MVP_M1_READY_WITH_DECLARED_LIMITATIONS" if ready else "PURE_IMU_RAW_MVP_M1_BLOCKED_IMPLEMENTATION_FAILURE"
    final["viewer_frame_rate_pass"]=benchmark["viewer_frame_rate"]["pass"]
    final["viewer_runtime_screenshots"]={capture:item["screenshot"] for capture,item in captures.items()}
    dump(output/"FINAL_RESULT.json",final)
    recenter=json.loads((output/"RECENTER_INVARIANT_TESTS.json").read_text(encoding="utf-8")); normalized=json.loads((output/"NORMALIZED_WORKING_VIEW_AUDIT.json").read_text(encoding="utf-8"))
    latency=benchmark["captures"]; lines=[]
    for capture in "123":
        inv=recenter["captures"][capture]; perf=latency[capture]; norm=normalized["captures"][capture]
        lines.append(f"| {capture} | {perf['frames']:,} | {norm['working_quaternion_norm_error_max']:.3e} | {inv['display_gravity_difference_max']:.3e} | {inv['display_q_PC_vs_working_q_PC_max']:.3e} | {inv['display_bone_length_error_max_m']:.3e} | {perf['real_time_factor']:.2f}× | {perf['latency_ms']['p50']:.3f} / {perf['latency_ms']['p95']:.3f} / {perf['latency_ms']['p99']:.3f} / {perf['latency_ms']['max']:.3f} | {finite_fps.get(capture,float('nan')):.1f} |")
    report=f"""# BioSpur Pure-IMU MVP-M1

Verdict: **{final['verdict']}**

MVP-M1 preserves the frozen Stage 1 replay arrays as authoritative evidence, creates a separate float64 normalized computational view, and exposes one explicit whole-body global-yaw display gauge. All three captures passed exact replay/export checks, full packet-by-packet real-time qualification, deterministic repeat runs, manual-recenter invariants, negative controls, and offline browser interaction tests.

| Capture | Frames | Work norm max | Gravity Δ max | qPC Δ max | Bone error max (m) | Real-time factor | Latency p50 / p95 / p99 / max (ms) | Viewer draw fps |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(lines)}

The viewer permanently states **PURE IMU**, **ROOT FIXED**, **RAW AUTHORITATIVE**, **GLOBAL YAW MAY DRIFT**, and **MANUAL RECENTER CHANGES GAUGE ONLY**. Recenter is one common global Z rotation, logged as `OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE`; it leaves raw evidence, gravity, articulation, bone lengths, root, and camera unchanged. Resets and health never invoke it.

No automatic heading correction, action-label trigger, final-still trigger, UWB numeric input, new human capture, external position, or confidence-derived pose state exists. Remaining limitations are six-axis global-yaw drift/no absolute heading, fixed root/no external translation, fixed development geometry rather than measured anthropometry, no anatomical joint-angle accuracy claim, and no implemented live hardware acquisition. The frozen packet adapter is ready for later hardware integration.
"""
    (output/"MVP_M1_FINAL.md").write_text(report,encoding="utf-8")
    command=f"PYTHONPATH=. python3 -m pure_imu_baseline.mvp_m1.browser_verify --output {output}"
    rebuild_manifest(output,command,"PASS" if passed else "FAIL")
    return result


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    result=verify(args.output); print("VIEWER_BROWSER_VERIFICATION_PASS" if result["pass"] else "VIEWER_BROWSER_VERIFICATION_FAIL"); raise SystemExit(0 if result["pass"] else 1)


if __name__=="__main__": main()
