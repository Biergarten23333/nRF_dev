"""Run the viewer's built-in interaction test in installed offline Chrome."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def _dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_capture(chrome: str, output: Path, capture: str) -> dict:
    page = output / f"CAPTURE{capture}_INTERACTIVE_3D.html"
    screenshot = output / f"CAPTURE{capture}_VIEWER_RUNTIME.png"
    url = page.as_uri() + "?selftest=1"
    with tempfile.TemporaryDirectory(prefix=f"biospur_stage2_chrome_c{capture}_") as profile:
        command = [
            chrome, "--headless=new", "--disable-background-networking",
            "--disable-component-update", "--disable-default-apps", "--disable-extensions",
            "--disable-sync", "--metrics-recording-only", "--no-first-run",
            "--safebrowsing-disable-auto-update", "--allow-file-access-from-files",
            f"--user-data-dir={profile}", "--window-size=1440,900",
            "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
            f"--screenshot={screenshot}", "--dump-dom", url,
        ]
        run = subprocess.run(command, capture_output=True, text=True, timeout=90)
    match = re.search(r'<pre id="biospur-selftest"[^>]*>(.*?)</pre>', run.stdout, re.DOTALL)
    parsed = None
    parse_error = None
    if match:
        try:
            parsed = json.loads(html.unescape(match.group(1)))
        except Exception as exc:  # pragma: no cover - evidence path
            parse_error = str(exc)
    else:
        parse_error = "self-test evidence element missing"
    return {
        "capture": capture,
        "command_return_code": run.returncode,
        "selftest": parsed,
        "parse_error": parse_error,
        "screenshot": str(screenshot) if screenshot.is_file() else None,
        "stderr_tail": run.stderr[-2000:],
        "pass": bool(run.returncode == 0 and parsed and parsed.get("pass") and screenshot.is_file()),
    }


def verify(output: Path) -> dict:
    chrome = shutil.which("google-chrome")
    if chrome is None:
        raise RuntimeError("installed Google Chrome was not found")
    captures = {capture: _run_capture(chrome, output, capture) for capture in ("1", "2", "3")}
    passed = all(item["pass"] for item in captures.values())
    result = {
        "schema": "biospur.pure_imu.stage2.browser_verification.v1",
        "browser": chrome,
        "network_required": False,
        "file_url_direct_open": True,
        "captures": captures,
        "pass": passed,
    }
    _dump(output / "VIEWER_BROWSER_VERIFICATION.json", result)
    final_path = output / "STAGE2_FINAL_RESULT.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["browser_runtime_verification"] = "PASS" if passed else "FAIL"
    final["verdict"] = ("PURE_IMU_STAGE2_INTERACTIVE_REVIEW_AND_DRIFT_DECOMPOSITION_COMPLETE"
                        if passed and final.get("parity_pass") and final.get("stage2_tests_pass")
                        else "PURE_IMU_STAGE2_INCOMPLETE")
    final["viewer_runtime_screenshots"] = {
        capture: value["screenshot"] for capture, value in captures.items()
    }
    _dump(final_path, final)
    trace_path = output / "REQUIREMENTS_TRACEABILITY.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    for requirement in trace["requirements"]:
        if requirement["status"] == "PENDING_BROWSER_RUNTIME":
            requirement["status"] = "PASS" if passed else "FAIL"
    _dump(trace_path, trace)
    report_path = output / "STAGE2_REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace(
        "Runtime browser verification is recorded separately in `VIEWER_BROWSER_VERIFICATION.json`.",
        f"Runtime browser verification is `{'PASS' if passed else 'FAIL'}` for all three capture pages and is recorded in `VIEWER_BROWSER_VERIFICATION.json`."
    )
    report_path.write_text(report, encoding="utf-8")
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
