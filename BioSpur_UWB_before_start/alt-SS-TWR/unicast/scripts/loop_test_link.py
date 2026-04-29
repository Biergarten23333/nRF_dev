#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import serial

from anchor_probe_guard import (
    assert_no_forbidden_master_flash_cmd,
    validate_anchor_probe_snr,
    validate_anchor_serial_port,
)

ANCHORS = tuple("ABCDEFGH")
MATRIX_RE = (
    r"Matrix (?P<m>[A-H])-(?P<p>[A-H]) addr=0x(?P<addr>[0-9a-fA-F]+) "
    r"raw=(?P<raw>-?\d+) mm filt=(?P<filt>-?\d+) mm ok=(?P<ok>\d+) fail=(?P<fail>\d+) q=(?P<q>\d+)%"
)


@dataclass
class AnchorEndpoint:
    anchor: str
    probe_serial: str | None
    serial_port: str | None


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def preflight_kill_hotplug_scanners() -> None:
    subprocess.run(
        ["pkill", "-f", "nrfutil-device --json list --hotplug"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["pkill", "-f", "nrfutil-device"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.15)


def sn_pinned_reset(snr: str) -> None:
    validate_anchor_probe_snr(snr, "sn_pinned_reset")
    preflight_kill_hotplug_scanners()
    jlink = subprocess.run(
        ["bash", "-lc", "command -v JLinkExe"],
        text=True,
        capture_output=True,
        check=False,
    )
    jlink_path = (jlink.stdout or "").strip()
    if not jlink_path:
        raise RuntimeError("JLinkExe not found for SN-pinned reset")

    with tempfile.NamedTemporaryFile("w", suffix=".jlink", delete=False) as f:
        cmd_file = f.name
        f.write("Device nRF52832_XXAA\n")
        f.write("SelectInterface SWD\n")
        f.write("Speed 4000\n")
        f.write("Connect\n")
        f.write("Reset\n")
        f.write("Go\n")
        f.write("Exit\n")
    try:
        run_cmd([jlink_path, "-NoGui", "1", "-SelectEmuBySN", snr, "-CommanderScript", cmd_file], check=True)
    finally:
        try:
            os.unlink(cmd_file)
        except OSError:
            pass


def parse_anchor_letter(value: str) -> str:
    v = value.strip().upper()
    if v not in ANCHORS:
        raise argparse.ArgumentTypeError("anchor must be A..H")
    return v


def parse_param_specs(specs: list[str]) -> list[tuple[str, list[str]]]:
    dims: list[tuple[str, list[str]]] = []
    for item in specs:
        if "=" not in item:
            raise ValueError(f"invalid --params item (missing '='): {item}")
        key, values = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid --params key in: {item}")
        parsed = [v.strip() for v in values.split(",") if v.strip() != ""]
        if not parsed:
            raise ValueError(f"invalid --params values in: {item}")
        dims.append((key, parsed))
    return dims


def build_param_grid(dims: list[tuple[str, list[str]]]) -> list[dict[str, str]]:
    if not dims:
        return [{}]
    keys = [d[0] for d in dims]
    values = [d[1] for d in dims]
    out: list[dict[str, str]] = []
    for combo in itertools.product(*values):
        out.append({k: v for k, v in zip(keys, combo)})
    return out


def load_probe_map(path: Path) -> dict[str, AnchorEndpoint]:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError(f"empty probe-map CSV: {path}")

    def pick(row: dict[str, str], keys: list[str]) -> str | None:
        for k in keys:
            v = row.get(k)
            if v and v.strip():
                return v.strip()
        return None

    mapping: dict[str, AnchorEndpoint] = {}
    for row in rows:
        anchor = pick(row, ["anchor", "anchor_id", "id", "name", "Anchor"])
        if not anchor:
            continue
        a = anchor.strip().upper()
        if a not in ANCHORS:
            continue
        probe = pick(row, ["probe_serial", "probe", "snr", "jlink_serial"])
        port = pick(row, ["serial_port", "port", "tty", "console_port"])
        if probe:
            probe = validate_anchor_probe_snr(probe, f"probe-map anchor={a}")
        if port:
            port = validate_anchor_serial_port(port, f"probe-map anchor={a}")
        mapping[a] = AnchorEndpoint(anchor=a, probe_serial=probe, serial_port=port)
    missing = [a for a in ANCHORS if a not in mapping]
    if missing:
        raise RuntimeError(f"probe-map missing anchors: {','.join(missing)}")
    return mapping


def normalize_param_key(params: dict[str, str], sweeps_per_round: int) -> str:
    parts = [f"sweeps={sweeps_per_round}"]
    for k in sorted(params):
        parts.append(f"{k}={params[k]}")
    return "__".join(parts).replace("/", "_")


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    f = parse_float(value)
    return None if f is None else int(round(f))


def ci95_mm(pstdev: float | None, count: int) -> float | None:
    if pstdev is None or count <= 0:
        return None
    return 1.96 * pstdev / math.sqrt(count)


def quant_metrics(values: list[float], qualities: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {
            "count": 0,
            "mean_mm": None,
            "median_mm": None,
            "pstdev_mm": None,
            "ci95_mm": None,
            "quality_median": None,
        }
    pstdev = statistics.pstdev(values) if n >= 2 else 0.0
    return {
        "count": n,
        "mean_mm": statistics.mean(values),
        "median_mm": statistics.median(values),
        "pstdev_mm": pstdev,
        "ci95_mm": ci95_mm(pstdev, n),
        "quality_median": statistics.median(qualities) if qualities else None,
    }


def parse_raw_to_pairs_csv(raw_log: Path, out_csv: Path, sweep_limit: int | None) -> dict[str, Any]:
    import re

    matrix_re = re.compile(MATRIX_RE)
    sweep_complete_re = re.compile(r"Anchor sweep\s+(?P<sweep>\d+)\s+complete\s+for\s+(?P<master>[A-H])")

    rows: list[dict[str, Any]] = []
    sweep_done = 0
    with raw_log.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            sweep_match = sweep_complete_re.search(line)
            if sweep_match:
                sweep_done += 1
                continue
            if sweep_limit is not None and sweep_done >= sweep_limit:
                continue
            m = matrix_re.search(line)
            if not m:
                continue
            a, b = m.group("m"), m.group("p")
            aa, bb = sorted([a, b])
            rows.append(
                {
                    "a": aa,
                    "b": bb,
                    "master": a,
                    "raw_mm": int(m.group("raw")),
                    "filt_mm": int(m.group("filt")),
                    "quality_percent": int(m.group("q")),
                    "ok": int(m.group("ok")),
                    "fail": int(m.group("fail")),
                    "addr_hex": m.group("addr"),
                }
            )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["a", "b", "master", "raw_mm", "filt_mm", "quality_percent", "ok", "fail", "addr_hex"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return {"row_count": len(rows), "sweeps_seen": sweep_done}


def pick_field(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    for n in names:
        if n in row and str(row[n]).strip() != "":
            return row[n]
    return None


def extract_link_samples(
    pairs_csv: Path,
    master: str,
    peer: str,
    quality_min: int,
) -> tuple[list[float], list[float], dict[str, int]]:
    values: list[float] = []
    qualities: list[float] = []
    counters = {"rows_total": 0, "rows_link": 0, "rows_valid": 0, "rows_invalid": 0}
    with pairs_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            counters["rows_total"] += 1
            a = (pick_field(row, ("a", "anchor_a", "from")) or "").strip().upper()
            b = (pick_field(row, ("b", "anchor_b", "to")) or "").strip().upper()
            row_master = (pick_field(row, ("master", "initiator")) or "").strip().upper()
            if {a, b} != {master, peer}:
                continue
            if row_master and row_master != master:
                continue
            counters["rows_link"] += 1

            filt = parse_float(pick_field(row, ("filt_mm", "filt", "dist_mm", "dist", "distance_mm")))
            q = parse_int(pick_field(row, ("quality_percent", "q", "quality")))
            ok = parse_int(pick_field(row, ("ok",)))
            if filt is None:
                counters["rows_invalid"] += 1
                continue
            if ok is not None and ok <= 0:
                counters["rows_invalid"] += 1
                continue
            if q is not None and q < quality_min:
                counters["rows_invalid"] += 1
                continue
            values.append(filt)
            qualities.append(float(q) if q is not None else 100.0)
            counters["rows_valid"] += 1
    return values, qualities, counters


def scoring(agg: dict[str, Any], min_samples: int) -> float:
    count = int(agg.get("count", 0) or 0)
    pstdev = agg.get("pstdev_mm")
    ci = agg.get("ci95_mm")
    insufficient_penalty = 0.0
    if count < min_samples:
        insufficient_penalty += 1_000_000.0 + (min_samples - count) * 10_000.0
    if pstdev is None:
        insufficient_penalty += 1_000_000.0
        pstdev_val = 99999.0
    else:
        pstdev_val = float(pstdev)
    if ci is None:
        insufficient_penalty += 100_000.0
        ci_val = 9999.0
    else:
        ci_val = float(ci)
    # lower is better: pstdev primary, ci secondary, count tertiary
    return insufficient_penalty + pstdev_val * 1000.0 + ci_val * 100.0 - min(count, 5000) * 0.1


def apply_params_if_needed(args: argparse.Namespace, params: dict[str, str], combo_dir: Path) -> None:
    if not args.apply_params_cmd:
        return
    kv = " ".join(f"{k}={v}" for k, v in sorted(params.items()))
    cmd = args.apply_params_cmd.format(
        master=args.master,
        peer=args.peer,
        out_dir=str(combo_dir),
        params_json=json.dumps(params, sort_keys=True),
        params_kv=kv,
    )
    run_cmd(["bash", "-lc", cmd], check=True)


def set_roles(
    args: argparse.Namespace,
    mapping: dict[str, AnchorEndpoint],
    master: str,
    round_dir: Path,
) -> None:
    if args.skip_flash:
        return
    preflight_kill_hotplug_scanners()
    role_log = round_dir / "role_switch.log"
    with role_log.open("w", encoding="utf-8") as log:
        for anchor in ANCHORS:
            role = "master" if anchor == master else "matrix"
            ep = mapping[anchor]
            if args.method == "provision":
                if not ep.probe_serial:
                    raise RuntimeError(f"missing probe_serial for anchor {anchor} in probe-map")
                validate_anchor_probe_snr(ep.probe_serial, f"set_roles anchor={anchor}")
                cmd = [
                    "python3",
                    "scripts/provision_anchor.py",
                    "--probe-serial",
                    ep.probe_serial,
                    "--anchor-id",
                    anchor,
                    "--role",
                    role,
                    "--verify",
                ]
            else:
                if not ep.serial_port:
                    raise RuntimeError(f"missing serial_port for anchor {anchor} in probe-map")
                validate_anchor_serial_port(ep.serial_port, f"set_roles anchor={anchor}")
                cmd = [
                    "python3",
                    "scripts/serial_switch_role.py",
                    "--port",
                    ep.serial_port,
                    "--role",
                    role,
                    "--anchor-id",
                    anchor,
                    "--save",
                    "--reboot",
                    "--boot-window-reboot",
                    "--timeout",
                    str(args.serial_timeout),
                ]
            assert_no_forbidden_master_flash_cmd(cmd, f"set_roles anchor={anchor}")
            proc = run_cmd(cmd, check=True)
            log.write(f"$ {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}\n")


def capture_master_round(
    args: argparse.Namespace,
    mapping: dict[str, AnchorEndpoint],
    round_dir: Path,
    sweeps_per_round: int,
) -> tuple[Path, dict[str, Any]]:
    raw_path = round_dir / "raw.log"
    capture_meta = {"status": "ok", "timeout": False, "sweeps_completed": 0}
    master_ep = mapping[args.master]
    if not master_ep.serial_port:
        raise RuntimeError(f"missing serial_port for master {args.master} in probe-map")
    validate_anchor_serial_port(master_ep.serial_port, f"capture master={args.master}")
    if master_ep.probe_serial:
        validate_anchor_probe_snr(master_ep.probe_serial, f"capture master={args.master}")

    if not args.no_reset and master_ep.probe_serial:
        sn_pinned_reset(master_ep.probe_serial)
    time.sleep(args.settle_s)

    import re

    sweep_re = re.compile(rf"Anchor sweep\s+\d+\s+complete\s+for\s+{args.master}\b")
    start = time.time()
    timeout_at = start + args.timeout
    sweeps = 0
    with raw_path.open("w", encoding="utf-8") as raw:
        try:
            with serial.Serial(master_ep.serial_port, args.baud, timeout=0.2) as ser:
                while time.time() < timeout_at:
                    line = ser.readline()
                    if not line:
                        continue
                    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    raw.write(text + "\n")
                    if args.verbose:
                        print(text)
                    if sweep_re.search(text):
                        sweeps += 1
                        if sweeps >= sweeps_per_round:
                            break
        except serial.SerialException as exc:
            capture_meta["status"] = f"serial_error:{exc}"
    capture_meta["sweeps_completed"] = sweeps
    if sweeps < sweeps_per_round:
        capture_meta["timeout"] = True
    return raw_path, capture_meta


def verdict_from_metrics(metrics: dict[str, Any], args: argparse.Namespace) -> str:
    count = int(metrics.get("count", 0) or 0)
    pstdev = parse_float(metrics.get("pstdev_mm"))
    qmed = parse_float(metrics.get("quality_median"))
    ci = parse_float(metrics.get("ci95_mm"))
    if count >= args.min_samples and (pstdev is not None and pstdev <= args.pass_pstdev_mm) and (
        qmed is not None and qmed >= args.pass_quality_median
    ):
        if ci is not None and ci > args.warn_ci95_mm:
            return "WARN"
        return "PASS"
    return "FAIL"


def aggregate_rounds(round_results: list[dict[str, Any]]) -> dict[str, Any]:
    all_values: list[float] = []
    all_q: list[float] = []
    insufficient = 0
    for r in round_results:
        all_values.extend(r.get("samples_mm", []))
        all_q.extend(r.get("qualities", []))
        if not r.get("sufficient", False):
            insufficient += 1
    agg = quant_metrics(all_values, all_q)
    agg["insufficient_rounds"] = insufficient
    agg["rounds"] = len(round_results)
    return agg


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def try_make_plots(out_dir: Path, ranked: list[dict[str, Any]]) -> dict[str, str]:
    if not ranked:
        return {}
    try:
        os.environ.setdefault("MPLBACKEND", "Agg")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return {}

    labels = [r["param_key"] for r in ranked]
    pstdev_vals = [parse_float(r["aggregate"]["pstdev_mm"]) or float("nan") for r in ranked]
    ci_vals = [parse_float(r["aggregate"]["ci95_mm"]) or float("nan") for r in ranked]

    fig, ax = plt.subplots(figsize=(max(10, len(labels) * 0.7), 5))
    x = range(len(labels))
    ax.plot(x, pstdev_vals, marker="o", label="pstdev_mm")
    ax.plot(x, ci_vals, marker="x", label="ci95_mm")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("mm")
    ax.set_title("Loop Test Link Stability Ranking")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    rank_plot = out_dir / "ranking_plot.png"
    fig.savefig(rank_plot, dpi=140)
    plt.close(fig)

    sweep_points: dict[int, float] = {}
    for row in ranked:
        key = row["param_key"]
        if not key.startswith("sweeps="):
            continue
        head = key.split("__", 1)[0]
        try:
            sweeps = int(head.split("=", 1)[1])
        except Exception:
            continue
        pv = parse_float(row["aggregate"]["pstdev_mm"])
        if pv is None:
            continue
        if sweeps not in sweep_points or pv < sweep_points[sweeps]:
            sweep_points[sweeps] = pv

    if sweep_points:
        fig2, ax2 = plt.subplots(figsize=(8, 4.5))
        xs = sorted(sweep_points)
        ys = [sweep_points[s] for s in xs]
        ax2.plot(xs, ys, marker="o")
        ax2.set_xlabel("sweeps-per-round")
        ax2.set_ylabel("best pstdev_mm")
        ax2.set_title("Sweep Count vs Stability (lower is better)")
        ax2.grid(True, alpha=0.3)
        fig2.tight_layout()
        sweep_plot = out_dir / "sweep_stability_plot.png"
        fig2.savefig(sweep_plot, dpi=140)
        plt.close(fig2)
        return {"ranking_plot": str(rank_plot), "sweep_stability_plot": str(sweep_plot)}
    return {"ranking_plot": str(rank_plot)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Loop-test and optimize one Master->Peer matrix link.")
    p.add_argument("--master", type=parse_anchor_letter, default="A")
    p.add_argument("--peer", type=parse_anchor_letter, default="H")
    p.add_argument("--method", choices=("provision", "serial"), default="provision")
    p.add_argument("--probe-map", help="CSV map with anchor/probe_serial/serial_port columns")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--sweeps-per-round", type=int, default=3)
    p.add_argument("--sweep-grid", default="", help="Optional comma-separated sweep counts, e.g. 1,2,3,5,8")
    p.add_argument("--min-samples", type=int, default=50)
    p.add_argument("--params", nargs="*", default=[])
    p.add_argument("--out-dir", default=None)
    p.add_argument("--skip-flash", action="store_true", help="Skip role switching and flashing operations.")
    p.add_argument("--dry-run", action="store_true", help="Do not capture hardware; parse existing logs.")
    p.add_argument(
        "--dry-run-source",
        default="",
        help="Optional raw.log path reused for all rounds in dry-run mode if round files are absent.",
    )
    p.add_argument("--timeout", type=float, default=40.0, help="Per-round capture timeout in seconds.")
    p.add_argument("--settle-s", type=float, default=1.5)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--quality-min", type=int, default=50)
    p.add_argument("--pass-pstdev-mm", type=float, default=10.0)
    p.add_argument("--pass-quality-median", type=float, default=70.0)
    p.add_argument("--warn-ci95-mm", type=float, default=2.0)
    p.add_argument("--abort-on-fail", action="store_true")
    p.add_argument("--apply-params-cmd", default="", help="Optional shell command template to apply params per combo.")
    p.add_argument("--serial-timeout", type=float, default=6.0)
    p.add_argument("--no-reset", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.master == args.peer:
        raise SystemExit("--master and --peer must be different")
    preflight_kill_hotplug_scanners()
    if args.rounds <= 0 or args.sweeps_per_round <= 0:
        raise SystemExit("--rounds and --sweeps-per-round must be > 0")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("logs") / f"loop_test_{args.master}_{args.peer}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    param_dims = parse_param_specs(args.params)
    param_grid = build_param_grid(param_dims)

    sweep_grid = [args.sweeps_per_round]
    if args.sweep_grid.strip():
        sweep_grid = [int(x.strip()) for x in args.sweep_grid.split(",") if x.strip()]
        if not sweep_grid:
            raise SystemExit("invalid --sweep-grid")

    expanded_grid: list[tuple[int, dict[str, str]]] = []
    for sweeps in sweep_grid:
        for combo in param_grid:
            expanded_grid.append((sweeps, combo))

    mapping: dict[str, AnchorEndpoint] = {}
    if not args.dry_run:
        if not args.probe_map:
            raise SystemExit("--probe-map is required unless --dry-run")
        mapping = load_probe_map(Path(args.probe_map))
        if args.apply_params_cmd:
            if "flash_master_noninteractive.sh" in args.apply_params_cmd or "683234364" in args.apply_params_cmd:
                raise SystemExit("--apply-params-cmd contains forbidden 52840 path (blocked by anchor-only policy)")

    dry_run_source = Path(args.dry_run_source) if args.dry_run_source else None
    grid_results: list[dict[str, Any]] = []

    run_meta = {
        "master": args.master,
        "peer": args.peer,
        "method": args.method,
        "rounds": args.rounds,
        "sweeps_per_round_default": args.sweeps_per_round,
        "sweep_grid": sweep_grid,
        "min_samples": args.min_samples,
        "params_spec": args.params,
        "grid_size": len(expanded_grid),
        "dry_run": bool(args.dry_run),
        "timestamp": ts,
    }
    save_json(out_dir / "run_meta.json", run_meta)

    for sweeps_per_round, params in expanded_grid:
        param_key = normalize_param_key(params, sweeps_per_round)
        combo_dir = out_dir / "params" / param_key
        combo_dir.mkdir(parents=True, exist_ok=True)
        apply_params_if_needed(args, params, combo_dir)

        round_results: list[dict[str, Any]] = []
        for ridx in range(1, args.rounds + 1):
            round_dir = combo_dir / f"round_{ridx}"
            round_dir.mkdir(parents=True, exist_ok=True)

            raw_log = round_dir / "raw.log"
            capture_info: dict[str, Any] = {}
            if args.dry_run:
                if not raw_log.exists() and dry_run_source and dry_run_source.exists():
                    raw_log.write_text(dry_run_source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                if not raw_log.exists():
                    capture_info = {"status": "missing_raw_log", "timeout": True, "sweeps_completed": 0}
                else:
                    capture_info = {"status": "dry_run_loaded", "timeout": False, "sweeps_completed": sweeps_per_round}
            else:
                set_roles(args, mapping, args.master, round_dir)
                raw_log, capture_info = capture_master_round(args, mapping, round_dir, sweeps_per_round)

            pairs_csv = round_dir / f"pairs_master_{args.master}.csv"
            parse_meta = {"row_count": 0, "sweeps_seen": 0}
            if raw_log.exists():
                parse_meta = parse_raw_to_pairs_csv(raw_log, pairs_csv, sweep_limit=sweeps_per_round)

            samples, qualities, counters = ([], [], {"rows_total": 0, "rows_link": 0, "rows_valid": 0, "rows_invalid": 0})
            if pairs_csv.exists():
                samples, qualities, counters = extract_link_samples(
                    pairs_csv=pairs_csv,
                    master=args.master,
                    peer=args.peer,
                    quality_min=args.quality_min,
                )
            metrics = quant_metrics(samples, qualities)
            sufficient = metrics["count"] >= args.min_samples
            round_result = {
                "round": ridx,
                "params": params,
                "sweeps_per_round": sweeps_per_round,
                "capture": capture_info,
                "parse": parse_meta,
                "extract_counters": counters,
                "metrics": metrics,
                "sufficient": sufficient,
                "samples_mm": samples,
                "qualities": qualities,
            }
            round_results.append(round_result)
            save_json(round_dir / "metrics.json", round_result)

            if args.abort_on_fail and metrics["count"] == 0:
                raise RuntimeError(f"round failed without samples for {param_key} round={ridx}")

        agg = aggregate_rounds(round_results)
        score = scoring(agg, args.min_samples)
        combo_result = {
            "param_key": param_key,
            "params": params,
            "sweeps_per_round": sweeps_per_round,
            "round_results": [
                {
                    "round": r["round"],
                    "sufficient": r["sufficient"],
                    "metrics": r["metrics"],
                    "capture": r["capture"],
                }
                for r in round_results
            ],
            "aggregate": agg,
            "score": score,
        }
        save_json(combo_dir / "aggregate.json", combo_result)
        grid_results.append(combo_result)

    ranked = sorted(grid_results, key=lambda x: x["score"])
    save_json(out_dir / "grid_results.json", {"ranked": ranked, "run_meta": run_meta})

    if not ranked:
        print("best=none verdict=FAIL reason=no-results")
        return 1

    best = ranked[0]
    best_params = best["params"]
    best_sweeps = int(best["sweeps_per_round"])
    confirm_dir = out_dir / "best_confirm"
    confirm_dir.mkdir(parents=True, exist_ok=True)

    confirm_raw = confirm_dir / "raw.log"
    if args.dry_run:
        if not confirm_raw.exists() and dry_run_source and dry_run_source.exists():
            confirm_raw.write_text(dry_run_source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        confirm_capture = {"status": "dry_run_loaded" if confirm_raw.exists() else "missing_raw_log"}
    else:
        set_roles(args, mapping, args.master, confirm_dir)
        confirm_raw, confirm_capture = capture_master_round(args, mapping, confirm_dir, best_sweeps)

    confirm_pairs = confirm_dir / f"pairs_master_{args.master}.csv"
    confirm_parse = {"row_count": 0, "sweeps_seen": 0}
    if confirm_raw.exists():
        confirm_parse = parse_raw_to_pairs_csv(confirm_raw, confirm_pairs, sweep_limit=best_sweeps)
    confirm_samples, confirm_q, confirm_counters = extract_link_samples(
        confirm_pairs, args.master, args.peer, args.quality_min
    ) if confirm_pairs.exists() else ([], [], {"rows_total": 0, "rows_link": 0, "rows_valid": 0, "rows_invalid": 0})
    confirm_metrics = quant_metrics(confirm_samples, confirm_q)
    verdict = verdict_from_metrics(confirm_metrics, args)
    recommend = (
        "stable"
        if verdict == "PASS"
        else "increase sweeps/timeout or improve link quality"
    )
    confirm_out = {
        "best_param_key": best["param_key"],
        "best_params": best_params,
        "best_sweeps_per_round": best_sweeps,
        "capture": confirm_capture,
        "parse": confirm_parse,
        "extract_counters": confirm_counters,
        "metrics": confirm_metrics,
        "verdict": verdict,
        "recommendation": recommend,
        "thresholds": {
            "min_samples": args.min_samples,
            "pass_pstdev_mm": args.pass_pstdev_mm,
            "pass_quality_median": args.pass_quality_median,
            "warn_ci95_mm": args.warn_ci95_mm,
        },
    }
    save_json(out_dir / "best_param_confirm.json", confirm_out)

    plots = try_make_plots(out_dir, ranked)
    if plots:
        save_json(out_dir / "plots.json", plots)

    print(
        f"best={best['param_key']} verdict={verdict} "
        f"count={confirm_metrics['count']} pstdev_mm={confirm_metrics['pstdev_mm']} "
        f"quality_median={confirm_metrics['quality_median']} out={out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
