#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Black-box checker for strict-target OTA runs.")
    p.add_argument("--run-dir", required=True, help="Directory containing summary.json from loop_test_ota_targeting.py")
    p.add_argument("--require-complete", action="store_true",
                   help="Require full OTA completion; default checks only early-stage robustness gates.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        print(f"FAIL: missing summary.json in {run_dir}")
        return 2

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    failures: list[str] = []

    if summary.get("wrong_target_trials", 0) != 0:
        failures.append("wrong_target_trials != 0")
    if summary.get("recv_bg_interference_trials", 0) != 0:
        failures.append("recv_bg_interference_trials != 0")
    if summary.get("target_match_count", 0) != summary.get("trial_count", 0):
        failures.append("strict UUID target match did not hold for all trials")

    trials = summary.get("trials", [])
    for t in trials:
        trial_no = t.get("trial", "?")
        if t.get("first_upload_tx_seen", False) and not t.get("first_upload_rsp_seen", False):
            failures.append(f"trial {trial_no}: first IMG_UPLOAD response missing")
        if t.get("first_upload_rsp_seen", False) and not t.get("upload_progressed", False):
            failures.append(f"trial {trial_no}: upload did not progress beyond early chunks")

    if args.require_complete and summary.get("successful_trials", 0) != summary.get("trial_count", 0):
        failures.append("not all trials completed OTA end-to-end")

    if failures:
        print("FAIL")
        for f in failures:
            print(f"- {f}")
        return 2

    print("PASS")
    print(f"- trial_count={summary.get('trial_count', 0)}")
    print(f"- wrong_target_trials={summary.get('wrong_target_trials', 0)}")
    print(f"- recv_bg_interference_trials={summary.get('recv_bg_interference_trials', 0)}")
    print(f"- target_match_count={summary.get('target_match_count', 0)}")
    print(f"- successful_trials={summary.get('successful_trials', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

