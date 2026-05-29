#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


THIS = Path(__file__).resolve()
OFFICIAL_ROOT = THIS.parents[2]
REPLAY_SCRIPT = THIS.with_name("cuda_t4_keepk_replay.py")


def parse_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def visible_gpu_count() -> int | None:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "-L"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return sum(1 for line in proc.stdout.splitlines() if line.strip().startswith("GPU "))


def selected_layouts_for_args(value: str) -> list[str]:
    all_layouts = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
    if value.strip().lower() == "all":
        return all_layouts
    return parse_csv(value)


def selected_methods_for_args(value: str) -> list[str]:
    all_methods = ["T1", "T2", "T3", "T4"]
    if value.strip().lower() == "all":
        return all_methods
    return [x.strip().upper() for x in parse_csv(value)]


def selected_kinds_for_args(args: argparse.Namespace) -> list[str]:
    kinds = []
    if not args.skip_static:
        kinds.append("static")
    if not args.skip_roto:
        kinds.append("roto")
    return kinds


def build_blocks(args: argparse.Namespace) -> list[dict]:
    blocks = []
    for layout in selected_layouts_for_args(args.layout_versions):
        for method in selected_methods_for_args(args.tag_methods):
            for kind in selected_kinds_for_args(args):
                weight = 1.0
                if kind == "roto":
                    weight *= 1.35
                if method in ("T3", "T4"):
                    weight *= 1.08
                blocks.append(
                    {
                        "block_index": len(blocks) + 1,
                        "layout": layout,
                        "tag_method": method,
                        "kind": kind,
                        "weight": weight,
                    }
                )
    return blocks


def weighted_assignments(blocks: list[dict], n_workers: int) -> list[list[int]]:
    loads = [0.0] * n_workers
    assignments: list[list[int]] = [[] for _ in range(n_workers)]
    for block in sorted(blocks, key=lambda b: (-float(b["weight"]), b["block_index"])):
        worker = min(range(n_workers), key=lambda i: loads[i])
        assignments[worker].append(int(block["block_index"]))
        loads[worker] += float(block["weight"])
    for xs in assignments:
        xs.sort()
    return assignments


def build_worker_cmd(args: argparse.Namespace, shard_id: int, num_shards: int, block_indices: list[int] | None = None) -> list[str]:
    cmd = [
        sys.executable,
        str(REPLAY_SCRIPT),
        "--out",
        str(Path(args.out)),
        "--repeats",
        str(args.repeats),
        "--repeat-batch",
        str(args.repeat_batch),
        "--max-host-gb",
        str(args.max_host_gb),
        "--keep-list",
        str(args.keep_list),
        "--seed",
        str(args.seed),
        "--max-frames-per-track",
        str(args.max_frames_per_track),
        "--layout-versions",
        str(args.layout_versions),
        "--tag-methods",
        str(args.tag_methods),
        "--num-shards",
        str(num_shards),
        "--shard-id",
        str(shard_id),
    ]
    if block_indices:
        cmd.extend(["--block-indices", ",".join(str(x) for x in block_indices)])
    if args.summary_only:
        cmd.append("--summary-only")
    if args.skip_existing:
        cmd.append("--skip-existing")
    if args.force_large_batch:
        cmd.append("--force-large-batch")
    if args.skip_static:
        cmd.append("--skip-static")
    if args.skip_roto:
        cmd.append("--skip-roto")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch CUDA keep-k replay as independent slots across two or more GPUs."
    )
    parser.add_argument(
        "--out",
        default=str(OFFICIAL_ROOT / "Analysis" / "Monte-Carlo-Simulation"),
        help="shared output root; shard workers write non-overlapping layout/tag/kind subfolders",
    )
    parser.add_argument("--gpus", default="0,1", help="physical GPU ids to use, e.g. 0,1")
    parser.add_argument("--repeats", type=int, default=5000)
    parser.add_argument("--repeat-batch", type=int, default=1000)
    parser.add_argument("--max-host-gb", type=float, default=4.0)
    parser.add_argument("--keep-list", default="8,7,6,5,4")
    parser.add_argument("--seed", type=int, default=20260528)
    parser.add_argument("--max-frames-per-track", type=int, default=0)
    parser.add_argument("--layout-versions", default="all")
    parser.add_argument("--tag-methods", default="all")
    parser.add_argument("--summary-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force-large-batch", action="store_true")
    parser.add_argument("--skip-static", action="store_true")
    parser.add_argument("--skip-roto", action="store_true")
    parser.add_argument("--scheduler", choices=["weighted", "modulo"], default="weighted")
    parser.add_argument("--wait", action="store_true", help="wait for all workers instead of returning after launch")
    parser.add_argument("--dry-run", action="store_true", help="print commands without starting workers")
    args = parser.parse_args()
    args.out = str(Path(args.out).expanduser().resolve())

    gpu_ids = parse_csv(args.gpus)
    if not gpu_ids:
        raise SystemExit("no GPUs selected")
    detected = visible_gpu_count()
    if detected is not None and not args.dry_run:
        missing = [g for g in gpu_ids if not g.isdigit() or int(g) >= detected]
        if missing:
            raise SystemExit(f"requested GPU ids {missing}, but nvidia-smi reports {detected} GPU(s)")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    num_shards = len(gpu_ids)
    workers = []
    launch_time = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    blocks = build_blocks(args)
    assignments = weighted_assignments(blocks, num_shards) if args.scheduler == "weighted" else [None] * num_shards

    for shard_id, gpu_id in enumerate(gpu_ids):
        block_indices = assignments[shard_id] if args.scheduler == "weighted" else None
        cmd = build_worker_cmd(args, shard_id, num_shards, block_indices=block_indices)
        log_path = out_root / f"run_gpu{gpu_id}_shard{shard_id}_of_{num_shards}.log"
        pid_path = out_root / f"run_gpu{gpu_id}_shard{shard_id}_of_{num_shards}.pid"
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = gpu_id
        printable = " ".join(shlex.quote(x) for x in cmd)
        print(f"[dual-gpu] shard={shard_id}/{num_shards} gpu={gpu_id} log={log_path}")
        if block_indices:
            assigned_weight = sum(float(b["weight"]) for b in blocks if int(b["block_index"]) in set(block_indices))
            print(f"[dual-gpu] scheduler=weighted blocks={block_indices} assigned_weight={assigned_weight:.2f}")
        print(f"[dual-gpu] CUDA_VISIBLE_DEVICES={gpu_id} {printable}")
        if args.dry_run:
            workers.append({"gpu": gpu_id, "shard_id": shard_id, "cmd": cmd, "log": str(log_path), "pid": None, "block_indices": block_indices})
            continue
        log_f = log_path.open("ab")
        proc = subprocess.Popen(
            cmd,
            cwd=str(OFFICIAL_ROOT.parents[1]),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
        workers.append(
            {
                "gpu": gpu_id,
                "shard_id": shard_id,
                "cmd": cmd,
                "log": str(log_path),
                "pid_path": str(pid_path),
                "pid": proc.pid,
                "block_indices": block_indices,
            }
        )

    manifest = {
        "started_at": launch_time,
        "out": str(out_root),
        "script": str(REPLAY_SCRIPT),
        "gpus": gpu_ids,
        "num_shards": num_shards,
        "workers": workers,
        "scheduler": args.scheduler,
        "weighted_blocks": blocks,
        "resume_policy": "skip existing complete summary CSVs before running a block",
    }
    manifest_path = out_root / ("dual_gpu_dry_run_manifest.json" if args.dry_run else "dual_gpu_run_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_root / "run_dual_gpu_command.txt").write_text(
        " ".join(shlex.quote(x) for x in [sys.executable, str(THIS), *sys.argv[1:]]) + "\n",
        encoding="utf-8",
    )

    if args.dry_run:
        print(f"[dual-gpu] dry-run manifest: {manifest_path}")
        return 0

    print(f"[dual-gpu] launched {len(workers)} worker(s); manifest={manifest_path}")
    if not args.wait:
        return 0

    exit_code = 0
    for worker in workers:
        pid = int(worker["pid"])
        proc_exit = None
        while proc_exit is None:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                proc_exit = 0
                break
            time.sleep(5)
        print(f"[dual-gpu] worker gpu={worker['gpu']} shard={worker['shard_id']} finished")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
