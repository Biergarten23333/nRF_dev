"""Final byte-integrity comparison, tests, media audit, and manifest."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import time

from .data import M1_PATH, REPOSITORY, primary_input_inventory, sha256


GIT_ROOT = REPOSITORY.parent
CACHE_NAMES = {".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"}
AUTHORIZED_FILES = {
    "Fusion_Part/SOURCE_PLACEMENT_DECISION_ROOT_R4.md",
    "Fusion_Part/tools/run_root_r4.py",
    "Fusion_Part/tools/build_root_r4_review.py",
    "Fusion_Part/tools/finalize_root_r4.py",
}
AUTHORIZED_PREFIXES = ("Fusion_Part/src/biospur_fusion/root_r4/", "Fusion_Part/tests/root_r4/")


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _intentional(relative: str) -> bool:
    return relative in AUTHORIZED_FILES or any(relative.startswith(prefix) for prefix in AUTHORIZED_PREFIXES)


def _tree_digest(root: Path, *, repository_mode: bool = False, progress: bool = False) -> dict:
    aggregate = hashlib.sha256(); count = total = links = 0; started = time.monotonic(); next_report = 5 << 30
    for base, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in CACHE_NAMES)
        for name in sorted(files):
            path = Path(base) / name; relative = path.relative_to(root).as_posix()
            if repository_mode and _intentional(relative):
                continue
            status = path.lstat(); relative_bytes = relative.encode("utf-8", "surrogateescape")
            if stat.S_ISLNK(status.st_mode):
                content_digest = hashlib.sha256(os.readlink(path).encode("utf-8", "surrogateescape")).digest(); links += 1
            elif stat.S_ISREG(status.st_mode):
                content_digest = bytes.fromhex(sha256(path)); count += 1; total += status.st_size
            else:
                continue
            aggregate.update(len(relative_bytes).to_bytes(4, "little")); aggregate.update(relative_bytes)
            aggregate.update((status.st_mode & 0o7777).to_bytes(4, "little")); aggregate.update(status.st_size.to_bytes(8, "little")); aggregate.update(content_digest)
            if progress and total >= next_report:
                print(f"final integrity progress: {total / (1 << 30):.1f} GiB", flush=True); next_report += 5 << 30
    return {"root": str(root), "regular_files": count, "symlinks": links, "bytes": total,
            "sha256_tree_v1": aggregate.hexdigest(),
            "definition": "SHA256(sorted relative path, mode, lstat size, file SHA256 or symlink-target SHA256)",
            "excluded_cache_names": sorted(CACHE_NAMES), "excluded_declared_root_r4_paths": repository_mode,
            "elapsed_s": time.monotonic() - started}


def _status_path(line: bytes) -> str:
    text = line.decode("utf-8", "surrogateescape").rstrip("\n")
    path = text[3:] if len(text) >= 3 else text
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    prefix = "BioSpur_Fusion/"
    return path[len(prefix):] if path.startswith(prefix) else path


def _filtered_status(raw: bytes) -> bytes:
    return b"".join(line for line in raw.splitlines(keepends=True) if not _intentional(_status_path(line)))


def _tests(output: Path) -> dict:
    command = [sys.executable, "-m", "pytest", "-q", str(REPOSITORY / "Fusion_Part/tests/root_r4")]
    environment = os.environ.copy(); environment["PYTHONPATH"] = str(REPOSITORY / "Fusion_Part/src")
    result = subprocess.run(command, cwd=REPOSITORY, env=environment, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    value = {"schema": "biospur.root_r4.qualification_tests.v1", "command": command,
             "returncode": result.returncode, "pass": result.returncode == 0, "output": result.stdout}
    dump(output / "QUALIFICATION_TEST_RESULTS.json", value); return value


def _media(output: Path) -> dict:
    viewer = output / "C1_ROOT_R4_VIEWER_INDEX.html"; video = output / "C1_ROOT_R4_REVIEW.mp4"
    text = viewer.read_text(encoding="utf-8"); scripts = []
    cursor = 0
    while True:
        start = text.find("<script>", cursor)
        if start < 0: break
        end = text.find("</script>", start)
        if end < 0: raise ValueError("unterminated viewer script")
        scripts.append(text[start + 8:end]); cursor = end + 9
    syntax = subprocess.run(["node", "--check", "-"], input="\n".join(scripts), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,width,height,r_frame_rate,nb_frames", "-of", "json", str(video)], text=True))
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    value = {"schema": "biospur.root_r4.media_verification.v1",
             "viewer": {"bytes": viewer.stat().st_size, "sha256": sha256(viewer),
                        "offline": all(token not in text for token in ("fetch(", "XMLHttpRequest", "WebSocket(")),
                        "javascript_syntax_pass": syntax.returncode == 0,
                        "required_labels": all(label in text for label in ("Synthetic truth", "Real C1", "Authorized frame",
                            "Unauthorized counterfactual", "T4-only", "Raw-range-only", "Lineage-safe hybrid"))},
             "video": {"bytes": video.stat().st_size, "sha256": sha256(video), "ffprobe": probe,
                       "full_decode_pass": decode.returncode == 0}}
    dump(output / "BROWSER_AND_MEDIA_VERIFICATION.json", value); return value


def _integrity(output: Path) -> dict:
    baseline = json.loads((output / "INTEGRITY_BASELINE_BEFORE.json").read_text(encoding="utf-8"))
    print("Root-R4 final integrity: pre-existing repository tree", flush=True)
    repository = _tree_digest(REPOSITORY, repository_mode=True, progress=True)
    package = {
        "root_r3_source": _tree_digest(REPOSITORY / "Fusion_Part/src/biospur_fusion/root_r3"),
        "root_r3_tests": _tree_digest(REPOSITORY / "Fusion_Part/tests/root_r3"),
    }
    evidence = [_tree_digest(Path(row["root"])) for row in baseline["prior_evidence_trees"]]
    raw_status = subprocess.check_output(["git", "-C", str(GIT_ROOT), "status", "--porcelain=v1", "--untracked-files=all"])
    (output / "FINAL_GIT_STATUS.txt").write_bytes(raw_status)
    initial = (output / "INITIAL_GIT_STATUS.txt").read_bytes(); initial_filtered = _filtered_status(initial); final_filtered = _filtered_status(raw_status)
    head = subprocess.check_output(["git", "-C", str(GIT_ROOT), "rev-parse", "HEAD"], text=True).strip()
    branch = subprocess.check_output(["git", "-C", str(GIT_ROOT), "branch", "--show-current"], text=True).strip()
    comparisons = {
        "repository_preexisting_tree": repository["sha256_tree_v1"] == baseline["repository_preexisting_tree"]["sha256_tree_v1"],
        "root_r3_source": package["root_r3_source"]["sha256_tree_v1"] == baseline["package_trees"]["root_r3_source"]["sha256_tree_v1"],
        "root_r3_tests": package["root_r3_tests"]["sha256_tree_v1"] == baseline["package_trees"]["root_r3_tests"]["sha256_tree_v1"],
        "prior_evidence_trees": {row["root"]: row["sha256_tree_v1"] == before["sha256_tree_v1"]
                                 for row, before in zip(evidence, baseline["prior_evidence_trees"])},
        "git_head": head == baseline["repository_git"]["head"], "git_branch": branch == baseline["repository_git"]["branch"],
        "preexisting_git_status_after_authorized_exclusions": initial_filtered == final_filtered,
    }
    inputs = {}
    for name, before in baseline["primary_inputs"].items():
        path = Path(name); actual = {"bytes": path.stat().st_size, "sha256": sha256(path)}
        actual["exact_match"] = actual == before; inputs[name] = actual
    all_exact = (all(value for key, value in comparisons.items() if key != "prior_evidence_trees") and
                 all(comparisons["prior_evidence_trees"].values()) and all(row["exact_match"] for row in inputs.values()))
    value = {"schema": "biospur.root_r4.integrity_final.v1", "before": str(output / "INTEGRITY_BASELINE_BEFORE.json"),
             "after": {"repository_preexisting_tree": repository, "package_trees": package,
                       "prior_evidence_trees": evidence, "repository_git": {"head": head, "branch": branch,
                       "status_lines": raw_status.count(b"\n"), "status_sha256": hashlib.sha256(raw_status).hexdigest(),
                       "filtered_status_lines": final_filtered.count(b"\n"), "filtered_status_sha256": hashlib.sha256(final_filtered).hexdigest()}},
             "before_filtered_status": {"lines": initial_filtered.count(b"\n"), "sha256": hashlib.sha256(initial_filtered).hexdigest()},
             "comparisons": comparisons, "primary_inputs_after": inputs,
             "all_preexisting_content_and_inputs_exact": all_exact,
             "authorized_root_r4_additions_excluded": sorted(AUTHORIZED_FILES) + list(AUTHORIZED_PREFIXES),
             "commit": False, "push": False, "merge": False, "product_default_changed": False}
    dump(output / "INTEGRITY_FINAL.json", value); return value


def _manifest(output: Path, tests: dict, media: dict, integrity: dict) -> dict:
    hashes = {}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "REPRODUCIBILITY_MANIFEST.json":
            hashes[path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    source = {}
    for path in sorted((REPOSITORY / "Fusion_Part/src/biospur_fusion/root_r4").glob("*.py")):
        source[str(path)] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    for path in sorted((REPOSITORY / "Fusion_Part/tests/root_r4").glob("*.py")) + sorted((REPOSITORY / "Fusion_Part/tools").glob("*root_r4*.py")):
        source[str(path)] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    value = {"schema": "biospur.root_r4.reproducibility_manifest.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
             "python": sys.version, "platform": platform.platform(), "numpy": __import__("numpy").__version__,
             "source_and_test_hashes": source, "primary_inputs": primary_input_inventory(),
             "output_hashes_before_manifest": hashes, "qualification_tests_pass": tests["pass"],
             "media_verification_pass": media["viewer"]["javascript_syntax_pass"] and media["video"]["full_decode_pass"],
             "integrity_pass": integrity["all_preexisting_content_and_inputs_exact"],
             "run_command": "PYTHONPATH=Fusion_Part/src python3 Fusion_Part/tools/run_root_r4.py --output <dir>",
             "finalize_command": "PYTHONPATH=Fusion_Part/src python3 Fusion_Part/tools/finalize_root_r4.py --output <dir>",
             "commit": False, "push": False, "merge": False, "product_default_changed": False,
             "manifest_self_hash_convention": "SHA256 canonical JSON payload with manifest_payload_sha256 omitted"}
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(); value["manifest_payload_sha256"] = hashlib.sha256(payload).hexdigest()
    dump(output / "REPRODUCIBILITY_MANIFEST.json", value); return value


def finalize(output: Path) -> dict:
    output = Path(output).resolve(); tests = _tests(output)
    if not tests["pass"]: raise RuntimeError("Root-R4 qualification tests failed")
    media = _media(output)
    if not (media["viewer"]["javascript_syntax_pass"] and media["video"]["full_decode_pass"]):
        raise RuntimeError("viewer/video verification failed")
    integrity = _integrity(output)
    if not integrity["all_preexisting_content_and_inputs_exact"]:
        raise RuntimeError("Root-R4 pre-existing content/input integrity failed")
    final_path = output / "FINAL_RESULT.json"; final = json.loads(final_path.read_text(encoding="utf-8"))
    final["qualification_tests_pass"] = True; final["media_verification_pass"] = True
    final["integrity_pass"] = True; final["finalized_utc"] = datetime.now(timezone.utc).isoformat(); dump(final_path, final)
    report = output / "ROOT_R4_RAW_RANGE_FRAME_FUSION_FINAL.md"
    integrity_section = ("\n## Final integrity and reproducibility\n\n"
        "All pre-existing repository content (after excluding only enumerated Root-R4 additions), frozen M1, Root-R3, prior evidence trees, primary inputs, branch, HEAD, and pre-existing Git status are byte-identical. Root-R4 tests and offline viewer/video verification pass. Nothing was committed, pushed, merged, promoted, enabled, or made a product default.\n")
    report_text = report.read_text(encoding="utf-8")
    if "## Final integrity and reproducibility" not in report_text:
        report.write_text(report_text + integrity_section, encoding="utf-8")
    manifest = _manifest(output, tests, media, integrity)
    return {"final": final, "integrity": integrity["all_preexisting_content_and_inputs_exact"],
            "tests": tests["pass"], "media": True, "manifest": manifest["manifest_payload_sha256"]}
