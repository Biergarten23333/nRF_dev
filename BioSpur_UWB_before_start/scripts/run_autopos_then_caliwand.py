#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ANCHOR_PORT = (
    "/dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00"
)
DEFAULT_TAG_PORT = (
    "/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00"
)
DEFAULT_WAND_TARGETS = "BSCCF4,BS9336,BS955A"


def normalize_bs(value: str) -> str:
    name = value.strip().upper()
    if not name.startswith("BS") or len(name) != 6:
        raise argparse.ArgumentTypeError(f"invalid BS name: {value!r}")
    int(name[2:], 16)
    return name


def parse_targets(value: str) -> list[str]:
    targets = [normalize_bs(item) for item in value.replace(" ", ",").split(",") if item.strip()]
    if not targets:
        raise argparse.ArgumentTypeError("at least one BS target is required")
    if len(set(targets)) != len(targets):
        raise argparse.ArgumentTypeError("targets must be unique")
    return targets


def run_step(name: str, cmd: list[str], *, dry_run: bool = False) -> dict:
    print(f"\n=== {name} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    started = time.time()
    if dry_run:
        return {
            "name": name,
            "cmd": cmd,
            "returncode": 0,
            "elapsed_s": 0.0,
            "dry_run": True,
        }
    result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    elapsed = time.time() - started
    return {
        "name": name,
        "cmd": cmd,
        "returncode": int(result.returncode),
        "elapsed_s": round(elapsed, 3),
        "dry_run": False,
    }


def find_latest_caliwand_summary(caliwand_root: Path) -> Path | None:
    candidates = sorted(caliwand_root.glob("**/caliwand_summary.json"))
    if candidates:
        return candidates[-1]
    candidates = sorted(caliwand_root.glob("**/summary.json"))
    if candidates:
        return candidates[-1]
    return None


def load_json_if_exists(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run the practical AutoPos -> APOS push/verify -> CaliWand capture chain. "
            "This wrapper leaves the existing sweep/solve/capture scripts unchanged."
        )
    )
    p.add_argument("--anchor-port", default=os.environ.get("BIOSPUR_ANCHOR_PORT", DEFAULT_ANCHOR_PORT))
    p.add_argument("--tag-port", default=os.environ.get("BIOSPUR_TAG_PORT", DEFAULT_TAG_PORT))
    p.add_argument("--anchor-snr", default=os.environ.get("BIOSPUR_ANCHOR_SNR", "960148546"))
    p.add_argument("--tag-snr", default=os.environ.get("BIOSPUR_TAG_SNR", "1050070698"))
    p.add_argument("--order", default="ABCDEFGH")
    p.add_argument("--sw-sets", type=int, default=100)
    p.add_argument("--timeout-s", type=int, default=3600)
    p.add_argument("--warmup-min-quality", type=int, default=0)
    p.add_argument("--wand-targets", type=parse_targets, default=parse_targets(DEFAULT_WAND_TARGETS))
    p.add_argument(
        "--push-targets",
        type=parse_targets,
        default=None,
        help="Tags that receive APOS layout. Default: same as --wand-targets.",
    )
    p.add_argument("--caliwand-duration", type=float, default=120.0)
    p.add_argument("--out-dir", default="logs/autopos_then_caliwand")
    p.add_argument("--layout-json", help="Skip solve output selection and push this layout JSON.")
    p.add_argument("--skip-autopos", action="store_true", help="Reuse --layout-json or an existing out-dir layout.")
    p.add_argument("--skip-push", action="store_true")
    p.add_argument("--skip-caliwand", action="store_true")
    p.add_argument("--with-listener", action="store_true")
    p.add_argument("--listener-port", default=os.environ.get("BIOSPUR_LISTENER_PORT"))
    p.add_argument("--skip-anchor-preflight", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = Path(f"{args.out_dir}_{stamp}")
    autopos_dir = base / "01_autopos"
    push_dir = base / "02_push_apos"
    caliwand_dir = base / "03_caliwand"
    base.mkdir(parents=True, exist_ok=True)

    wand_targets = args.wand_targets
    push_targets = args.push_targets or wand_targets
    steps: list[dict] = []

    layout_json = Path(args.layout_json) if args.layout_json else autopos_dir / "solve_v3_box" / "anchor_layout_v3_box.json"

    if not args.skip_autopos:
        cmd = [
            sys.executable,
            "scripts/run_autopos_sweep_and_solve_v3_box.py",
            "--port",
            args.anchor_port,
            "--order",
            args.order,
            "--sw-sets",
            str(args.sw_sets),
            "--timeout-s",
            str(args.timeout_s),
            "--warmup-min-quality",
            str(args.warmup_min_quality),
            "--quiet-tag-name",
            "-",
            "--no-bootstrap-autopos-reset",
            "--floating-reference-z-prior-mm",
            "820",
            "--floating-reference-z-sigma-mm",
            "80",
            "--out-dir",
            str(autopos_dir),
        ]
        step = run_step("01_autopos_sweep_solve", cmd, dry_run=args.dry_run)
        steps.append(step)
        if step["returncode"] != 0:
            print("[error] AutoPos sweep/solve failed; stopping before push/capture.", flush=True)
            return step["returncode"]

    if not args.dry_run and not layout_json.exists():
        print(f"[error] layout JSON not found: {layout_json}", flush=True)
        return 2

    if not args.skip_push:
        cmd = [
            sys.executable,
            "scripts/gui_push_apos_verified.py",
            "--port",
            args.tag_port,
            "--layout-input",
            str(layout_json),
            "--targets",
            ",".join(push_targets),
            "--out-dir",
            str(push_dir),
        ]
        step = run_step("02_push_apos_verify", cmd, dry_run=args.dry_run)
        steps.append(step)
        if step["returncode"] != 0:
            print("[error] APOS push/verify failed; stopping before CaliWand capture.", flush=True)
            return step["returncode"]

    if not args.skip_caliwand:
        cmd = [
            sys.executable,
            "scripts/run_caliwand_capture.py",
            "--targets",
            ",".join(wand_targets),
            "--duration",
            str(args.caliwand_duration),
            "--tag-port",
            args.tag_port,
            "--tag-snr",
            args.tag_snr,
            "--anchor-port",
            args.anchor_port,
            "--anchor-snr",
            args.anchor_snr,
            "--out-dir",
            str(caliwand_dir / "capture"),
        ]
        if args.with_listener:
            cmd.append("--with-listener")
            if args.listener_port:
                cmd.extend(["--listener-port", args.listener_port])
        if args.skip_anchor_preflight:
            cmd.append("--skip-anchor-preflight")
        step = run_step("03_caliwand_capture", cmd, dry_run=args.dry_run)
        steps.append(step)
        if step["returncode"] != 0:
            print("[error] CaliWand capture failed.", flush=True)
            return step["returncode"]

    autopos_manifest = autopos_dir / "pipeline_manifest.json"
    push_summary = push_dir / "summary.json"
    caliwand_summary_path = find_latest_caliwand_summary(caliwand_dir)
    summary = {
        "pipeline": "autopos_sweep_solve -> apos_push_verify -> caliwand_capture",
        "stamp": stamp,
        "base_dir": str(base.resolve()),
        "anchor_port": args.anchor_port,
        "tag_port": args.tag_port,
        "anchor_snr": args.anchor_snr,
        "tag_snr": args.tag_snr,
        "order": args.order,
        "sw_sets": args.sw_sets,
        "wand_targets": wand_targets,
        "push_targets": push_targets,
        "layout_json": str(layout_json.resolve()) if layout_json.exists() else str(layout_json),
        "autopos_manifest": str(autopos_manifest.resolve()) if autopos_manifest.exists() else None,
        "push_summary": str(push_summary.resolve()) if push_summary.exists() else None,
        "caliwand_summary": (
            str(caliwand_summary_path.resolve()) if caliwand_summary_path is not None else None
        ),
        "steps": steps,
        "push": load_json_if_exists(push_summary),
        "caliwand": load_json_if_exists(caliwand_summary_path),
    }
    summary_path = base / "pipeline_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\n[ok] pipeline summary: {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
