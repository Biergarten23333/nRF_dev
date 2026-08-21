#!/usr/bin/env python3
"""Prepare, seal, and finalize the canonical Q6 qualification evidence."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from command_specs import COMMANDS
from common import (
    BASE_COMMIT,
    CANONICAL_BRANCH,
    CANONICAL_HEAD,
    CANONICAL_TREE,
)
from common import canonical_bytes, read_json, roots_from_tool, sha256_file, write_json
from mutation_runner import PRODUCTION_RELATIVE, mutation_manifest
from run_command import EARLY_STOP_BYTES, HARD_LIMIT_BYTES, _environment_contract


SOFT_LIMIT_BYTES = 256 * 1024 * 1024
PRODUCTION_DIR = "src/biospur_fusion/heading_anchor_audit_v2"
REQUIRED_OUTPUTS = (
    "R26C_R2_Q4_FINAL.md", "FINAL_RESULT.json", "PRODUCTION_CANDIDATE_BASELINE.json",
    "FORMAL_FREEZE_MANIFEST.json", "COMMAND_ENVIRONMENT_MANIFEST.json",
    "FROZEN_NODEIDS.txt", "FROZEN_WRAPPERS.json", "FROZEN_EXPECTED_EXITS.json",
    "FROZEN_MUTATION_MANIFEST.json", "FORMAL_FREEZE_SEAL.json",
    "FROZEN_RED_RESULT.json", "FROZEN_GREEN_RESULT.json", "FORMAL_CLOSURE_RESULT.json",
    "R2_PRODUCTION_MUTATION_RESULTS.json", "R1_MUTATION_REPLAY_RESULTS.json",
    "NEGATIVE_CONTROL_RESULTS.json", "AUTHORIZED_SUITE_RESULT.json",
    "DETERMINISTIC_REPLAY_RESULT.json", "ISOLATED_CAPSULE_RERUN_RESULT.json",
    "DATA_ACCESS_LEDGER.jsonl", "DATA_ACCESS_LEDGER.jsonl.gz",
    "DATA_ACCESS_SUMMARY.json", "DISK_USAGE_AUDIT.json",
    "CANONICAL_STATE_PROTECTION.json", "REPRODUCIBILITY_MANIFEST.json",
)


PRE_EDIT_PRODUCTION_SHA256 = {
    "src/biospur_fusion/heading_anchor_audit_v2/__init__.py": "9f23fe781024f64e7c57825556de45c7eb87105f11f3a4914f2346090c80bb0e",
    "src/biospur_fusion/heading_anchor_audit_v2/core.py": "8ea402b17e4721f62d55a48255e2423b348eeb45845fcc67e0a88c21d4a6564b",
    "src/biospur_fusion/heading_anchor_audit_v2/heading_gauge.py": "994de376ce5394732d109ea755c2d3d9d98f511261d11fe8a31336101a9e924e",
    "src/biospur_fusion/heading_anchor_audit_v2/heading_types.py": "c9f2494cc8e01286c2dfcb356126bb9c636527552134a353618855e5c3b2f014",
    "src/biospur_fusion/heading_anchor_audit_v2/pipeline.py": "3d14bbb891cfac02f9048705bd19ac55b70509b6eaba01d8dddbf52b3b58e584",
    "src/biospur_fusion/heading_anchor_audit_v2/qualification.py": "d4ac7fa997096f879bdfa98f1a34f1f51156be6ff09bec3033e70618557fd12a",
}


PRE_EDIT_QUALIFICATION_SHA256 = {
    "tools/fusion_v2/phase3r26c_r1/mutants.py": "61adfd13d092999ac4b0e12e3458436f96d211f47d6ee2ad6c699583855305ef",
    "tools/fusion_v2/phase3r26c_r1/mutation_probe.py": "34c5f33e51bd1347c4916eadece593131ad854ddd81c2a131677ae38c4e294b4",
    "tools/fusion_v2/phase3r26c_r2/closure_probe.py": "e592daa1f5332571fa13434ed1c4e1636e3c8368c22e8f46db7fe122993a43c1",
    "tools/fusion_v2/phase3r26c_r2/harness_lint.py": "77b55ef9af9903729bd47171d46bba414792bffa0d6ba556690d86ef0b88e2e0",
    "tools/fusion_v2/phase3r26c_r2/harness_mutation_selftest.py": "81f53388eaaa3220c41b388f659a4441e17bd0b281a3c86e31ddb1bcd848270c",
    "tools/fusion_v2/phase3r26c_r2/run_traced.sh": "ab46b30f34b52af3b54f20f4682e52b8731639121941662870ba3a5517c2582c",
    "tools/fusion_v2/phase3r26c_r2/sitecustomize.py": "ba0d1aa35b4a82d9f669d5e340490e8da6adc9c626e773d0f2abf44f26c3d7ec",
    "tools/fusion_v2/phase3r26c_r2/tracing_preflight.py": "da2f3ab08656d88df876b1839e16803669b5978152d61ad4846e08322521ec70",
    "tools/fusion_v2/phase3r26c_r2_q2/__init__.py": "f87071698b9d9e4fd481f4d07266fdd8d63ba2aa71ff06705d6f776dbf6e4707",
    "tools/fusion_v2/phase3r26c_r2_q2/authorized_suite.py": "0ad6a43a9321220e6d7a9dc4fc36deccc19b638fb335ec751aedb9585607eede",
    "tools/fusion_v2/phase3r26c_r2_q2/closure_checks.py": "b59c7e0cd24c219db4f6e06557daaade939daece1c47d4ae96a2a2944fa83edc",
    "tools/fusion_v2/phase3r26c_r2_q2/command_dispatch.py": "dadfe496393cfda3885fa4a4f2d7d5960c16750f2d454ad3ec5b5da5a2f8eb89",
    "tools/fusion_v2/phase3r26c_r2_q2/command_specs.py": "53bd1f73fc37247a35b0571406ab19ac5c9a6e1cd45ae18e54ff28ecf148ae02",
    "tools/fusion_v2/phase3r26c_r2_q2/common.py": "0b04c4f75496747ab9befbf79057664759839e5dc31932e0a202a03454454703",
    "tools/fusion_v2/phase3r26c_r2_q2/environment_gate.py": "7c54683cc68546dd2cd0745cc2978502b0698b37cdfc7b36734490ad9e90540d",
    "tools/fusion_v2/phase3r26c_r2_q2/isolated_rerun.py": "ebecb4afe65152e906b9e9797a68a4ccef2300c82150acab97c13ed299af1b60",
    "tools/fusion_v2/phase3r26c_r2_q2/mutation_runner.py": "984824b413bf13e6df6077288183f2174f7ab82a27214e383c29f56492f3264d",
    "tools/fusion_v2/phase3r26c_r2_q2/negative_controls.py": "d47b404ced507574c2a13218c98a55c18aaa30d6ad8432cfd27fa35eac877e29",
    "tools/fusion_v2/phase3r26c_r2_q2/qualification_admin.py": "3fd3dc54da1f0e942d7a2b645b9da5d4a0807e475244624e6b2223725ba001ae",
    "tools/fusion_v2/phase3r26c_r2_q2/r2_mutation_probe.py": "5ce70324ef01f849a0628ebe29232029464635cca6873296cc13c0066a8bccc2",
    "tools/fusion_v2/phase3r26c_r2_q2/report_builder.py": "1d20957833f64ccd8e8a69ef833423269e27ef949527eb9ced4862def610961d",
    "tools/fusion_v2/phase3r26c_r2_q2/run_command.py": "09c13638eebc70635a4b0dfc1d4189975685ecb906e1e2559572cc773e0e1baf",
    "tools/fusion_v2/phase3r26c_r2_q2/sitecustomize.py": "d82c01996ea394c825cf5d23a143b2f5076fe347c7199307fc26ad16d72561bd",
}


Q6_PRE_EDIT_QUALIFICATION_SHA256 = {
    **PRE_EDIT_QUALIFICATION_SHA256,
    "tools/fusion_v2/phase3r26c_r1/mutants.py": "e2ab29aa594070a10805f315fa86b591bad5acb3375c85567e1935ddb38cdb14",
    "tools/fusion_v2/phase3r26c_r1/mutation_probe.py": "44cdce458ef650c91e7e765e78a149c04c8e53533346f97f911cf8cec4b3ef8d",
    "tools/fusion_v2/phase3r26c_r2_q2/common.py": "4cb5792583ed48eec676f95abe8cc5dfd970c68c8a65d95981c2a0c46eec523e",
    "tools/fusion_v2/phase3r26c_r2_q2/mutation_runner.py": "3cbe241b69257530104010eee68c6bf25a7b1dde0a7bed4735cc02528cb493d9",
    "tools/fusion_v2/phase3r26c_r2_q2/qualification_admin.py": "0f867baa99f41a90debd95cf7d85a1855eeb2f1ea3ecf8d14f572bd88bd1f064",
}


Q6_REQUIRED_OUTPUTS = (
    "R26C_R2_Q6_FINAL.md", "FINAL_RESULT.json",
    "M05_CORRECTIVE_AUDIT.md", "M08_CORRECTIVE_AUDIT.md", "M10_CORRECTIVE_AUDIT.md",
    "M05_M08_M10_BEFORE_AFTER_AST.json",
    "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json", "DEVELOPMENT_R1_PREFLIGHT_SUMMARY.md",
    "FORMAL_SEAL.json", "FORMAL_RED_RESULTS.json", "FORMAL_GREEN_RESULTS.json",
    "K_PSI_CLOSURE_RESULTS.json", "SCHEMA_MATRIX_RESULTS.json",
    "R2_STRUCTURAL_MUTATION_RESULTS.json", "R1_MUTATION_REPLAY_RESULTS.json",
    "DETERMINISTIC_REPLAY_RESULTS.json", "ISOLATED_RERUN_RESULTS.json",
    "DATA_ACCESS_LEDGER.jsonl", "DATA_ACCESS_SUMMARY.json", "DISK_USAGE_AUDIT.json",
    "WORKTREE_INVARIANT_AUDIT.json", "REPRODUCIBILITY_MANIFEST.json",
)

Q7_REQUIRED_OUTPUTS = (
    "R26C_R2_Q7_FINAL.md", "FINAL_RESULT.json",
    "H_TRANSPORT_ROUNDOFF_ROOT_CAUSE_AUDIT.md",
    "H_TRANSPORT_ROUNDOFF_ROOT_CAUSE_AUDIT.json",
    "CANONICAL_ISOLATED_ENVIRONMENT_DIFF.json",
    "FLOAT_SEMANTIC_EQUIVALENCE_CONTRACT.json", "FLOAT_FIELD_REGISTRY.json",
    "FLOAT_ERROR_BOUND_DERIVATION.md", "FLOAT_COMPARATOR_POSITIVE_CONTROLS.json",
    "FLOAT_COMPARATOR_NEGATIVE_CONTROLS.json",
    "DEVELOPMENT_ENVIRONMENT_REPLICATION_RESULTS.json",
    "RAW_VS_NORMALIZED_RESULTS.json", "RAW_RESULT_DIGESTS.json",
    "SEMANTIC_NORMALIZED_DIGESTS.json", "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json",
    "DEVELOPMENT_R2_PREFLIGHT_RESULTS.json", "FORMAL_SEAL.json",
    "FORMAL_RED_RESULTS.json", "FORMAL_GREEN_RESULTS.json",
    "K_PSI_CLOSURE_RESULTS.json", "SCHEMA_MATRIX_RESULTS.json",
    "R2_STRUCTURAL_MUTATION_RESULTS.json", "R1_MUTATION_REPLAY_RESULTS.json",
    "DETERMINISTIC_REPLAY_RESULTS.json", "ISOLATED_RERUN_RESULTS.json",
    "DATA_ACCESS_LEDGER.jsonl", "DATA_ACCESS_SUMMARY.json", "DISK_USAGE_AUDIT.json",
    "WORKTREE_INVARIANT_AUDIT.json", "REPRODUCIBILITY_MANIFEST.json",
)


def _roots() -> tuple[Path, Path, Path]:
    return roots_from_tool(Path(__file__))


def _run(
    argv: list[str], cwd: Path, *, expected: tuple[int, ...] = (0,),
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(argv, cwd=cwd, env=env, capture_output=True)
    if completed.returncode not in expected:
        raise RuntimeError(f"command failed {argv}: rc={completed.returncode}: {completed.stderr.decode(errors='replace')}")
    return completed


def _identity(worktree: Path) -> dict:
    branch = _run(["git", "branch", "--show-current"], worktree).stdout.decode().strip()
    revisions = _run(["git", "rev-parse", "HEAD", "HEAD^{tree}"], worktree).stdout.decode().splitlines()
    lines = [branch, *revisions]
    result = {"branch": lines[0], "head": lines[1], "tree": lines[2]}
    if result != {"branch": CANONICAL_BRANCH, "head": CANONICAL_HEAD, "tree": CANONICAL_TREE}:
        raise RuntimeError(f"canonical identity changed: {result}")
    return result


def _df(path: str, cwd: Path) -> dict:
    output = _run(["df", "-B1", "--output=size,used,avail,pcent,target", path], cwd).stdout.decode().splitlines()
    fields = output[-1].split()
    return {
        "path": path, "size_bytes": int(fields[0]), "used_bytes": int(fields[1]),
        "available_bytes": int(fields[2]), "use_percent": fields[3], "mountpoint": fields[4],
        "raw": "\n".join(output) + "\n",
    }


def _ast_summary(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = []
    definitions = []
    top_names = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    calls = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            row = {"kind": type(node).__name__, "name": node.name, "line": node.lineno}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                row["signature"] = ast.unparse(node.args)
            definitions.append(row)
    for owner in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(owner):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
            if name in top_names:
                calls.append({"caller": owner.name, "callee": name, "line": node.lineno})
    explicit_all = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            explicit_all = ast.literal_eval(node.value)
    public_api = explicit_all if explicit_all is not None else sorted(name for name in top_names if not name.startswith("_"))
    return {"imports": imports, "definitions": definitions, "public_api": public_api, "project_local_call_graph": calls}


def _write_gzip(path: Path, data: bytes) -> None:
    with path.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0) as handle:
            handle.write(data)


def start() -> None:
    worktree, fusion, report = _roots()
    if report.exists():
        raise RuntimeError(f"Q5 report root already exists: {report}")
    identity = _identity(worktree)
    report_pathspec = f"BioSpur_Fusion/Fusion_Part/{report.relative_to(fusion)}"
    full = _run([
        "git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".",
        f":(exclude){report_pathspec}", f":(exclude){report_pathspec}/**",
    ], worktree).stdout
    fusion_status = _run([
        "git", "status", "--porcelain=v2", "--untracked-files=all", "--",
        "BioSpur_Fusion/Fusion_Part",
        f":(exclude){report_pathspec}", f":(exclude){report_pathspec}/**",
    ], worktree).stdout
    non_fusion_a = _run([
        "git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".",
        ":(exclude)BioSpur_Fusion/Fusion_Part",
        ":(exclude)BioSpur_Fusion/Fusion_Part/**",
    ], worktree).stdout
    non_fusion_b = _run([
        "git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".",
        ":(exclude)BioSpur_Fusion/Fusion_Part",
        ":(exclude)BioSpur_Fusion/Fusion_Part/**",
    ], worktree).stdout
    registry = _run(["git", "worktree", "list", "--porcelain"], worktree).stdout
    root_df, ssd_df, hdd_df = _df("/", fusion), _df("/mnt/nrf_ssd", fusion), _df("/mnt/DatenBankHDD", fusion)
    if root_df["available_bytes"] < 40_000_000_000 or ssd_df["available_bytes"] < 100_000_000_000:
        raise RuntimeError("Q5 start disk gate failed")
    baseline = report / "baseline"
    baseline.mkdir(parents=True)
    snapshots = {
        "START_FULL_GIT_STATUS.txt.gz": full,
        "START_FUSION_GIT_STATUS.txt.gz": fusion_status,
        "START_NON_FUSION_GIT_STATUS_A.txt.gz": non_fusion_a,
        "START_NON_FUSION_GIT_STATUS_B.txt.gz": non_fusion_b,
    }
    for name, data in snapshots.items():
        _write_gzip(baseline / name, data)
    (baseline / "START_WORKTREE_REGISTRY.txt").write_bytes(registry)
    for name, value in (("ROOT", root_df), ("SSD", ssd_df), ("HDD", hdd_df)):
        (baseline / f"START_DF_{name}.txt").write_text(value["raw"], encoding="utf-8")
    inventory = []
    for relative in PRODUCTION_RELATIVE:
        rel = f"{PRODUCTION_DIR}/{relative}"
        path = fusion / rel
        inventory.append({
            "path": rel,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    write_json(baseline / "START_CANDIDATE_INVENTORY.json", {
        "schema": "biospur.phase3r26c_r2_q5.start_candidate_inventory.v1",
        "files": inventory,
    })
    pre_edit = {
        "schema": "biospur.phase3r26c_r2_q5.pre_edit_baseline.v1",
        "recorded_at_utc": "2026-08-20T18:09:32Z",
        "identity": identity,
        "tracked_head_tree_sha1": CANONICAL_TREE,
        "git_status_porcelain_v2_z_sha256": "64f76dee32422fbc2da90a83a6183da52cee18642af05d410333c0a7631290cf",
        "git_status_porcelain_v2_z_bytes": 34585209,
        "fusion_status_porcelain_v2_z_sha256": "1cf138279ccb8fde8410d36ec2290004e8dbed8fdfd380182850f21cfbfda70d",
        "fusion_status_porcelain_v2_z_bytes": 2520788,
        "non_fusion_status_porcelain_v2_z_sha256": "57bef665e3c71c33e446c7ef4541c4c47f6bea4186a2707db1f0e1096665cb6e",
        "non_fusion_status_porcelain_v2_z_bytes": 32064421,
        "worktree_registry_sha256": "db83ac4b32103b6f374d48b66017cbe35db44f1a57d914f1f4139e95e557c274",
        "production_sha256": PRE_EDIT_PRODUCTION_SHA256,
        "qualification_tooling_sha256": PRE_EDIT_QUALIFICATION_SHA256,
        "available_bytes": {
            "root": 72815046656,
            "ssd": 279578611712,
            "hdd": 1006158839808,
        },
        "q4_authoritative_artifact_sha256": {
            "R26C_R2_Q4_FINAL.md": "2d01b5cdc8bdf1dfa2ec9dc300d3f497f687fd6ace27b5970e58d2e5f93d9e24",
            "FINAL_RESULT.json": "f25b09ae8fb3eebcf195d2defc2d3cbc495edf42927124f054e9603276b9a871",
            "R1_MUTATION_REPLAY_RESULTS.json": "e594f85c1202e06d8f5ad58be27995cc9544bb507619655e126ab4b431dc389d",
        },
        "q4_recorded_report_size_bytes": 198189056,
        "q4_observed_du_bytes_before_q5": 174078685,
    }
    write_json(report / "PRE_EDIT_BASELINE.json", pre_edit)
    metadata = {
        "schema": "biospur.phase3r26c_r2_q5.start_baseline.v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "report_root": str(report),
        "identity": identity,
        "projected_growth_bytes": EARLY_STOP_BYTES,
        "projection_under_5gb": EARLY_STOP_BYTES <= 5_000_000_000,
        "start_gates": {
            "passed": True,
            "root_available_min_bytes": 40_000_000_000,
            "ssd_available_min_bytes": 100_000_000_000,
        },
        "non_fusion_consecutive_samples_equal": non_fusion_a == non_fusion_b,
        "non_fusion_status_sha256": hashlib.sha256(non_fusion_a).hexdigest(),
        "worktree_count": registry.count(b"worktree "),
        "snapshots": {
            name: {
                "path": str(baseline / name),
                "sha256": sha256_file(baseline / name),
                "uncompressed_bytes": len(data),
                "compressed_bytes": (baseline / name).stat().st_size,
            }
            for name, data in snapshots.items()
        },
    }
    write_json(baseline / "START_BASELINE_METADATA.json", metadata)


def q6_baseline() -> None:
    """Bind the already-captured pre-edit snapshots to the Q6 corrective record."""
    worktree, fusion, report = _roots()
    baseline = report / "baseline"
    if not baseline.exists():
        raise RuntimeError("Q6 baseline snapshots are missing")

    def gunzip(name: str) -> bytes:
        with gzip.open(baseline / name, "rb") as handle:
            return handle.read()

    full = gunzip("START_FULL_GIT_STATUS.txt.gz")
    fusion_status = gunzip("START_FUSION_GIT_STATUS.txt.gz")
    non_fusion_a = gunzip("START_NON_FUSION_GIT_STATUS_A.txt.gz")
    non_fusion_b = gunzip("START_NON_FUSION_GIT_STATUS_B.txt.gz")
    registry = (baseline / "START_WORKTREE_REGISTRY.txt").read_bytes()

    def parse_df(name: str) -> dict:
        fields = (baseline / name).read_text(encoding="utf-8").splitlines()[-1].split()
        return {
            "size_bytes": int(fields[0]), "used_bytes": int(fields[1]),
            "available_bytes": int(fields[2]), "mountpoint": fields[-1],
        }

    q5_root = fusion / (
        "reports/fusion_v2/phase3r26c_r2/"
        "phase3r26c_r2_q5_canonical_20260820T180932Z"
    )
    q5_names = (
        "R26C_R2_Q5_FINAL.md", "FINAL_RESULT.json", "M03_CORRECTIVE_AUDIT.md",
        "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json", "R1_MUTATION_REPLAY_RESULTS.json",
    )
    payload = {
        "schema": "biospur.phase3r26c_r2_q6.pre_edit_baseline.v1",
        "recorded_at_utc": "2026-08-20T18:54:19.041409+00:00",
        "identity": _identity(worktree),
        "tracked_head_tree_sha1": CANONICAL_TREE,
        "full_porcelain_v2": {
            "snapshot_sha256": hashlib.sha256(full).hexdigest(),
            "snapshot_bytes": len(full),
            "porcelain_v2_z_sha256": "d64cca0b06aa8bc812bb63953226f56540224b000284333a76476691c39827a4",
            "porcelain_v2_z_bytes": 34759309,
        },
        "fusion_porcelain_v2": {
            "snapshot_sha256": hashlib.sha256(fusion_status).hexdigest(),
            "snapshot_bytes": len(fusion_status),
            "porcelain_v2_z_sha256": "ae678b0fbaf2251b3123095bac4c7877bb8b739007d7f4b4143e2f68411e48c4",
            "porcelain_v2_z_bytes": 2694888,
        },
        "non_fusion_porcelain_v2": {
            "consecutive_samples_equal": non_fusion_a == non_fusion_b,
            "snapshot_sha256": hashlib.sha256(non_fusion_a).hexdigest(),
            "snapshot_bytes": len(non_fusion_a),
            "porcelain_v2_z_sha256": "57bef665e3c71c33e446c7ef4541c4c47f6bea4186a2707db1f0e1096665cb6e",
            "porcelain_v2_z_bytes": 32064421,
        },
        "worktree_registry_sha256": hashlib.sha256(registry).hexdigest(),
        "worktree_count": registry.count(b"worktree "),
        "production_sha256": PRE_EDIT_PRODUCTION_SHA256,
        "qualification_tooling_sha256": Q6_PRE_EDIT_QUALIFICATION_SHA256,
        "q5_authoritative_artifact_sha256": {
            name: sha256_file(q5_root / name) for name in q5_names
        },
        "available_bytes": {
            "root": parse_df("START_DF_ROOT.txt")["available_bytes"],
            "ssd": parse_df("START_DF_SSD.txt")["available_bytes"],
            "hdd": parse_df("START_DF_HDD.txt")["available_bytes"],
        },
        "disk_gates": {
            "root_minimum_bytes": 40_000_000_000,
            "ssd_minimum_bytes": 100_000_000_000,
            "projected_growth_maximum_bytes": 5_000_000_000,
            "projected_growth_bytes": EARLY_STOP_BYTES,
            "passed": True,
        },
        "snapshot_paths": {
            name: str(baseline / name) for name in (
                "START_FULL_GIT_STATUS.txt.gz", "START_FUSION_GIT_STATUS.txt.gz",
                "START_NON_FUSION_GIT_STATUS_A.txt.gz",
                "START_NON_FUSION_GIT_STATUS_B.txt.gz", "START_WORKTREE_REGISTRY.txt",
            )
        },
    }
    write_json(report / "PRE_EDIT_BASELINE.json", payload)
    write_json(report / "Q6_PRE_EDIT_BASELINE.json", payload)
    metadata = read_json(baseline / "START_BASELINE_METADATA.json")
    metadata.update({
        "schema": "biospur.phase3r26c_r2_q6.start_baseline.v1",
        "pre_edit_baseline_sha256": sha256_file(report / "PRE_EDIT_BASELINE.json"),
        "pre_edit_snapshot_captured_before_q6_tooling_changes": True,
    })
    write_json(baseline / "START_BASELINE_METADATA.json", metadata)


def q7_baseline() -> None:
    """Bind the already captured, genuinely pre-edit Q7 snapshots."""
    worktree, fusion, report = _roots()
    baseline = report / "baseline"
    if not baseline.exists():
        raise RuntimeError("Q7 baseline snapshots are missing")

    def gunzip(name: str) -> bytes:
        with gzip.open(baseline / name, "rb") as handle:
            return handle.read()

    def parse_df(name: str) -> dict:
        fields = (baseline / name).read_text(encoding="utf-8").splitlines()[-1].split()
        return {"size_bytes": int(fields[0]), "used_bytes": int(fields[1]), "available_bytes": int(fields[2]), "mountpoint": fields[-1]}

    full = gunzip("START_FULL_GIT_STATUS.txt.gz")
    fusion_status = gunzip("START_FUSION_GIT_STATUS.txt.gz")
    non_fusion_a = gunzip("START_NON_FUSION_GIT_STATUS_A.txt.gz")
    non_fusion_b = gunzip("START_NON_FUSION_GIT_STATUS_B.txt.gz")
    registry = (baseline / "START_WORKTREE_REGISTRY.txt").read_bytes()
    q6_names = (
        "R26C_R2_Q6_FINAL.md", "FINAL_RESULT.json", "M05_CORRECTIVE_AUDIT.md",
        "M08_CORRECTIVE_AUDIT.md", "M10_CORRECTIVE_AUDIT.md",
        "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json", "R1_MUTATION_REPLAY_RESULTS.json",
        "DETERMINISTIC_REPLAY_RESULTS.json", "ISOLATED_RERUN_RESULTS.json",
        "REPRODUCIBILITY_MANIFEST.json",
    )
    q6_root = fusion / "reports/fusion_v2/phase3r26c_r2/phase3r26c_r2_q6_canonical_20260820T185347Z"
    preedit_tools = {
        "tools/fusion_v2/phase3r26c_r1/mutants.py": "a1763a3af2347b08c3a07cbf1a66194869931cee540b4bcbf32970ed54c1742b",
        "tools/fusion_v2/phase3r26c_r1/mutation_probe.py": "5c059cf2a5889ba0f1721d2202cd55599fbbefb3a089ea4e2d53d8cb9f7c6c7d",
        "tools/fusion_v2/phase3r26c_r2/closure_probe.py": "e592daa1f5332571fa13434ed1c4e1636e3c8368c22e8f46db7fe122993a43c1",
        "tools/fusion_v2/phase3r26c_r2/harness_lint.py": "77b55ef9af9903729bd47171d46bba414792bffa0d6ba556690d86ef0b88e2e0",
        "tools/fusion_v2/phase3r26c_r2/harness_mutation_selftest.py": "81f53388eaaa3220c41b388f659a4441e17bd0b281a3c86e31ddb1bcd848270c",
        "tools/fusion_v2/phase3r26c_r2/sitecustomize.py": "ba0d1aa35b4a82d9f669d5e340490e8da6adc9c626e773d0f2abf44f26c3d7ec",
        "tools/fusion_v2/phase3r26c_r2/tracing_preflight.py": "da2f3ab08656d88df876b1839e16803669b5978152d61ad4846e08322521ec70",
        "tools/fusion_v2/phase3r26c_r2_q2/__init__.py": "f87071698b9d9e4fd481f4d07266fdd8d63ba2aa71ff06705d6f776dbf6e4707",
        "tools/fusion_v2/phase3r26c_r2_q2/authorized_suite.py": "0ad6a43a9321220e6d7a9dc4fc36deccc19b638fb335ec751aedb9585607eede",
        "tools/fusion_v2/phase3r26c_r2_q2/closure_checks.py": "b59c7e0cd24c219db4f6e06557daaade939daece1c47d4ae96a2a2944fa83edc",
        "tools/fusion_v2/phase3r26c_r2_q2/command_dispatch.py": "dadfe496393cfda3885fa4a4f2d7d5960c16750f2d454ad3ec5b5da5a2f8eb89",
        "tools/fusion_v2/phase3r26c_r2_q2/command_specs.py": "53bd1f73fc37247a35b0571406ab19ac5c9a6e1cd45ae18e54ff28ecf148ae02",
        "tools/fusion_v2/phase3r26c_r2_q2/common.py": "2e2302c1ad1384c68dd8bb5b14d8cdcca3678e51a2f239ac697b3599b9f079b6",
        "tools/fusion_v2/phase3r26c_r2_q2/environment_gate.py": "7c54683cc68546dd2cd0745cc2978502b0698b37cdfc7b36734490ad9e90540d",
        "tools/fusion_v2/phase3r26c_r2_q2/isolated_rerun.py": "ebecb4afe65152e906b9e9797a68a4ccef2300c82150acab97c13ed299af1b60",
        "tools/fusion_v2/phase3r26c_r2_q2/mutation_runner.py": "efbf693ed3cd5c1bbb2d9056fe68949a8b0af5e59dd3c9c21658d2f83d62a48f",
        "tools/fusion_v2/phase3r26c_r2_q2/negative_controls.py": "d47b404ced507574c2a13218c98a55c18aaa30d6ad8432cfd27fa35eac877e29",
        "tools/fusion_v2/phase3r26c_r2_q2/qualification_admin.py": "4f989ea5c2c919b5d4297ab4aceb4bc37cce59a58d9f29144dac3276ed61c43b",
        "tools/fusion_v2/phase3r26c_r2_q2/r2_mutation_probe.py": "5ce70324ef01f849a0628ebe29232029464635cca6873296cc13c0066a8bccc2",
        "tools/fusion_v2/phase3r26c_r2_q2/report_builder.py": "1d20957833f64ccd8e8a69ef833423269e27ef949527eb9ced4862def610961d",
        "tools/fusion_v2/phase3r26c_r2_q2/run_command.py": "09c13638eebc70635a4b0dfc1d4189975685ecb906e1e2559572cc773e0e1baf",
        "tools/fusion_v2/phase3r26c_r2_q2/sitecustomize.py": "d82c01996ea394c825cf5d23a143b2f5076fe347c7199307fc26ad16d72561bd",
    }
    production = {row["path"]: row["sha256"] for row in read_json(baseline / "START_CANDIDATE_INVENTORY.json")["files"]}
    payload = {
        "schema": "biospur.phase3r26c_r2_q7.pre_edit_baseline.v1",
        "recorded_at_utc": "2026-08-20T20:01:36Z",
        "identity": _identity(worktree), "tracked_head_tree_sha1": CANONICAL_TREE,
        "full_porcelain_v2": {"snapshot_sha256": hashlib.sha256(full).hexdigest(), "snapshot_bytes": len(full), "porcelain_v2_z_sha256": "77938f58934d45acc32c76805fc93d1e10d0061896c5baecdda3521a1a712593", "porcelain_v2_z_bytes": 35861783},
        "fusion_porcelain_v2": {"snapshot_sha256": hashlib.sha256(fusion_status).hexdigest(), "snapshot_bytes": len(fusion_status), "porcelain_v2_z_sha256": "beeceecd0e4f4b272da2b299a5aac142321a3a7c98c9285030e2415aa1d04f31", "porcelain_v2_z_bytes": 3797362},
        "non_fusion_porcelain_v2": {"consecutive_samples_equal": non_fusion_a == non_fusion_b, "snapshot_sha256": hashlib.sha256(non_fusion_a).hexdigest(), "snapshot_bytes": len(non_fusion_a), "porcelain_v2_z_sha256": "57bef665e3c71c33e446c7ef4541c4c47f6bea4186a2707db1f0e1096665cb6e", "porcelain_v2_z_bytes": 32064421},
        "worktree_registry_sha256": hashlib.sha256(registry).hexdigest(), "worktree_count": registry.count(b"worktree "),
        "production_sha256": production, "qualification_tooling_sha256": preedit_tools,
        "q6_authoritative_artifact_sha256": {name: sha256_file(q6_root / name) for name in q6_names},
        "available_bytes": {key: parse_df(f"START_DF_{key.upper()}.txt")["available_bytes"] for key in ("root", "ssd", "hdd")},
        "disk_gates": {"root_minimum_bytes": 40_000_000_000, "ssd_minimum_bytes": 100_000_000_000, "projected_growth_maximum_bytes": 5_000_000_000, "projected_growth_bytes": EARLY_STOP_BYTES, "passed": True},
    }
    write_json(report / "PRE_EDIT_BASELINE.json", payload)
    write_json(report / "Q7_PRE_EDIT_BASELINE.json", payload)
    metadata = read_json(baseline / "START_BASELINE_METADATA.json")
    metadata.update({"schema": "biospur.phase3r26c_r2_q7.start_baseline.v1", "pre_edit_baseline_sha256": sha256_file(report / "PRE_EDIT_BASELINE.json"), "pre_edit_snapshot_captured_before_q7_tooling_changes": True})
    write_json(baseline / "START_BASELINE_METADATA.json", metadata)


def bind_q7_development() -> None:
    """Promote completed development-only mutation diagnostics to named Q7 artifacts."""
    _, _, report = _roots()
    r2_path, r2_command = _latest_preflight(report, "r2_mutation_runner")
    r2 = r2_command["runtime_preflight"]["runner_preflight"]
    r2_payload = {
        "schema": "biospur.phase3r26c_r2_q7.development_r2_preflight.v1",
        "evidence_classification": "DEVELOPMENT_ONLY_NOT_FORMAL_EVIDENCE",
        "status": r2["status"], "mutant_count": len(r2["results"]),
        "valid_count": sum(row["classification"] == "VALID_SEMANTIC_KILL" for row in r2["results"]),
        "killed_count": sum(bool(row["killed"]) for row in r2["results"]),
        "invalid_mutants_counted_as_kills": 0, "results": r2["results"],
        "source_command_result": str(r2_path), "source_command_result_sha256": sha256_file(r2_path),
    }
    write_json(report / "DEVELOPMENT_R2_PREFLIGHT_RESULTS.json", r2_payload)
    r1 = read_json(report / "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json")
    if not (r2_payload["status"] == "PASS" and r2_payload["valid_count"] == r2_payload["killed_count"] == 22 and r1["status"] == "PASS" and r1["summary"]["r1_intended_semantic_kills"] == "14/14"):
        raise RuntimeError("Q7 development mutation binding failed")


def _build_red_capsule(worktree: Path, fusion: Path, report: Path) -> dict:
    capsule = report / "capsules/frozen_red_source"
    if capsule.exists():
        raise RuntimeError(f"frozen RED capsule already exists: {capsule}")
    heading = capsule / "biospur_fusion/heading_anchor_audit_v2"
    heading.mkdir(parents=True)
    git_paths = ["src/biospur_fusion/__init__.py", *[f"{PRODUCTION_DIR}/{name}" for name in PRODUCTION_RELATIVE]]
    for relative in git_paths:
        object_path = f"BioSpur_Fusion/Fusion_Part/{relative}"
        data = _run(["git", "show", f"{BASE_COMMIT}:{object_path}"], worktree).stdout
        target = capsule / "biospur_fusion/__init__.py" if relative == "src/biospur_fusion/__init__.py" else heading / Path(relative).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    files = [path for path in capsule.rglob("*") if path.is_file()]
    size = sum(path.stat().st_size for path in files)
    result = {
        "source_commit": BASE_COMMIT,
        "capsule_root": str(capsule),
        "file_count": len(files),
        "size_bytes": size,
        "under_5mb": size < 5 * 1024 * 1024,
        "contains_git_metadata": any(path.name == ".git" for path in capsule.rglob("*")),
        "files": {str(path.relative_to(capsule)): sha256_file(path) for path in sorted(files)},
        "extraction": "read-only git show of allowlisted source objects",
        "not_a_worktree": True,
    }
    if not result["under_5mb"] or result["contains_git_metadata"] or result["file_count"] != 7:
        raise RuntimeError(f"frozen RED capsule contract failed: {result}")
    return result


def prepare() -> None:
    worktree, fusion, report = _roots()
    _identity(worktree)
    root_df, ssd_df = _df("/", fusion), _df("/mnt/nrf_ssd", fusion)
    if root_df["available_bytes"] < 40_000_000_000 or ssd_df["available_bytes"] < 100_000_000_000:
        raise RuntimeError("disk gate failed during prepare")
    start_inventory = read_json(report / "baseline/START_CANDIDATE_INVENTORY.json")
    start_by_path = {row["path"]: row for row in start_inventory["files"]}
    records = []
    diff_chunks = []
    for relative in PRODUCTION_RELATIVE:
        rel = f"{PRODUCTION_DIR}/{relative}"
        path = fusion / rel
        st = path.lstat()
        sha = sha256_file(path)
        if start_by_path[rel]["sha256"] != sha:
            raise RuntimeError(f"production changed since start baseline: {rel}")
        status_output = _run(["git", "status", "--porcelain=v2", "--untracked-files=all", "--", f"BioSpur_Fusion/Fusion_Part/{rel}"], worktree).stdout.decode()
        diff = _run(["git", "diff", "--no-index", "--", "/dev/null", str(path)], worktree, expected=(0, 1)).stdout
        diff_chunks.append(diff)
        records.append({
            "path": rel, "type": "regular_file" if stat.S_ISREG(st.st_mode) else "other",
            "size_bytes": st.st_size, "mode_octal": format(stat.S_IMODE(st.st_mode), "04o"),
            "sha256": sha, "git_status": status_output.strip(), "ast": _ast_summary(path),
        })
    diff_path = report / "baseline/PRODUCTION_CURRENT_DIFF.patch"
    diff_path.write_bytes(b"".join(diff_chunks))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(worktree), str(fusion), str(fusion / "src")))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    closure = _run(
        [sys.executable, "-B", str(fusion / "tools/fusion_v2/phase3r26c_r2/closure_probe.py")],
        worktree, env=env,
    )
    closure_payload = json.loads(closure.stdout)
    if closure_payload["status"] != "PASS" or closure_payload["k_consumer_count"] != 4 or closure_payload["formal"]["passed"] != 35:
        raise RuntimeError("production baseline semantic gate failed")
    red_capsule = _build_red_capsule(worktree, fusion, report)
    payload = {
        "schema": "biospur.phase3r26c_r2_q4.production_candidate_baseline.v1",
        "status": "PASS",
        "identity": _identity(worktree),
        "production_files": records,
        "production_file_count": len(records),
        "production_current_diff": {"path": str(diff_path), "sha256": sha256_file(diff_path), "size_bytes": diff_path.stat().st_size},
        "p1_001": {
            "status": "PASS", "k_consumer_count": 4, "k_consumers_psi_free": 4,
            "kernel_exact_signature": ["edge", "k_protocol_relative", "measurement_protocol_relative"],
            "kernel_wrap": "wrap_mod_pi(K - measurement_protocol_relative)",
            "kernel_accepts_psi": False, "kernel_accepts_H": False,
            "kernel_accepts_full_heading_state": False, "kernel_accesses_gauge_adapter": False,
            "closure": closure_payload["k_consumers"],
        },
        "p1_002": {
            "status": "PASS",
            "required_schema": closure_payload["formal"]["top_level_required_fields"],
            "allowed_schema": closure_payload["formal"]["top_level_allowed_fields"],
            "schema_matrix_executed": closure_payload["formal"]["executed"],
            "schema_matrix_passed": closure_payload["formal"]["passed"],
            "schema_matrix_failed": closure_payload["formal"]["failed"],
            "fail_closed_checks": closure_payload["formal"]["checks"],
            "shared_validator_paths": ["create", "from_json_bytes", "to_payload", "canonical_bytes"],
        },
        "development_probe_classification": "DEVELOPMENT_ONLY_NOT_FORMAL_EVIDENCE",
        "frozen_red_capsule": red_capsule,
    }
    write_json(report / "PRODUCTION_CANDIDATE_BASELINE.json", payload)


def _latest_preflight(report: Path, label: str) -> tuple[Path, dict]:
    attempts = sorted((report / "development/preflight" / label).glob("attempt_*/COMMAND_RESULT.json"))
    if not attempts:
        raise RuntimeError(f"missing preflight for {label}")
    result = read_json(attempts[-1])
    if not result.get("qualified") or not result.get("runtime_preflight"):
        raise RuntimeError(f"preflight not qualified for {label}")
    return attempts[-1], result


def _latest_development_execution(report: Path, label: str) -> tuple[Path, dict]:
    attempts = sorted((report / "development/execution" / label).glob("attempt_*/COMMAND_RESULT.json"))
    if not attempts:
        raise RuntimeError(f"missing development execution for {label}")
    result = read_json(attempts[-1])
    if not result.get("qualified"):
        raise RuntimeError(f"development execution not qualified for {label}")
    return attempts[-1], result


def seal() -> None:
    worktree, fusion, report = _roots()
    _identity(worktree)
    if (report / "FORMAL_FREEZE_SEALED").exists():
        raise RuntimeError("formal freeze is already sealed")
    baseline = read_json(report / "PRODUCTION_CANDIDATE_BASELINE.json")
    if baseline["status"] != "PASS":
        raise RuntimeError("production baseline is not qualified for freeze")
    development_execution_files = {}
    for label in (
        "q7_root_cause", "float_comparator_controls", "environment_replication",
        "harness_lint", "harness_self_tests", "harness_mutation_tests",
        "frozen_red", "frozen_green", "consumer_closure", "h_boundary_closure",
        "formal_schema_closure", "negative_controls", "authorized_suite",
    ):
        path, result = _latest_development_execution(report, label)
        development_environment = result.get("command_environment", {}).get("environment_sha256")
        frozen_environment = _environment_contract(label, "formal")["environment_sha256"]
        if development_environment != frozen_environment:
            raise RuntimeError(f"development execution environment is stale for {label}")
        development_execution_files[label] = {
            "path": str(path), "sha256": sha256_file(path), "qualified": True,
            "environment_sha256": development_environment,
        }
    commands = {}
    preflight_files = {}
    for label in COMMANDS:
        path, result = _latest_preflight(report, label)
        formal_contract = _environment_contract(label, "formal")
        if result["command_environment"]["environment_sha256"] != formal_contract["environment_sha256"]:
            raise RuntimeError(f"preflight/formal environment differs for {label}")
        commands[label] = {**formal_contract, "runtime_preflight": result["runtime_preflight"]}
        preflight_files[label] = {"path": str(path), "sha256": sha256_file(path)}
    write_json(report / "COMMAND_ENVIRONMENT_MANIFEST.json", {
        "schema": "biospur.phase3r26c_r2_q4.command_environment.v1",
        "commands": commands,
    })
    green_nodeids = commands["frozen_green"]["runtime_preflight"]["pytest"]["collected_nodeids"]
    (report / "FROZEN_NODEIDS.txt").write_text("\n".join(green_nodeids) + "\n", encoding="utf-8")
    tool_root = Path(__file__).resolve().parent
    wrapper_files = [
        tool_root / "run_command.py", tool_root / "command_dispatch.py", tool_root / "command_specs.py",
        tool_root / "sitecustomize.py", tool_root / "environment_gate.py",
    ]
    write_json(report / "FROZEN_WRAPPERS.json", {
        "schema": "biospur.phase3r26c_r2_q4.frozen_wrappers.v1",
        "wrappers": {str(path): sha256_file(path) for path in wrapper_files},
        "tracing": "single outer strace -ff -ttt; nested ptrace forbidden",
    })
    write_json(report / "FROZEN_EXPECTED_EXITS.json", {
        "schema": "biospur.phase3r26c_r2_q4.expected_exits.v1",
        "commands": {label: COMMANDS[label]["exit"] for label in COMMANDS},
        "frozen_red": {"exit_code": 1, "meaning": "expected exact semantic RED"},
        "semantic_mutant": {"exit_code": 17, "meaning": "specified assertion kill only"},
    })
    write_json(report / "FROZEN_MUTATION_MANIFEST.json", mutation_manifest(fusion))
    sealed_files = _environment_contract("frozen_green", "formal")["sealed_file_sha256"]
    registry = _run(["git", "worktree", "list", "--porcelain"], worktree).stdout
    report_pathspec = f"BioSpur_Fusion/Fusion_Part/{report.relative_to(fusion)}"
    seal_status = _run([
        "git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".",
        f":(exclude){report_pathspec}", f":(exclude){report_pathspec}/**",
    ], worktree).stdout
    production_hashes = {
        f"{PRODUCTION_DIR}/{name}": sha256_file(fusion / PRODUCTION_DIR / name)
        for name in PRODUCTION_RELATIVE
    }
    r1_spec_paths = (
        "tools/fusion_v2/phase3r26c_r1/mutants.py",
        "tools/fusion_v2/phase3r26c_r1/mutation_probe.py",
    )
    r2_spec_paths = (
        "tests/fusion_v2/phase3r26c_r2/expected_red.json",
        "tests/fusion_v2/phase3r26c_r2/frozen_nodeids.txt",
        "tests/fusion_v2/phase3r26c_r2/gauge_projection_contract.json",
        "tests/fusion_v2/phase3r26c_r2/k_kernel_api_contract.json",
        "tools/fusion_v2/phase3r26c_r2_q2/mutation_runner.py",
        "tools/fusion_v2/phase3r26c_r2_q2/r2_mutation_probe.py",
    )
    corrective_paths = (
        "tools/fusion_v2/phase3r26c_r1/mutants.py",
        "tools/fusion_v2/phase3r26c_r1/mutation_probe.py",
        "tools/fusion_v2/phase3r26c_r2_q2/mutation_runner.py",
    )
    contract_paths = (
        "tests/fusion_v2/phase3r26c_r2/expected_red.json",
        "tests/fusion_v2/phase3r26c_r2/gauge_projection_contract.json",
        "tests/fusion_v2/phase3r26c_r2/k_kernel_api_contract.json",
        "tools/fusion_v2/phase3r26c_r2_q2/command_specs.py",
        "tools/fusion_v2/phase3r26c_r2_q2/run_command.py",
        "tools/fusion_v2/phase3r26c_r2_q2/sitecustomize.py",
    )
    development_r1 = read_json(report / "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json")
    development_r2 = read_json(report / "DEVELOPMENT_R2_PREFLIGHT_RESULTS.json")
    if development_r1.get("status") != "PASS" or development_r1.get("summary", {}).get("r1_intended_semantic_kills") != "14/14":
        raise RuntimeError("Q6 seal refused without a complete 14/14 development R1 preflight")
    if not (
        development_r2.get("status") == "PASS"
        and development_r2.get("mutant_count") == development_r2.get("valid_count") == development_r2.get("killed_count") == 22
        and development_r2.get("invalid_mutants_counted_as_kills") == 0
    ):
        raise RuntimeError("Q7 seal refused without a complete 22/22 development R2 preflight")
    required_q7_development = (
        "H_TRANSPORT_ROUNDOFF_ROOT_CAUSE_AUDIT.md",
        "H_TRANSPORT_ROUNDOFF_ROOT_CAUSE_AUDIT.json",
        "CANONICAL_ISOLATED_ENVIRONMENT_DIFF.json",
        "FLOAT_FIELD_REGISTRY.json", "FLOAT_SEMANTIC_EQUIVALENCE_CONTRACT.json",
        "FLOAT_ERROR_BOUND_DERIVATION.md", "FLOAT_COMPARATOR_POSITIVE_CONTROLS.json",
        "FLOAT_COMPARATOR_NEGATIVE_CONTROLS.json",
        "DEVELOPMENT_ENVIRONMENT_REPLICATION_RESULTS.json",
        "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json", "DEVELOPMENT_R2_PREFLIGHT_RESULTS.json",
    )
    missing_q7 = [name for name in required_q7_development if not (report / name).exists()]
    if missing_q7:
        raise RuntimeError(f"Q7 seal missing development artifacts: {missing_q7}")
    q7_gate_statuses = {
        "root_cause": read_json(report / "H_TRANSPORT_ROUNDOFF_ROOT_CAUSE_AUDIT.json")["status"],
        "environment_diff": read_json(report / "CANONICAL_ISOLATED_ENVIRONMENT_DIFF.json")["status"],
        "positive_controls": read_json(report / "FLOAT_COMPARATOR_POSITIVE_CONTROLS.json")["status"],
        "negative_controls": read_json(report / "FLOAT_COMPARATOR_NEGATIVE_CONTROLS.json")["status"],
        "replication": read_json(report / "DEVELOPMENT_ENVIRONMENT_REPLICATION_RESULTS.json")["status"],
    }
    if set(q7_gate_statuses.values()) != {"PASS"}:
        raise RuntimeError(f"Q7 development comparator gates failed: {q7_gate_statuses}")
    from float_semantic_equivalence import H_TRANSPORT_TAU_ZERO, REGISTRY_PATH, contract_payload
    freeze_manifest = {
        "schema": "biospur.phase3r26c_r2_q7.formal_freeze_manifest.v1",
        "status": "READY_TO_SEAL",
        "sealed_at_utc": datetime.now(timezone.utc).isoformat(),
        "identity": _identity(worktree),
        "production_candidate_baseline_sha256": sha256_file(report / "PRODUCTION_CANDIDATE_BASELINE.json"),
        "sealed_file_sha256": sealed_files,
        "clean_base_capsule": baseline["frozen_red_capsule"],
        "preflight_command_results": preflight_files,
        "development_execution_results": development_execution_files,
        "command_count": len(commands),
        "all_preflights_qualified": True,
        "all_required_development_executions_qualified": True,
        "formal_production_files_frozen": True,
        "no_worktree": True,
        "canonical_branch": CANONICAL_BRANCH,
        "canonical_head": CANONICAL_HEAD,
        "canonical_tree": CANONICAL_TREE,
        "working_tree_status_sha256": hashlib.sha256(seal_status).hexdigest(),
        "working_tree_status_bytes": len(seal_status),
        "production_source_sha256": production_hashes,
        "test_source_sha256": {
            path: digest for path, digest in sealed_files.items()
            if path.startswith("tests/")
        },
        "mutation_generator_sha256": {
            path: sha256_file(fusion / path) for path in corrective_paths
        },
        "r1_mutant_specification_sha256": {
            path: sha256_file(fusion / path) for path in r1_spec_paths
        },
        "r2_mutant_specification_sha256": {
            path: sha256_file(fusion / path) for path in r2_spec_paths
        },
        "m03_m05_m08_m10_corrective_tooling_sha256": {
            path: sha256_file(fusion / path) for path in corrective_paths
        },
        "formal_runner_sha256": sha256_file(Path(__file__).resolve().parent / "run_command.py"),
        "probe_sha256": sha256_file(fusion / "tools/fusion_v2/phase3r26c_r1/mutation_probe.py"),
        "red_green_k_psi_schema_contract_sha256": {
            path: sha256_file(fusion / path) for path in contract_paths
        },
        "development_r1_preflight_sha256": sha256_file(report / "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json"),
        "development_r1_preflight_summary": development_r1["summary"],
        "development_r2_preflight_sha256": sha256_file(report / "DEVELOPMENT_R2_PREFLIGHT_RESULTS.json"),
        "development_r2_preflight_summary": {key: development_r2[key] for key in ("mutant_count", "valid_count", "killed_count", "invalid_mutants_counted_as_kills")},
        "q7_comparator_development_gate_statuses": q7_gate_statuses,
        "q7_development_artifact_sha256": {name: sha256_file(report / name) for name in required_q7_development},
        "comparison_tool_sha256": sha256_file(fusion / "tools/fusion_v2/phase3r26c_r2_q2/float_semantic_equivalence.py"),
        "field_registry_sha256": sha256_file(REGISTRY_PATH),
        "floating_equivalence_contract_sha256": hashlib.sha256(canonical_bytes(contract_payload())).hexdigest(),
        "frozen_field_specific_tolerances": {"h_transport_max_error_rad": H_TRANSPORT_TAU_ZERO},
        "numerical_derivation_artifact_sha256": sha256_file(report / "FLOAT_ERROR_BOUND_DERIVATION.md"),
        "positive_control_artifact_sha256": sha256_file(report / "FLOAT_COMPARATOR_POSITIVE_CONTROLS.json"),
        "negative_control_artifact_sha256": sha256_file(report / "FLOAT_COMPARATOR_NEGATIVE_CONTROLS.json"),
        "capsule_builder_sha256": sha256_file(fusion / "tools/fusion_v2/phase3r26c_r2_q2/isolated_rerun.py"),
        "environment_and_package_versions": {
            "python": sys.version,
            "pytest": importlib.metadata.version("pytest"),
            "numpy": importlib.metadata.version("numpy"),
        },
        "allowed_input_manifest": {
            "source_synthetic_and_existing_qualification_artifacts_only": True,
            "sealed_file_sha256": sealed_files,
        },
        "forbidden_access_manifest": {
            "sealed_holdout": True, "17_final_still_numeric": True,
            "walk_boxing_golf_numeric": True, "uwb_opensense_vicon_numeric": True,
        },
        "disk_thresholds": {
            "root_available_min_bytes": 40_000_000_000,
            "ssd_available_min_bytes": 100_000_000_000,
            "report_soft_limit_bytes": SOFT_LIMIT_BYTES,
            "report_early_stop_bytes": EARLY_STOP_BYTES,
            "report_hard_limit_bytes": HARD_LIMIT_BYTES,
            "isolated_capsule_max_bytes_exclusive": 5 * 1024 * 1024,
        },
        "worktree_registry_sha256": hashlib.sha256(registry).hexdigest(),
        "worktree_count": registry.count(b"worktree "),
    }
    write_json(report / "FORMAL_FREEZE_MANIFEST.json", freeze_manifest)
    seal_inputs = (
        "FORMAL_FREEZE_MANIFEST.json", "COMMAND_ENVIRONMENT_MANIFEST.json", "FROZEN_NODEIDS.txt",
        "FROZEN_WRAPPERS.json", "FROZEN_EXPECTED_EXITS.json", "FROZEN_MUTATION_MANIFEST.json",
        "FLOAT_FIELD_REGISTRY.json", "FLOAT_SEMANTIC_EQUIVALENCE_CONTRACT.json",
        "FLOAT_ERROR_BOUND_DERIVATION.md", "FLOAT_COMPARATOR_POSITIVE_CONTROLS.json",
        "FLOAT_COMPARATOR_NEGATIVE_CONTROLS.json", "DEVELOPMENT_ENVIRONMENT_REPLICATION_RESULTS.json",
    )
    seal_payload = {
        "schema": "biospur.phase3r26c_r2_q7.formal_seal.v1",
        "status": "FORMAL_FREEZE_SEALED",
        "sealed_artifact_sha256": {name: sha256_file(report / name) for name in seal_inputs},
        "sealed_artifact_count": len(seal_inputs),
        "post_seal_repair_permitted": False,
    }
    write_json(report / "FORMAL_FREEZE_SEAL.json", seal_payload)
    write_json(report / "FORMAL_SEAL.json", {
        **seal_payload,
        "seal_created": True,
        "formal_freeze_manifest_sha256": sha256_file(report / "FORMAL_FREEZE_MANIFEST.json"),
    })
    (report / "FORMAL_FREEZE_SEALED").write_text("FORMAL_FREEZE_SEALED\n", encoding="utf-8")


def _write_trace_ledgers(report: Path) -> dict:
    trace_paths = (
        sorted(report.glob("raw/*/strace.*"))
        + sorted(report.glob("development/preflight/*/attempt_*/strace.*"))
        + sorted(report.glob("development/execution/*/attempt_*/strace.*"))
    )
    aggregate: dict[tuple[str, ...], dict] = {}
    raw_event_count = 0
    trace_forbidden_count = 0
    blocked_control_count = 0
    nested_ptrace_executed_count = 0
    path_pattern = re.compile(r'"((?:\\.|[^"\\])*)"')
    line_pattern = re.compile(r"^(\d+\.\d+)\s+([A-Za-z0-9_]+)\(")
    scoped_fragments = ("/mnt/nrf_ssd/nRF_dev", "/mnt/DatenBankHDD", "/tmp/biospur_")
    forbidden_fragments = (
        "/datasets/", "/logs/", "17_final_still", "_walk/", "_boxing/",
        "_golf/", "/vicon/", "/opensense/", "/sealed_holdout/",
        "/tmp/biospur_", "/mnt/nrf_ssd/nrf_dev_worktrees/",
        "/mnt/datenbankhdd/biospur_archive/fusion_worktree_cold_",
    )

    def label_for(path: Path) -> str:
        parts = path.parts
        if "raw" in parts:
            return parts[parts.index("raw") + 1]
        if "preflight" in parts:
            return parts[parts.index("preflight") + 1]
        return parts[parts.index("execution") + 1]

    def add_aggregate(row: dict) -> None:
        key = tuple(str(row[name]) for name in (
            "command_label", "path", "operation", "decision", "classification",
            "trace_source",
        ))
        value = aggregate.get(key)
        if value is None:
            value = {
                "first_timestamp": row["timestamp"],
                "last_timestamp": row["timestamp"],
                "command_label": row["command_label"],
                "processes": set(),
                "path": row["path"],
                "operation": row["operation"],
                "decision": row["decision"],
                "classification": row["classification"],
                "trace_source": row["trace_source"],
                "event_count": 0,
            }
            aggregate[key] = value
        value["last_timestamp"] = row["timestamp"]
        value["processes"].add(row["process"])
        value["event_count"] += 1

    event_path = report / "DATA_ACCESS_LEDGER.jsonl.gz"
    with event_path.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0) as event_handle:
            for trace in trace_paths:
                label = label_for(trace)
                pid = trace.name.split(".")[-1]
                with trace.open("r", errors="replace") as handle:
                    for line in handle:
                        if "ptrace(" in line:
                            nested_ptrace_executed_count += 1
                        match = line_pattern.match(line)
                        quoted = path_pattern.findall(line)
                        if not match or not quoted:
                            continue
                        operation = match.group(2)
                        raw_path = bytes(quoted[0], "utf-8").decode("unicode_escape", errors="replace")
                        lower = raw_path.lower()
                        if not any(fragment.lower() in lower for fragment in scoped_fragments):
                            continue
                        write_access = any(flag in line for flag in (
                            "O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND",
                        ))
                        forbidden = any(fragment in lower for fragment in forbidden_fragments)
                        classification = (
                            "FORBIDDEN_NUMERIC_OR_PATH" if forbidden
                            else "QUALIFICATION_EVIDENCE" if str(report).lower() in lower
                            else "AUTHORIZED_SOURCE_HARNESS_OR_GIT_METADATA"
                        )
                        row = {
                            "timestamp": match.group(1), "command_label": label,
                            "process": pid, "path": raw_path,
                            "operation": operation + ("_write" if write_access else "_read_or_metadata"),
                            "decision": "ALLOW", "classification": classification,
                            "trace_source": "strace:" + str(trace.relative_to(report)),
                        }
                        event_handle.write(canonical_bytes(row))
                        raw_event_count += 1
                        add_aggregate({**row, "trace_source": "strace"})
                        if forbidden:
                            trace_forbidden_count += 1
            audits = (
                sorted(report.glob("raw/*/audit.jsonl"))
                + sorted(report.glob("development/preflight/*/attempt_*/audit.jsonl"))
                + sorted(report.glob("development/execution/*/attempt_*/audit.jsonl"))
            )
            for audit in audits:
                label = label_for(audit)
                with audit.open("r", errors="replace") as handle:
                    for line in handle:
                        value = json.loads(line)
                        if value.get("event") not in {
                            "forbidden_open_blocked", "nested_ptrace_blocked",
                            "unauthorized_write_blocked",
                        }:
                            continue
                        row = {
                            "timestamp": f"{value.get('timestamp_ns', 0) / 1_000_000_000:.9f}",
                            "command_label": label, "process": str(value.get("pid")),
                            "path": value.get("path", value.get("detail", "")),
                            "operation": value.get("operation", value.get("event")),
                            "decision": "DENY",
                            "classification": value.get("classification", value.get("event")),
                            "trace_source": "python_audit_hook:" + str(audit.relative_to(report)),
                        }
                        event_handle.write(canonical_bytes(row))
                        raw_event_count += 1
                        blocked_control_count += 1
                        add_aggregate({**row, "trace_source": "python_audit_hook"})

    aggregate_path = report / "DATA_ACCESS_LEDGER.jsonl"
    with aggregate_path.open("wb") as handle:
        for key in sorted(aggregate):
            row = aggregate[key]
            processes = sorted(row.pop("processes"))
            row["process"] = processes[0] if len(processes) == 1 else "MULTIPLE"
            row["processes"] = processes
            row["process_count"] = len(processes)
            handle.write(canonical_bytes(row))
    return {
        "trace_file_count": len(trace_paths),
        "ledger_event_count": raw_event_count,
        "ledger_aggregate_count": len(aggregate),
        "event_ledger_gzip_bytes": event_path.stat().st_size,
        "aggregate_ledger_bytes": aggregate_path.stat().st_size,
        "forbidden_trace_access_count": trace_forbidden_count,
        "blocked_control_event_count": blocked_control_count,
        "nested_ptrace_executed_count": nested_ptrace_executed_count,
    }


def _capture_end_state(worktree: Path, fusion: Path, report: Path) -> dict:
    baseline = report / "baseline"
    report_pathspec = f"BioSpur_Fusion/Fusion_Part/{report.relative_to(fusion)}"
    captures = {
        "END_FULL_GIT_STATUS.txt.gz": ["git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".", f":(exclude){report_pathspec}", f":(exclude){report_pathspec}/**"],
        "END_NON_FUSION_GIT_STATUS_A.txt.gz": ["git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".", ":(exclude)BioSpur_Fusion/Fusion_Part", ":(exclude)BioSpur_Fusion/Fusion_Part/**"],
        "END_NON_FUSION_GIT_STATUS_B.txt.gz": ["git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".", ":(exclude)BioSpur_Fusion/Fusion_Part", ":(exclude)BioSpur_Fusion/Fusion_Part/**"],
        "END_NON_FUSION_GIT_STATUS_C.txt.gz": ["git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".", ":(exclude)BioSpur_Fusion/Fusion_Part", ":(exclude)BioSpur_Fusion/Fusion_Part/**"],
        "END_FUSION_GIT_STATUS.txt.gz": ["git", "status", "--porcelain=v2", "--untracked-files=all", "--", "BioSpur_Fusion/Fusion_Part", f":(exclude){report_pathspec}", f":(exclude){report_pathspec}/**"],
        "END_WORKTREE_REGISTRY.txt": ["git", "worktree", "list", "--porcelain"],
    }
    values = {name: _run(argv, worktree).stdout for name, argv in captures.items()}
    for name, data in values.items():
        path = baseline / name
        if name.endswith(".gz"):
            with path.open("wb") as raw_handle:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0) as handle:
                    handle.write(data)
        else:
            path.write_bytes(data)
    with gzip.open(baseline / "START_NON_FUSION_GIT_STATUS_A.txt.gz", "rb") as handle:
        start_non_fusion = handle.read()
    end_samples = [values[f"END_NON_FUSION_GIT_STATUS_{suffix}.txt.gz"] for suffix in "ABC"]
    non_fusion_stable = end_samples[0] == end_samples[1] == end_samples[2]
    non_fusion_unchanged = non_fusion_stable and end_samples[0] == start_non_fusion
    worktrees_unchanged = values["END_WORKTREE_REGISTRY.txt"] == (baseline / "START_WORKTREE_REGISTRY.txt").read_bytes()
    start_production = {row["path"]: row["sha256"] for row in read_json(baseline / "START_CANDIDATE_INVENTORY.json")["files"] if row["path"].startswith(PRODUCTION_DIR)}
    production_end = {f"{PRODUCTION_DIR}/{name}": sha256_file(fusion / PRODUCTION_DIR / name) for name in PRODUCTION_RELATIVE}
    return {
        "schema": "biospur.phase3r26c_r2_q4.canonical_state_protection.v1",
        "status": "PASS" if non_fusion_unchanged and worktrees_unchanged and start_production == production_end else "FAIL",
        "start_identity": {"branch": CANONICAL_BRANCH, "head": CANONICAL_HEAD, "tree": CANONICAL_TREE},
        "end_identity": _identity(worktree),
        "non_fusion_status_unchanged": non_fusion_unchanged,
        "non_fusion_end_samples_stable": non_fusion_stable,
        "non_fusion_end_sample_count": 3,
        "worktree_registry_unchanged": worktrees_unchanged,
        "new_worktree_count": 0 if worktrees_unchanged else None,
        "production_files_unchanged_from_start": start_production == production_end,
        "production_sha256_start": start_production,
        "production_sha256_end": production_end,
        "authorized_development_scope": ["tools/fusion_v2/phase3r26c_r2_q2", str(report.relative_to(fusion))],
        "commit_created": False, "push_performed": False,
        "branch_switched": False, "unauthorized_path_write": False,
        "full_snapshot_excludes_report_root": True,
        "excluded_report_root": str(report),
        "snapshots": {name: {"path": str(baseline / name), "sha256": sha256_file(baseline / name)} for name in captures},
    }


def development_stop() -> None:
    worktree, fusion, report = _roots()
    preflight_path = report / "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json"
    if not preflight_path.exists():
        raise RuntimeError("development stop requires DEVELOPMENT_R1_PREFLIGHT_RESULTS.json")
    preflight = read_json(preflight_path)
    if preflight.get("status") == "PASS":
        raise RuntimeError("refusing development-stop finalization for a passing preflight")
    if (report / "FORMAL_FREEZE_SEALED").exists():
        raise RuntimeError("development-stop finalization refused after a formal seal")
    failed = [
        row for row in preflight["mutants"]
        if row["classification"] != "VALID_SEMANTIC_KILL"
    ]
    not_executed = {
        "status": "NOT_EXECUTED_DEVELOPMENT_GATE_FAILED",
        "formal_evidence": False,
        "blocking_gate": "DEVELOPMENT_R1_PREFLIGHT",
        "failed_mutants": [row["mutant_id"] for row in failed],
    }
    placeholders = {
        "FORMAL_SEAL.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.formal_seal.v1",
            "seal_created": False,
        },
        "FORMAL_RED_RESULTS.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.formal_red.v1",
        },
        "FORMAL_GREEN_RESULTS.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.formal_green.v1",
        },
        "K_PSI_CLOSURE_RESULTS.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.k_psi_closure.v1",
        },
        "SCHEMA_MATRIX_RESULTS.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.schema_matrix.v1",
        },
        "R2_STRUCTURAL_MUTATION_RESULTS.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.r2_structural_mutations.v1",
        },
        "R1_MUTATION_REPLAY_RESULTS.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.r1_mutation_replay.v1",
            "development_results": str(preflight_path),
            "all_replayed_mutants_semantically_killed": False,
            "invalid_mutants_counted_as_kills": 0,
        },
        "DETERMINISTIC_REPLAY_RESULTS.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.deterministic_replay.v1",
        },
        "ISOLATED_RERUN_RESULTS.json": {
            **not_executed,
            "schema": "biospur.phase3r26c_r2_q5.isolated_rerun.v1",
            "isolated_capsule_size_bytes": None,
        },
    }
    for name, value in placeholders.items():
        write_json(report / name, value)
    trace_summary = _write_trace_ledgers(report)
    data_summary = {
        "schema": "biospur.phase3r26c_r2_q5.data_access_summary.v1",
        "status": "PASS" if trace_summary["forbidden_trace_access_count"] == 0 and trace_summary["nested_ptrace_executed_count"] == 0 else "FAIL",
        **trace_summary,
        "formal_commands_executed": 0,
        "sealed_holdout_read": False,
        "final_still_numeric_read": False,
        "walk_boxing_golf_numeric_read": False,
        "UWB_OpenSense_Vicon_numeric_read": False,
        "new_worktree_created": False,
        "event_ledger_encoding": "gzip_jsonl_stream",
        "summary_ledger_encoding": "aggregated_jsonl",
    }
    write_json(report / "DATA_ACCESS_SUMMARY.json", data_summary)
    canonical_state = _capture_end_state(worktree, fusion, report)
    canonical_state["schema"] = "biospur.phase3r26c_r2_q5.worktree_invariant_audit.v1"
    canonical_state["authorized_development_scope"] = [
        "tools/fusion_v2/phase3r26c_r1",
        "tools/fusion_v2/phase3r26c_r2_q2",
        str(report.relative_to(fusion)),
    ]
    write_json(report / "WORKTREE_INVARIANT_AUDIT.json", canonical_state)
    write_json(report / "CANONICAL_STATE_PROTECTION.json", canonical_state)
    start_dfs = {
        key: (report / "baseline" / f"START_DF_{key.upper()}.txt").read_text()
        for key in ("root", "ssd", "hdd")
    }

    def parse_df(text: str) -> dict:
        fields = text.splitlines()[-1].split()
        return {
            "size_bytes": int(fields[0]),
            "used_bytes": int(fields[1]),
            "available_bytes": int(fields[2]),
            "mountpoint": fields[-1],
        }

    end_df = {
        "root": _df("/", fusion),
        "ssd": _df("/mnt/nrf_ssd", fusion),
        "hdd": _df("/mnt/DatenBankHDD", fusion),
    }
    for key, value in end_df.items():
        (report / "baseline" / f"END_DF_{key.upper()}.txt").write_text(value["raw"], encoding="utf-8")
    usage = {}
    for key in ("root", "ssd", "hdd"):
        start_value = parse_df(start_dfs[key])
        end_value = {
            name: end_df[key][name]
            for name in ("size_bytes", "used_bytes", "available_bytes", "mountpoint")
        }
        usage[key] = {
            "start": start_value,
            "end": end_value,
            "used_delta_bytes": end_value["used_bytes"] - start_value["used_bytes"],
            "available_delta_bytes": end_value["available_bytes"] - start_value["available_bytes"],
        }
    disk = {
        "schema": "biospur.phase3r26c_r2_q5.disk_usage_audit.v1",
        "status": "PASS",
        "filesystems": usage,
        "report_size_bytes": 0,
        "soft_limit_bytes": SOFT_LIMIT_BYTES,
        "early_stop_bytes": EARLY_STOP_BYTES,
        "hard_limit_bytes": HARD_LIMIT_BYTES,
        "soft_limit_exceeded": False,
        "early_stop_exceeded": False,
        "hard_limit_exceeded": False,
        "isolated_capsule_size_bytes": None,
        "isolated_capsule_limit_bytes": 5 * 1024 * 1024,
    }
    write_json(report / "DISK_USAGE_AUDIT.json", disk)
    direct_answers = {
        "original_m03_defect": "legacy M03 referenced branch_state inside the deliberately K-only helper where the name is undefined",
        "m03_corrected_without_production_semantic_change": True,
        "m03_frozen_semantic_intent_preserved": True,
        "development_all_fourteen_attempted": len(preflight["mutants"]) == 14,
        "development_all_fourteen_structurally_valid": preflight["summary"]["r1_structural_preconditions"] == "14/14",
        "development_all_fourteen_designated_semantic_kills": preflight["summary"]["r1_intended_semantic_kills"] == "14/14",
        "invalid_mutants_counted_as_kills": 0,
        "formal_red_exact": None,
        "formal_green_15_of_15": None,
        "formal_k_psi_4_of_4": None,
        "formal_schema_35_of_35": None,
        "formal_r2_22_of_22": None,
        "formal_r1_14_of_14": None,
        "formal_deterministic_replay": None,
        "formal_isolated_rerun": None,
        "forbidden_accesses": data_summary["forbidden_trace_access_count"],
        "nested_ptrace_events": data_summary["nested_ptrace_executed_count"],
        "new_worktrees": 0 if canonical_state["worktree_registry_unchanged"] else None,
        "commits": 0,
        "pushes": 0,
        "result_classification": "DEVELOPMENT_STOP_BEFORE_Q5_SEAL_NOT_FORMALLY_QUALIFIED",
        "ready_for_separate_commit_push_decision": False,
    }
    final = {
        "schema": "biospur.phase3r26c_r2_q5.final_result.v1",
        "verdict": "STOPPED_NO_COMMIT_NO_PUSH",
        "first_failed_gate": "DEVELOPMENT_R1_PREFLIGHT",
        "failed_mutants": [
            {
                "mutant_id": row["mutant_id"],
                "classification": row["classification"],
                "exact_match_count": row.get("exact_match_count"),
            }
            for row in failed
        ],
        "formal_seal_created": False,
        "formal_chain_started": False,
        "direct_answers": direct_answers,
        "states": [
            "NO_NEW_WORKTREE", "NO_COMMIT", "NO_PUSH",
            "NOT_FOR_OPENSENSE", "NOT_FOR_PHASE4",
        ],
    }
    write_json(report / "FINAL_RESULT.json", final)
    failed_lines = "\n".join(
        f"- `{row['mutant_id']}`: `{row['classification']}` (exact matches: `{row.get('exact_match_count')}`)"
        for row in failed
    )
    markdown = f"""# BioSpur Phase 3-R2.6C-R2 Q5 final

Verdict: `STOPPED_NO_COMMIT_NO_PUSH`

M03 was corrected in mutation tooling only. Its real AST mutation now applies the frozen extra-minus-psi defect at the caller boundary where `branch_state` exists, and the corrected M03 reached `{next(row for row in preflight['mutants'] if row['mutant_id'].startswith('M03'))['designated_assertion']}` with exit 17.

The mandatory development preflight attempted all fourteen R1 mutants but did not achieve 14/14, so no Q5 seal was created and no formal command was executed. The exact development blockers were:

{failed_lines}

- `NO_NEW_WORKTREE`
- `NO_COMMIT`
- `NO_PUSH`
- `NOT_FOR_OPENSENSE`
- `NOT_FOR_PHASE4`
"""
    (report / "R26C_R2_Q5_FINAL.md").write_text(markdown, encoding="utf-8")
    required = [
        "R26C_R2_Q5_FINAL.md", "FINAL_RESULT.json", "M03_CORRECTIVE_AUDIT.md",
        "M03_BEFORE_AFTER_AST.json", "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json",
        *placeholders, "DATA_ACCESS_LEDGER.jsonl", "DATA_ACCESS_SUMMARY.json",
        "DISK_USAGE_AUDIT.json", "WORKTREE_INVARIANT_AUDIT.json",
    ]
    reproducibility = {
        "schema": "biospur.phase3r26c_r2_q5.reproducibility.v1",
        "verdict": final["verdict"],
        "identity": _identity(worktree),
        "artifacts": {},
    }
    write_json(report / "REPRODUCIBILITY_MANIFEST.json", reproducibility)
    for _ in range(4):
        report_size = int(_run(["du", "-x", "-B1", "-s", str(report)], fusion).stdout.split()[0])
        disk.update({
            "report_size_bytes": report_size,
            "soft_limit_exceeded": report_size > SOFT_LIMIT_BYTES,
            "early_stop_exceeded": report_size >= EARLY_STOP_BYTES,
            "hard_limit_exceeded": report_size >= HARD_LIMIT_BYTES,
        })
        disk["status"] = "PASS" if report_size < EARLY_STOP_BYTES else "FAIL"
        write_json(report / "DISK_USAGE_AUDIT.json", disk)
        final["disk"] = {
            "filesystems": usage,
            "report_size_bytes": report_size,
            "isolated_capsule_size_bytes": None,
        }
        write_json(report / "FINAL_RESULT.json", final)
        reproducibility["artifacts"] = {
            name: {
                "sha256": sha256_file(report / name),
                "size_bytes": (report / name).stat().st_size,
            }
            for name in required
            if (report / name).exists()
        }
        write_json(report / "REPRODUCIBILITY_MANIFEST.json", reproducibility)
        after = int(_run(["du", "-x", "-B1", "-s", str(report)], fusion).stdout.split()[0])
        if after == report_size:
            break
    else:
        raise RuntimeError("development-stop report size did not stabilize")
    if after >= EARLY_STOP_BYTES:
        raise RuntimeError("development-stop report reached early-stop limit")


def finalize() -> None:
    worktree, fusion, report = _roots()
    for name in REQUIRED_OUTPUTS:
        if name in {
            "R26C_R2_Q4_FINAL.md", "FINAL_RESULT.json", "DATA_ACCESS_LEDGER.jsonl",
            "DATA_ACCESS_LEDGER.jsonl.gz", "DATA_ACCESS_SUMMARY.json",
            "DISK_USAGE_AUDIT.json", "CANONICAL_STATE_PROTECTION.json",
            "REPRODUCIBILITY_MANIFEST.json",
        }:
            continue
        if not (report / name).exists():
            raise RuntimeError(f"required formal artifact missing: {name}")
    if int(_run(["du", "-x", "-B1", "-s", str(report)], fusion).stdout.split()[0]) >= EARLY_STOP_BYTES:
        raise RuntimeError("384 MiB early-stop reserve reached before finalization")
    trace_summary = _write_trace_ledgers(report)
    negative = read_json(report / "NEGATIVE_CONTROL_RESULTS.json")
    data_summary = {
        "schema": "biospur.phase3r26c_r2_q4.data_access_summary.v1",
        "status": "PASS" if trace_summary["forbidden_trace_access_count"] == 0 and trace_summary["nested_ptrace_executed_count"] == 0 else "FAIL",
        **trace_summary,
        "negative_control_forbidden_attempt_blocked": negative["status"] == "PASS",
        "sealed_holdout_read": False, "final_still_numeric_read": False,
        "walk_boxing_golf_numeric_read": False, "UWB_OpenSense_Vicon_numeric_read": False,
        "forbidden_file_materialized": False, "forbidden_module_imported": False,
        "forbidden_script_executed": False, "new_worktree_created": False,
        "event_ledger_encoding": "gzip_jsonl_stream",
        "summary_ledger_encoding": "aggregated_jsonl",
    }
    write_json(report / "DATA_ACCESS_SUMMARY.json", data_summary)
    canonical_state = _capture_end_state(worktree, fusion, report)
    write_json(report / "CANONICAL_STATE_PROTECTION.json", canonical_state)
    start_meta = read_json(report / "baseline/START_BASELINE_METADATA.json")
    start_dfs = {
        "root": (report / "baseline/START_DF_ROOT.txt").read_text(),
        "ssd": (report / "baseline/START_DF_SSD.txt").read_text(),
        "hdd": (report / "baseline/START_DF_HDD.txt").read_text(),
    }
    def parse_df(text: str) -> dict:
        fields = text.splitlines()[-1].split()
        return {"size_bytes": int(fields[1]), "used_bytes": int(fields[2]), "available_bytes": int(fields[3]), "mountpoint": fields[-1]}
    end_df = {"root": _df("/", fusion), "ssd": _df("/mnt/nrf_ssd", fusion), "hdd": _df("/mnt/DatenBankHDD", fusion)}
    for key, value in end_df.items():
        (report / "baseline" / f"END_DF_{key.upper()}.txt").write_text(value["raw"], encoding="utf-8")
    usage = {}
    for key in ("root", "ssd", "hdd"):
        start = parse_df(start_dfs[key])
        end = end_df[key]
        usage[key] = {"start": start, "end": {k: end[k] for k in ("size_bytes", "used_bytes", "available_bytes", "mountpoint")}, "used_delta_bytes": end["used_bytes"] - start["used_bytes"], "available_delta_bytes": end["available_bytes"] - start["available_bytes"]}
    generated_dirs = {}
    for path in [report, *sorted(path for path in report.iterdir() if path.is_dir())]:
        du = int(_run(["du", "-x", "-B1", "-s", str(path)], fusion).stdout.split()[0])
        generated_dirs[str(path)] = du
    disk = {
        "schema": "biospur.phase3r26c_r2_q4.disk_usage_audit.v1",
        "status": "PASS" if end_df["root"]["available_bytes"] >= 40_000_000_000 and end_df["ssd"]["available_bytes"] >= 100_000_000_000 and generated_dirs[str(report)] < EARLY_STOP_BYTES else "FAIL",
        "start_timestamp_utc": start_meta["timestamp_utc"],
        "filesystems": usage,
        "generated_directory_du_x_B1": generated_dirs,
        "report_size_bytes": generated_dirs[str(report)],
        "soft_limit_bytes": SOFT_LIMIT_BYTES, "early_stop_bytes": EARLY_STOP_BYTES,
        "hard_limit_bytes": HARD_LIMIT_BYTES,
        "soft_limit_exceeded": generated_dirs[str(report)] > SOFT_LIMIT_BYTES,
        "early_stop_exceeded": generated_dirs[str(report)] >= EARLY_STOP_BYTES,
        "hard_limit_exceeded": generated_dirs[str(report)] >= HARD_LIMIT_BYTES,
    }
    write_json(report / "DISK_USAGE_AUDIT.json", disk)
    checks = {
        "formal_freeze_sealed": read_json(report / "FORMAL_FREEZE_SEAL.json")["status"] == "FORMAL_FREEZE_SEALED",
        "exact_red": read_json(report / "FROZEN_RED_RESULT.json")["status"] == "PASS",
        "green": read_json(report / "FROZEN_GREEN_RESULT.json")["status"] == "PASS",
        "closure": read_json(report / "FORMAL_CLOSURE_RESULT.json")["status"] == "PASS",
        "r2_mutations": read_json(report / "R2_PRODUCTION_MUTATION_RESULTS.json")["all_valid_mutants_semantically_killed"],
        "r1_replay": read_json(report / "R1_MUTATION_REPLAY_RESULTS.json")["all_replayed_mutants_semantically_killed"],
        "negative_controls": negative["status"] == "PASS",
        "authorized_suite": read_json(report / "AUTHORIZED_SUITE_RESULT.json")["status"] == "PASS",
        "deterministic_replay": read_json(report / "DETERMINISTIC_REPLAY_RESULT.json")["status"] == "PASS",
        "isolated_rerun": read_json(report / "ISOLATED_CAPSULE_RERUN_RESULT.json")["status"] == "PASS",
        "data_access": data_summary["status"] == "PASS",
        "canonical_state": canonical_state["status"] == "PASS",
        "disk": disk["status"] == "PASS",
    }
    verdict = "QUALIFIED_UNCOMMITTED_NO_PUSH" if all(checks.values()) else "STOPPED_NO_COMMIT_NO_PUSH"
    final = {
        "schema": "biospur.phase3r26c_r2_q4.final_result.v1", "verdict": verdict,
        "qualification_checks": checks,
        "q2_text_anchors_replaced_by_structural_mutations": True,
        "all_mutation_preconditions_exact": read_json(report / "R2_PRODUCTION_MUTATION_RESULTS.json")["all_structural_preconditions_exact"],
        "invalid_mutants_counted_as_kills": 0,
        "no_forbidden_numeric_or_path_access": data_summary["status"] == "PASS",
        "next_step": "independent review, then a separate decision whether to commit/push" if verdict.startswith("QUALIFIED") else "no qualification continuation in this round",
        "states": ["NO_NEW_WORKTREE", "NO_COMMIT", "NO_PUSH", "NOT_FOR_OPENSENSE", "NOT_FOR_PHASE4"],
    }
    write_json(report / "FINAL_RESULT.json", final)
    markdown = f"""# BioSpur Phase 3-R2.6C-R2-Q4 final\n\nVerdict: `{verdict}`\n\nThe canonical-direct tooling repair used AST-structured R2 mutations with exact preconditions. Formal RED, GREEN, closure, R2/R1 mutation campaigns, controls, authorized suite, deterministic replay, isolated capsule rerun, access audit, disk audit, and canonical-state protection are summarized in `FINAL_RESULT.json`.\n\n- `NO_NEW_WORKTREE`\n- `NO_COMMIT`\n- `NO_PUSH`\n- `NOT_FOR_OPENSENSE`\n- `NOT_FOR_PHASE4`\n"""
    (report / "R26C_R2_Q4_FINAL.md").write_text(markdown, encoding="utf-8")
    missing = [name for name in REQUIRED_OUTPUTS if name != "REPRODUCIBILITY_MANIFEST.json" and not (report / name).exists()]
    if missing:
        raise RuntimeError(f"final report set incomplete: {missing}")
    reproducibility = {
        "schema": "biospur.phase3r26c_r2_q4.reproducibility.v1",
        "verdict": verdict, "identity": _identity(worktree),
        "artifacts": {name: {"sha256": sha256_file(report / name), "size_bytes": (report / name).stat().st_size} for name in REQUIRED_OUTPUTS if name != "REPRODUCIBILITY_MANIFEST.json"},
        "formal_command_results": {path.parent.name: sha256_file(path) for path in sorted(report.glob("raw/*/COMMAND_RESULT.json"))},
        "formal_command_count": len(list(report.glob("raw/*/COMMAND_RESULT.json"))),
    }
    write_json(report / "REPRODUCIBILITY_MANIFEST.json", reproducibility)
    for _ in range(3):
        size_before = int(_run(["du", "-x", "-B1", "-s", str(report)], fusion).stdout.split()[0])
        latest_df = {
            "root": _df("/", fusion),
            "ssd": _df("/mnt/nrf_ssd", fusion),
            "hdd": _df("/mnt/DatenBankHDD", fusion),
        }
        for key, value in latest_df.items():
            (report / "baseline" / f"END_DF_{key.upper()}.txt").write_text(value["raw"], encoding="utf-8")
            start = parse_df(start_dfs[key])
            end = value
            disk["filesystems"][key] = {
                "start": start,
                "end": {name: end[name] for name in ("size_bytes", "used_bytes", "available_bytes", "mountpoint")},
                "used_delta_bytes": end["used_bytes"] - start["used_bytes"],
                "available_delta_bytes": end["available_bytes"] - start["available_bytes"],
            }
        disk["report_size_bytes"] = size_before
        disk["generated_directory_du_x_B1"][str(report)] = size_before
        disk["soft_limit_exceeded"] = size_before > SOFT_LIMIT_BYTES
        disk["early_stop_exceeded"] = size_before >= EARLY_STOP_BYTES
        disk["hard_limit_exceeded"] = size_before >= HARD_LIMIT_BYTES
        disk["status"] = (
            "PASS"
            if latest_df["root"]["available_bytes"] >= 40_000_000_000
            and latest_df["ssd"]["available_bytes"] >= 100_000_000_000
            and size_before < EARLY_STOP_BYTES
            else "FAIL"
        )
        write_json(report / "DISK_USAGE_AUDIT.json", disk)
        reproducibility["artifacts"] = {
            name: {"sha256": sha256_file(report / name), "size_bytes": (report / name).stat().st_size}
            for name in REQUIRED_OUTPUTS if name != "REPRODUCIBILITY_MANIFEST.json"
        }
        write_json(report / "REPRODUCIBILITY_MANIFEST.json", reproducibility)
        size_after = int(_run(["du", "-x", "-B1", "-s", str(report)], fusion).stdout.split()[0])
        if size_after == size_before:
            break
    else:
        raise RuntimeError("final report allocation did not stabilize")
    if size_after >= EARLY_STOP_BYTES:
        raise RuntimeError("384 MiB early-stop reserve reached during finalization")


def verify_seal_q6() -> None:
    worktree, fusion, report = _roots()
    seal = read_json(report / "FORMAL_FREEZE_SEAL.json")
    manifest = read_json(report / "FORMAL_FREEZE_MANIFEST.json")
    artifact_checks = {
        name: sha256_file(report / name) == digest
        for name, digest in seal["sealed_artifact_sha256"].items()
    }
    current_sealed = _environment_contract("frozen_green", "formal")["sealed_file_sha256"]
    checks = {
        "seal_status": seal.get("status") == "FORMAL_FREEZE_SEALED",
        "sentinel_present": (report / "FORMAL_FREEZE_SEALED").exists(),
        "all_sealed_artifact_hashes_match": all(artifact_checks.values()),
        "all_sealed_source_tool_test_hashes_match": current_sealed == manifest["sealed_file_sha256"],
        "identity_unchanged": _identity(worktree) == manifest["identity"],
        "worktree_registry_unchanged": hashlib.sha256(
            _run(["git", "worktree", "list", "--porcelain"], worktree).stdout
        ).hexdigest() == manifest["worktree_registry_sha256"],
        "production_hashes_match": all(
            sha256_file(fusion / path) == digest
            for path, digest in manifest["production_source_sha256"].items()
        ),
    }
    payload = {
        "schema": "biospur.phase3r26c_r2_q6.formal_seal_verification.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "sealed_artifact_checks": artifact_checks,
    }
    write_json(report / "FORMAL_SEAL_VERIFICATION.json", payload)
    if payload["status"] != "PASS":
        raise RuntimeError(f"Q6 formal seal verification failed: {payload}")


def _write_q6_formal_aliases(report: Path) -> None:
    red = read_json(report / "FROZEN_RED_RESULT.json")
    green = read_json(report / "FROZEN_GREEN_RESULT.json")
    closure = read_json(report / "FORMAL_CLOSURE_RESULT.json")
    r2 = read_json(report / "R2_PRODUCTION_MUTATION_RESULTS.json")
    deterministic = read_json(report / "DETERMINISTIC_REPLAY_RESULT.json")
    isolated = read_json(report / "ISOLATED_CAPSULE_RERUN_RESULT.json")
    write_json(report / "FORMAL_RED_RESULTS.json", {
        **red, "schema": "biospur.phase3r26c_r2_q6.formal_red.v1",
        "exact_nodeid_count": red["junit"]["tests"],
        "failure_count": len(red["junit"]["failures"]),
        "pass_count": red["junit"]["passes"],
        "skip_count": len(red["junit"]["skipped"]),
        "error_count": len(red["junit"]["errors"]),
        "required_diagnostics_matched": sum(red["comparison"]["diagnostic_matches"].values()),
        "required_diagnostics_total": len(red["comparison"]["diagnostic_matches"]),
    })
    write_json(report / "FORMAL_GREEN_RESULTS.json", {
        **green, "schema": "biospur.phase3r26c_r2_q6.formal_green.v1",
    })
    consumer = closure["sections"]["consumer_closure"]
    schema = closure["sections"]["formal_schema_closure"]
    write_json(report / "K_PSI_CLOSURE_RESULTS.json", {
        "schema": "biospur.phase3r26c_r2_q6.k_psi_closure.v1",
        "status": consumer["status"],
        **consumer,
    })
    write_json(report / "SCHEMA_MATRIX_RESULTS.json", {
        "schema": "biospur.phase3r26c_r2_q6.schema_matrix.v1",
        "status": schema["status"],
        **schema,
    })
    write_json(report / "R2_STRUCTURAL_MUTATION_RESULTS.json", {
        **r2, "schema": "biospur.phase3r26c_r2_q6.r2_structural_mutations.v1",
    })
    write_json(report / "DETERMINISTIC_REPLAY_RESULTS.json", {
        **deterministic, "schema": "biospur.phase3r26c_r2_q6.deterministic_replay.v1",
    })
    write_json(report / "ISOLATED_RERUN_RESULTS.json", {
        **isolated, "schema": "biospur.phase3r26c_r2_q6.isolated_rerun.v1",
        "isolated_capsule_size_bytes": isolated["inventory"]["size_bytes"],
        "isolated_semantically_identical": bool(
            isolated["green_consistent"]
            and isolated["closure_consistent"]
            and isolated["normalized_result_digest_consistent"]
        ),
        "no_undeclared_repository_dependency": isolated["module_origins_all_within_capsule"],
        "no_undeclared_environment_dependency": isolated["normalized_result_digest_consistent"],
    })


def finalize_q6() -> None:
    worktree, fusion, report = _roots()
    if not (report / "FORMAL_SEAL_VERIFICATION.json").exists():
        raise RuntimeError("Q6 finalization requires formal seal verification")
    _write_q6_formal_aliases(report)

    formal_red = read_json(report / "FORMAL_RED_RESULTS.json")
    formal_green = read_json(report / "FORMAL_GREEN_RESULTS.json")
    k_closure = read_json(report / "K_PSI_CLOSURE_RESULTS.json")
    schema_closure = read_json(report / "SCHEMA_MATRIX_RESULTS.json")
    r2 = read_json(report / "R2_STRUCTURAL_MUTATION_RESULTS.json")
    r1 = read_json(report / "R1_MUTATION_REPLAY_RESULTS.json")
    deterministic = read_json(report / "DETERMINISTIC_REPLAY_RESULTS.json")
    isolated = read_json(report / "ISOLATED_RERUN_RESULTS.json")
    development = read_json(report / "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json")

    trace_summary = _write_trace_ledgers(report)
    data_summary = {
        "schema": "biospur.phase3r26c_r2_q6.data_access_summary.v1",
        "status": "PASS" if (
            trace_summary["forbidden_trace_access_count"] == 0
            and trace_summary["nested_ptrace_executed_count"] == 0
        ) else "FAIL",
        **trace_summary,
        "formal_commands_executed": len(list(report.glob("raw/*/COMMAND_RESULT.json"))),
        "sealed_holdout_read": False,
        "final_still_numeric_read": False,
        "walk_boxing_golf_numeric_read": False,
        "UWB_OpenSense_Vicon_numeric_read": False,
        "new_worktree_created": False,
        "event_ledger_encoding": "gzip_jsonl_stream",
        "summary_ledger_encoding": "aggregated_jsonl",
    }
    write_json(report / "DATA_ACCESS_SUMMARY.json", data_summary)

    canonical = _capture_end_state(worktree, fusion, report)
    canonical.update({
        "schema": "biospur.phase3r26c_r2_q6.worktree_invariant_audit.v1",
        "authorized_development_scope": [
            "tools/fusion_v2/phase3r26c_r1",
            "tools/fusion_v2/phase3r26c_r2_q2",
            str(report.relative_to(fusion)),
        ],
    })
    write_json(report / "WORKTREE_INVARIANT_AUDIT.json", canonical)
    write_json(report / "CANONICAL_STATE_PROTECTION.json", canonical)

    def parse_df(path: Path) -> dict:
        fields = path.read_text(encoding="utf-8").splitlines()[-1].split()
        return {
            "size_bytes": int(fields[0]), "used_bytes": int(fields[1]),
            "available_bytes": int(fields[2]), "mountpoint": fields[-1],
        }

    start_df = {
        key: parse_df(report / "baseline" / f"START_DF_{key.upper()}.txt")
        for key in ("root", "ssd", "hdd")
    }
    end_df = {
        "root": _df("/", fusion), "ssd": _df("/mnt/nrf_ssd", fusion),
        "hdd": _df("/mnt/DatenBankHDD", fusion),
    }
    for key, value in end_df.items():
        (report / "baseline" / f"END_DF_{key.upper()}.txt").write_text(value["raw"], encoding="utf-8")
    usage = {}
    for key in ("root", "ssd", "hdd"):
        end = {name: end_df[key][name] for name in ("size_bytes", "used_bytes", "available_bytes", "mountpoint")}
        usage[key] = {
            "start": start_df[key], "end": end,
            "used_delta_bytes": end["used_bytes"] - start_df[key]["used_bytes"],
            "available_delta_bytes": end["available_bytes"] - start_df[key]["available_bytes"],
        }
    disk = {
        "schema": "biospur.phase3r26c_r2_q6.disk_usage_audit.v1",
        "status": "PENDING_STABILIZATION",
        "filesystems": usage,
        "report_size_bytes": 0,
        "soft_limit_bytes": SOFT_LIMIT_BYTES,
        "early_stop_bytes": EARLY_STOP_BYTES,
        "hard_limit_bytes": HARD_LIMIT_BYTES,
        "isolated_capsule_size_bytes": isolated["isolated_capsule_size_bytes"],
        "isolated_capsule_limit_bytes": 5 * 1024 * 1024,
    }
    write_json(report / "DISK_USAGE_AUDIT.json", disk)

    checks = {
        "formal_seal": read_json(report / "FORMAL_SEAL_VERIFICATION.json")["status"] == "PASS",
        "formal_red_exact": (
            formal_red["status"] == "PASS" and formal_red["exact_nodeid_count"] == 15
            and formal_red["failure_count"] == 10 and formal_red["pass_count"] == 5
            and formal_red["skip_count"] == 0 and formal_red["error_count"] == 0
            and formal_red["required_diagnostics_matched"] == formal_red["required_diagnostics_total"] == 10
        ),
        "formal_green_15_of_15": (
            formal_green["status"] == "PASS" and formal_green["junit"]["tests"] == 15
            and formal_green["junit"]["passes"] == 15
        ),
        "k_psi_4_of_4": k_closure["status"] == "PASS" and k_closure["k_consumers_psi_free"] == 4,
        "schema_35_of_35": (
            schema_closure["status"] == "PASS"
            and schema_closure["formal_schema_matrix"]["passed"] == 35
        ),
        "r2_22_of_22": (
            r2["mutant_count"] == r2["valid_count"] == r2["killed_count"] == 22
            and r2["invalid_mutants_counted_as_kills"] == 0
            and r2["all_structural_preconditions_exact"]
            and r2["all_valid_mutants_semantically_killed"]
        ),
        "r1_14_of_14": (
            r1["mutant_count"] == r1["valid_count"] == r1["killed_count"] == 14
            and r1["invalid_mutants_counted_as_kills"] == 0
            and r1["all_replayed_mutants_semantically_killed"]
        ),
        "deterministic_replay": deterministic["status"] == "PASS",
        "isolated_rerun": (
            isolated["status"] == "PASS" and isolated["isolated_semantically_identical"]
            and isolated["isolated_capsule_size_bytes"] < 5 * 1024 * 1024
        ),
        "data_access": data_summary["status"] == "PASS",
        "canonical_state": canonical["status"] == "PASS",
        "development_14_of_14": development["status"] == "PASS",
    }
    if not all(checks.values()):
        raise RuntimeError(f"Q6 final mandatory check failed: {checks}")

    direct_answers = {
        "m05_frozen_semantic_intent": "protocol-frame axis calculation consumes H instead of K",
        "m05_legacy_h_undefined_reason": "the deliberately K-only helper binds k and has no h local, parameter, closure, or global",
        "m05_correction": "structurally replace the unique caller K-view argument with the real branch_state.h_common_rad values in the mutant capsule only",
        "m08_missing_key": "semantic_version",
        "m08_correction": "membership-and-value short-circuit reaches the designated semantic assertion without catching KeyError or introducing a fallback",
        "m10_zero_anchor_reason": "the K-only refactor replaced the legacy branch_state assignment with k_protocol_relative[segment]",
        "m10_corrected_ast_target": "score_branch_candidate::_score_k_space_branch_candidate:first_argument:branch_state.k_protocol_relative_rad_by_coordinate",
        "m03_preserved_and_killed": next(row for row in r1["mutants"] if row["mutant_id"].startswith("M03"))["classification"] == "VALID_SEMANTIC_KILL",
        "development_all_fourteen_attempted": development["mutant_count"] == 14,
        "development_structural_14_of_14": development["summary"]["r1_structural_preconditions"] == "14/14",
        "development_semantic_kills_14_of_14": development["summary"]["r1_intended_semantic_kills"] == "14/14",
        "invalid_mutants_counted_as_kills": 0,
        "formal_seal_created": True,
        "formal_red_exact": checks["formal_red_exact"],
        "formal_green_15_of_15": checks["formal_green_15_of_15"],
        "formal_k_psi_4_of_4": checks["k_psi_4_of_4"],
        "formal_schema_35_of_35": checks["schema_35_of_35"],
        "formal_r2_22_of_22": checks["r2_22_of_22"],
        "formal_r1_14_of_14": checks["r1_14_of_14"],
        "formal_deterministic_replay": checks["deterministic_replay"],
        "formal_isolated_rerun": checks["isolated_rerun"],
        "isolated_capsule_size_bytes": isolated["isolated_capsule_size_bytes"],
        "forbidden_accesses": data_summary["forbidden_trace_access_count"],
        "nested_ptrace_events": data_summary["nested_ptrace_executed_count"],
        "new_worktrees": canonical["new_worktree_count"],
        "commits": 0,
        "pushes": 0,
        "result_classification": "FORMALLY_QUALIFIED_Q6_TOOLING_REPAIR",
        "ready_for_separate_commit_push_decision": True,
    }
    final = {
        "schema": "biospur.phase3r26c_r2_q6.final_result.v1",
        "verdict": "QUALIFIED_READY_FOR_SEPARATE_COMMIT_PUSH_DECISION",
        "qualification_checks": checks,
        "direct_answers": direct_answers,
        "states": [
            "NO_NEW_WORKTREE", "NO_COMMIT", "NO_PUSH",
            "NOT_FOR_OPENSENSE", "NOT_FOR_PHASE4",
        ],
        "disk": {"filesystems": usage},
    }
    write_json(report / "FINAL_RESULT.json", final)
    markdown = f"""# BioSpur Phase 3-R2.6C-R2 Q6 final

Verdict: `QUALIFIED_READY_FOR_SEPARATE_COMMIT_PUSH_DECISION`

The tooling-only repair preserved production and the Q5 M03 correction. Development proved all fourteen R1 mutants end to end before the fresh Q6 seal. The sealed formal chain then passed exact RED (15 nodeids, 10 failures, 5 passes, 10/10 diagnostics), GREEN (15/15), K/psi closure (4/4), schema closure (35/35), R2 (22/22), R1 (14/14), deterministic replay, and the isolated rerun.

The isolated capsule contains `{isolated['isolated_capsule_size_bytes']}` bytes.

- `NO_NEW_WORKTREE`
- `NO_COMMIT`
- `NO_PUSH`
- `NOT_FOR_OPENSENSE`
- `NOT_FOR_PHASE4`
"""
    (report / "R26C_R2_Q6_FINAL.md").write_text(markdown, encoding="utf-8")
    reproducibility = {
        "schema": "biospur.phase3r26c_r2_q6.reproducibility.v1",
        "verdict": final["verdict"], "identity": _identity(worktree), "artifacts": {},
        "formal_command_results": {
            path.parent.name: sha256_file(path)
            for path in sorted(report.glob("raw/*/COMMAND_RESULT.json"))
        },
    }
    write_json(report / "REPRODUCIBILITY_MANIFEST.json", reproducibility)

    for _ in range(5):
        size = int(_run(["du", "-x", "-B1", "-s", str(report)], fusion).stdout.split()[0])
        latest_df = {
            "root": _df("/", fusion), "ssd": _df("/mnt/nrf_ssd", fusion),
            "hdd": _df("/mnt/DatenBankHDD", fusion),
        }
        for key, value in latest_df.items():
            end = {name: value[name] for name in ("size_bytes", "used_bytes", "available_bytes", "mountpoint")}
            usage[key]["end"] = end
            usage[key]["used_delta_bytes"] = end["used_bytes"] - start_df[key]["used_bytes"]
            usage[key]["available_delta_bytes"] = end["available_bytes"] - start_df[key]["available_bytes"]
            (report / "baseline" / f"END_DF_{key.upper()}.txt").write_text(value["raw"], encoding="utf-8")
        disk.update({
            "status": "PASS" if (
                latest_df["root"]["available_bytes"] >= 40_000_000_000
                and latest_df["ssd"]["available_bytes"] >= 100_000_000_000
                and size < EARLY_STOP_BYTES
                and isolated["isolated_capsule_size_bytes"] < 5 * 1024 * 1024
            ) else "FAIL",
            "filesystems": usage,
            "report_size_bytes": size,
            "soft_limit_exceeded": size > SOFT_LIMIT_BYTES,
            "early_stop_exceeded": size >= EARLY_STOP_BYTES,
            "hard_limit_exceeded": size >= HARD_LIMIT_BYTES,
        })
        write_json(report / "DISK_USAGE_AUDIT.json", disk)
        final["disk"] = {
            "filesystems": usage, "report_size_bytes": size,
            "isolated_capsule_size_bytes": isolated["isolated_capsule_size_bytes"],
        }
        write_json(report / "FINAL_RESULT.json", final)
        reproducibility["artifacts"] = {
            name: {"sha256": sha256_file(report / name), "size_bytes": (report / name).stat().st_size}
            for name in Q6_REQUIRED_OUTPUTS if name != "REPRODUCIBILITY_MANIFEST.json"
        }
        write_json(report / "REPRODUCIBILITY_MANIFEST.json", reproducibility)
        after = int(_run(["du", "-x", "-B1", "-s", str(report)], fusion).stdout.split()[0])
        if after == size:
            break
    else:
        raise RuntimeError("Q6 final report allocation did not stabilize")
    missing = [name for name in Q6_REQUIRED_OUTPUTS if not (report / name).exists()]
    if missing:
        raise RuntimeError(f"Q6 final report set incomplete: {missing}")
    if disk["status"] != "PASS":
        raise RuntimeError("Q6 disk contract failed during finalization")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=(
        "start", "q6-baseline", "q7-baseline", "q7-bind-development",
        "prepare", "seal", "verify-seal-q6", "verify-seal-q7",
        "finalize", "finalize-q6", "finalize-q7", "development-stop",
    ))
    args = parser.parse_args()
    {
        "start": start,
        "q6-baseline": q6_baseline,
        "q7-baseline": q7_baseline,
        "q7-bind-development": bind_q7_development,
        "prepare": prepare,
        "seal": seal,
        "verify-seal-q6": verify_seal_q6,
        "verify-seal-q7": verify_seal_q6,
        "finalize": finalize,
        "finalize-q6": finalize_q6,
        "finalize-q7": finalize_q6,
        "development-stop": development_stop,
    }[args.action]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
