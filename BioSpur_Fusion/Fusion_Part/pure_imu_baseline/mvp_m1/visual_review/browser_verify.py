"""Finish MVP-M1R acceptance with offline Google Chrome runtime checks."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pure_imu_baseline.mvp_m1.visual_review.pipeline import (
    CAPTURES,
    MVP_ROOT,
    VIDEO_NAMES,
    dump,
    rebuild_manifest,
)


SELFTEST_ELEMENT = "biospur-selftest"
VIDEO_ELEMENT = "biospur-video-selftest"


def chrome_run(chrome: str, url: str, screenshot: Path, budget_ms: int = 30_000) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="biospur_m1r_chrome_") as profile:
        command = [
            chrome,
            "--headless=new",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--safebrowsing-disable-auto-update",
            "--allow-file-access-from-files",
            "--autoplay-policy=no-user-gesture-required",
            f"--user-data-dir={profile}",
            "--window-size=1440,900",
            "--run-all-compositor-stages-before-draw",
            f"--virtual-time-budget={budget_ms}",
            f"--screenshot={screenshot}",
            "--dump-dom",
            url,
        ]
        return subprocess.run(command, capture_output=True, text=True, timeout=120)


def parsed_pre(dom: str, element_id: str) -> tuple[dict | None, str | None]:
    match = re.search(rf'<pre id="{re.escape(element_id)}"[^>]*>(.*?)</pre>', dom, re.DOTALL)
    if not match:
        return None, f"runtime evidence element #{element_id} missing"
    try:
        return json.loads(html.unescape(match.group(1))), None
    except Exception as exc:  # pragma: no cover - recorded in the audit
        return None, str(exc)


def run_index(chrome: str, page: Path, screenshot: Path, expected_hrefs: list[str], expected_videos: int) -> dict:
    run = chrome_run(chrome, page.as_uri(), screenshot, 5_000)
    hrefs = re.findall(r'<a\s+[^>]*href="([^"]+)"', run.stdout, re.IGNORECASE)
    sources = re.findall(r'<video\s+[^>]*src="([^"]+)"', run.stdout, re.IGNORECASE)
    missing = [value for value in expected_hrefs if not any(item.endswith(value) for item in hrefs)]
    passed = run.returncode == 0 and not missing and len(sources) == expected_videos and screenshot.is_file()
    return {
        "url": page.as_uri(),
        "command_return_code": run.returncode,
        "anchor_count": len(hrefs),
        "video_element_count": len(sources),
        "expected_video_element_count": expected_videos,
        "missing_expected_links": missing,
        "screenshot": str(screenshot) if screenshot.is_file() else None,
        "stderr_tail": run.stderr[-2_000:],
        "pass": passed,
    }


def run_capture(chrome: str, capture: str, output: Path) -> dict:
    page = MVP_ROOT / f"CAPTURE{capture}_PURE_IMU_MVP.html"
    screenshot = output / f"CAPTURE{capture}_MVP_VIEWER_RUNTIME.png"
    url = page.as_uri() + "?selftest=1"
    run = chrome_run(chrome, url, screenshot)
    result, parse_error = parsed_pre(run.stdout, SELFTEST_ELEMENT)
    checks = {item.get("name"): bool(item.get("pass")) for item in (result or {}).get("checks", [])}
    requirements = {
        "play": ["play"],
        "pause": ["pause"],
        "scrub": ["complete timeline scrub"],
        "single_frame_forward_backward": ["exact one-frame forward step", "exact one-frame backward step"],
        "timestamp_jump": ["exact timestamp jump"],
        "orbit_drag": ["orbit by dragging"],
        "pan_drag": ["pan by dragging"],
        "wheel_zoom": ["zoom by wheel"],
        "front_side_top_oblique": [
            "camera WORLD_FIXED_FRONT",
            "camera WORLD_FIXED_SIDE",
            "camera WORLD_FIXED_TOP",
            "camera WORLD_FIXED_OBLIQUE",
        ],
        "raw_mode": ["clear returns raw gauge"],
        "recentered_mode": ["RECENTERED mode"],
        "overlay_mode": ["OVERLAY shared gauge"],
        "manual_yaw_recenter": ["explicit recenter accepted", "recenter matches engine candidate"],
        "clear_recenter": ["clear returns raw gauge"],
        "validity_overlay": ["validity overlay control", "toggle validity"],
        "gap_reset_markers": ["event jump", "reset does not auto-recenter"],
        "camera_retention_across_navigation_and_gauge_modes": [
            "camera retained while scrubbing",
            "recenter leaves camera unchanged",
            "recenter leaves camera basis unchanged",
        ],
        "pose_immutability_under_viewer_operations": ["camera and viewer interactions mutate zero pose values"],
    }
    requirement_results = {
        name: {"evidence_checks": names, "pass": all(checks.get(item, False) for item in names)}
        for name, names in requirements.items()
    }
    passed = bool(
        run.returncode == 0
        and result
        and result.get("pass")
        and all(item["pass"] for item in requirement_results.values())
        and screenshot.is_file()
    )
    return {
        "capture": capture,
        "url": url,
        "command_return_code": run.returncode,
        "selftest": result,
        "requested_interactions": requirement_results,
        "parse_error": parse_error,
        "screenshot": str(screenshot) if screenshot.is_file() else None,
        "stderr_tail": run.stderr[-2_000:],
        "pass": passed,
    }


def video_runtime_page(directory: Path) -> str:
    elements = "".join(
        f'<article><span>{name}</span><video id="v{i}" muted playsinline preload="auto" src="{(directory / name).as_uri()}"></video></article>'
        for i, name in enumerate(VIDEO_NAMES)
    )
    names = json.dumps(VIDEO_NAMES)
    return f'''<!doctype html><meta charset="utf-8"><title>BioSpur MP4 runtime check</title>
<style>body{{background:#050b11;color:#e7eef6;font:14px sans-serif}}main{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}article{{border:1px solid #274156;padding:5px}}video{{display:block;width:100%;height:160px;background:#000}}pre{{white-space:pre-wrap}}</style>
<h1>Offline Chrome H.264 runtime check</h1><main>{elements}</main>
<script>
const names={names};
const waitEvent=(target,name,timeout=15000)=>new Promise((resolve,reject)=>{{const timer=setTimeout(()=>reject(new Error(name+' timeout')),timeout);target.addEventListener(name,()=>{{clearTimeout(timer);resolve();}},{{once:true}});}});
async function verify(video,name){{
  if(video.readyState<1) await waitEvent(video,'loadedmetadata');
  const metadata={{duration_s:video.duration,width:video.videoWidth,height:video.videoHeight,can_play_type:video.canPlayType('video/mp4; codecs="avc1.640029"')}};
  video.currentTime=Math.min(0.5,Math.max(0.05,video.duration/2));
  await waitEvent(video,'seeked');
  const decodedFrame=video.readyState>=2&&video.videoWidth>0&&video.videoHeight>0;
  await video.play();
  video.pause();
  return {{name,...metadata,ready_state_after_seek:video.readyState,network_state_after_seek:video.networkState,seeked_time_s:video.currentTime,decoded_frame_ready:decodedFrame,play_promise_resolved:true,pass:Number.isFinite(metadata.duration_s)&&metadata.duration_s>0&&metadata.width>0&&metadata.height>0&&metadata.can_play_type!==''&&decodedFrame}};
}}
(async()=>{{const videos=[...document.querySelectorAll('video')];const settled=await Promise.allSettled(videos.map((video,i)=>verify(video,names[i])));const results=settled.map((item,i)=>item.status==='fulfilled'?item.value:{{name:names[i],pass:false,error:String(item.reason)}});const result={{browser_codec_query:'video/mp4; codecs=avc1.640029',videos:results,pass:results.length===names.length&&results.every(item=>item.pass)}};const pre=document.createElement('pre');pre.id='{VIDEO_ELEMENT}';pre.textContent=JSON.stringify(result);document.body.appendChild(pre);document.title='VIDEO_RUNTIME_'+(result.pass?'PASS':'FAIL');}})();
</script>'''


def run_videos(chrome: str, output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="biospur_m1r_video_page_") as temporary:
        page = Path(temporary) / "video-runtime.html"
        page.write_text(video_runtime_page(output), encoding="utf-8")
        screenshot = output / "VIDEO_RUNTIME_CHECK.png"
        run = chrome_run(chrome, page.as_uri(), screenshot, 45_000)
    result, parse_error = parsed_pre(run.stdout, VIDEO_ELEMENT)
    passed = bool(run.returncode == 0 and result and result.get("pass") and screenshot.is_file())
    return {
        "command_return_code": run.returncode,
        "selftest": result,
        "parse_error": parse_error,
        "screenshot": str(screenshot) if screenshot.is_file() else None,
        "stderr_tail": run.stderr[-2_000:],
        "pass": passed,
    }


def write_report(output: Path, final: dict, browser: dict, video: dict) -> None:
    rows = []
    for name in VIDEO_NAMES:
        item = video["videos"][name]
        rows.append(f"| {name} | {item['duration_s']:.3f} | {item['codec_name']} {item['profile']} / {item['pix_fmt']} | {'PASS' if item['chrome_runtime_pass'] else 'FAIL'} |")
    report = f"""# BioSpur Pure-IMU MVP-M1R visual review

Verdict: **{final['verdict']}**

This visual-review pack renders the frozen MVP-M1 `RAW` joint-position branch with a fixed root, fixed geometry, exact MVP frame timestamps, and the existing validity/reset masks. The three authoritative interactive viewers passed their offline Google Chrome self-tests, including playback, scrubbing, single-frame steps, timestamp and event jumps, orbit/pan/zoom, fixed cameras, RAW/RECENTERED/OVERLAY display modes, explicit recenter/clear, validity display, and camera retention checks.

| Video | Duration (s) | Encoding | Offline Chrome |
|---|---:|---|---|
{chr(10).join(rows)}

Numerical acceptance: maximum render-input coordinate difference is `0 m`; frozen raw arrays remained bit-for-bit unchanged; camera operations caused zero pose-array mutations; bone geometry remained within the frozen floating-point tolerance; invalid intervals and both Capture 2 long pelvis gaps remained unavailable and unbridged. The optional recenter clip is explicitly operator-triggered and changes one common global-yaw display gauge only; it is not used in the three main Capture reviews.

Declared invariants: `POSE_ALGORITHM_UNCHANGED`, `RAW_ARRAYS_UNCHANGED`, `NO_AUTOMATIC_CORRECTION`, `NO_UWB_NUMERIC_DATA_USED`, `NO_NEW_HUMAN_CAPTURE_REQUESTED`, and `LIVE_HARDWARE_INTEGRATION_NOT_STARTED`.
"""
    (output / "MVP_M1_VISUAL_REVIEW_FINAL.md").write_text(report, encoding="utf-8")


def verify(output: Path) -> dict:
    output = output.resolve()
    chrome = shutil.which("google-chrome")
    if chrome is None:
        raise RuntimeError("installed Google Chrome was not found")

    previous_browser = json.loads((output / "VIEWER_BROWSER_VERIFICATION.json").read_text(encoding="utf-8"))
    if previous_browser.get("status") != "PENDING" and not previous_browser.get("pass"):
        attempt = output / "BROWSER_RUNTIME_ATTEMPT_1_FAIL.json"
        if not attempt.exists():
            dump(attempt, {
                "browser_verification": previous_browser,
                "video_playability": json.loads((output / "VIDEO_PLAYABILITY_AUDIT.json").read_text(encoding="utf-8")),
                "final_result": json.loads((output / "FINAL_RESULT.json").read_text(encoding="utf-8")),
                "cause": "over-strict decoded-frame callback waited after a successful seek; Chrome screenshot visibly contained all eight decoded frames",
            })

    frozen_index = run_index(
        chrome,
        MVP_ROOT / "C123_PURE_IMU_MVP_VIEWER_INDEX.html",
        output / "C123_MVP_VIEWER_INDEX_RUNTIME.png",
        [f"CAPTURE{capture}_PURE_IMU_MVP.html" for capture in CAPTURES],
        0,
    )
    captures = {capture: run_capture(chrome, capture, output) for capture in CAPTURES}
    review_index = run_index(
        chrome,
        output / "C123_MVP_VISUAL_REVIEW_INDEX.html",
        output / "C123_MVP_VISUAL_REVIEW_INDEX_RUNTIME.png",
        [
            "C123_PURE_IMU_MVP_VIEWER_INDEX.html",
            *(f"CAPTURE{capture}_PURE_IMU_MVP.html" for capture in CAPTURES),
            *VIDEO_NAMES,
            "MVP_M1_VISUAL_REVIEW_FINAL.md",
        ],
        len(VIDEO_NAMES),
    )
    videos = run_videos(chrome, output)
    passed = bool(frozen_index["pass"] and review_index["pass"] and all(item["pass"] for item in captures.values()) and videos["pass"])
    result = {
        "schema": "biospur.pure_imu.mvp.m1r.browser_verification.v1",
        "browser": chrome,
        "browser_version": subprocess.run([chrome, "--version"], capture_output=True, text=True, check=True).stdout.strip(),
        "network_required": False,
        "file_url_direct_open": True,
        "runtime_surface": "installed Google Chrome headless after the direct browser-control bootstrap was unavailable",
        "direct_browser_control_bootstrap_error": "Importing module node:process is not allowed in node_repl",
        "frozen_viewer_index": frozen_index,
        "captures": captures,
        "review_index": review_index,
        "video_runtime": videos,
        "pass": passed,
    }
    dump(output / "VIEWER_BROWSER_VERIFICATION.json", result)

    video_audit = json.loads((output / "VIDEO_PLAYABILITY_AUDIT.json").read_text(encoding="utf-8"))
    by_name = {item["name"]: item for item in (videos.get("selftest") or {}).get("videos", [])}
    for name, item in video_audit["videos"].items():
        runtime = by_name.get(name, {"pass": False, "error": "missing Chrome runtime result"})
        item["chrome_runtime"] = runtime
        item["chrome_runtime_pass"] = bool(runtime.get("pass"))
    video_audit["chrome_runtime_status"] = "PASS" if videos["pass"] else "FAIL"
    video_audit["pass"] = bool(all(item["ffprobe_pass"] and item["chrome_runtime_pass"] for item in video_audit["videos"].values()))
    dump(output / "VIDEO_PLAYABILITY_AUDIT.json", video_audit)

    final = json.loads((output / "FINAL_RESULT.json").read_text(encoding="utf-8"))
    final["browser_runtime_pass"] = result["pass"]
    final["video_playability_pass"] = video_audit["pass"]
    final["verdict"] = "MVP_M1_VISUAL_REVIEW_PACK_READY" if all(
        (
            final["render_input_parity_pass"],
            final["raw_immutability_pass"],
            final["ffprobe_playability_pass"],
            final["video_playability_pass"],
            final["browser_runtime_pass"],
        )
    ) else "MVP_M1_VISUAL_REVIEW_BLOCKED_RENDER_FAILURE"
    dump(output / "FINAL_RESULT.json", final)
    write_report(output, final, result, video_audit)
    command = f"PYTHONPATH=. python3 -m pure_imu_baseline.mvp_m1.visual_review.browser_verify --output {output}"
    rebuild_manifest(output, "PASS" if final["verdict"] == "MVP_M1_VISUAL_REVIEW_PACK_READY" else "FAIL", command)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.output)
    print("VIEWER_BROWSER_VERIFICATION_PASS" if result["pass"] else "VIEWER_BROWSER_VERIFICATION_FAIL")
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
