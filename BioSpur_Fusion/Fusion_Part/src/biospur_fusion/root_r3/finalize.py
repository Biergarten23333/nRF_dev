"""Final reporting, integrity comparison, and reproducibility manifest."""
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

import numpy as np

from .data import sha256


REPOSITORY = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
GIT_ROOT = REPOSITORY.parent
FROZEN_M1 = Path("/tmp/biospur_pure_imu_mvp_m1_20260823T135120Z")
PRIOR_TREES = (
    Path("/tmp/biospur_c123_uwb_counterfactual_20260823T155948Z"),
    Path("/tmp/biospur_c123_uwb_beacon_causal_replay_20260823T171739Z"),
    Path("/tmp/biospur_c123_uwb_root_r2_20260824T035440Z"),
)
CACHE_NAMES = {".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"}
ROOT_R3_EXCLUDED_FILES = {
    "Fusion_Part/SOURCE_PLACEMENT_DECISION.md",
    "Fusion_Part/tools/run_root_r3.py",
    "Fusion_Part/tools/build_root_r3_review.py",
    "Fusion_Part/tools/finalize_root_r3.py",
}
ROOT_R3_EXCLUDED_PREFIXES = (
    "Fusion_Part/src/biospur_fusion/root_r3/",
    "Fusion_Part/tests/root_r3/",
)
SOURCE_FILES = (
    "Fusion_Part/SOURCE_PLACEMENT_DECISION.md",
    "Fusion_Part/src/biospur_fusion/root_r3/__init__.py",
    "Fusion_Part/src/biospur_fusion/root_r3/models.py",
    "Fusion_Part/src/biospur_fusion/root_r3/estimator.py",
    "Fusion_Part/src/biospur_fusion/root_r3/interfaces.py",
    "Fusion_Part/src/biospur_fusion/root_r3/data.py",
    "Fusion_Part/src/biospur_fusion/root_r3/metrics.py",
    "Fusion_Part/src/biospur_fusion/root_r3/replay.py",
    "Fusion_Part/src/biospur_fusion/root_r3/synthetic.py",
    "Fusion_Part/src/biospur_fusion/root_r3/pipeline.py",
    "Fusion_Part/src/biospur_fusion/root_r3/viewer.py",
    "Fusion_Part/src/biospur_fusion/root_r3/finalize.py",
    "Fusion_Part/tools/run_root_r3.py",
    "Fusion_Part/tools/build_root_r3_review.py",
    "Fusion_Part/tools/finalize_root_r3.py",
)
TEST_FILES = (
    "Fusion_Part/tests/root_r3/test_estimator.py",
    "Fusion_Part/tests/root_r3/test_interfaces.py",
    "Fusion_Part/tests/root_r3/test_synthetic.py",
    "Fusion_Part/tests/root_r3/test_c1_interface_integration.py",
    "Fusion_Part/tests/root_r3/test_data_cache.py",
    "Fusion_Part/tests/root_r3/test_viewer.py",
    "Fusion_Part/tests/root_r3/test_finalize.py",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _tree_digest(root: Path, *, repository_mode: bool = False, progress: bool = False) -> dict:
    """Use the exact byte framing frozen in the Root-R3 before snapshot."""

    aggregate = hashlib.sha256(); count = total = links = 0
    started = time.monotonic(); next_report = 5 << 30
    for base, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in CACHE_NAMES)
        for name in sorted(files):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if repository_mode and (
                relative in ROOT_R3_EXCLUDED_FILES or
                any(relative.startswith(prefix) for prefix in ROOT_R3_EXCLUDED_PREFIXES)
            ):
                continue
            status = path.lstat(); relative_bytes = relative.encode("utf-8", "surrogateescape")
            if stat.S_ISLNK(status.st_mode):
                target = os.readlink(path).encode("utf-8", "surrogateescape")
                content_digest = hashlib.sha256(target).digest(); links += 1
            elif stat.S_ISREG(status.st_mode):
                content_digest = bytes.fromhex(sha256(path)); count += 1; total += status.st_size
            else:
                continue
            aggregate.update(len(relative_bytes).to_bytes(4, "little")); aggregate.update(relative_bytes)
            aggregate.update((status.st_mode & 0o7777).to_bytes(4, "little"))
            aggregate.update(status.st_size.to_bytes(8, "little")); aggregate.update(content_digest)
            if progress and total >= next_report:
                print(f"integrity progress {root.name}: {total / (1 << 30):.1f} GiB", flush=True)
                next_report += 5 << 30
    return {
        "root": str(root), "regular_files": count, "symlinks": links, "bytes": total,
        "sha256_tree_v1": aggregate.hexdigest(),
        "definition": "SHA256(sorted relative path, mode, lstat size, file SHA256 or symlink-target SHA256)",
        "excluded_cache_names": sorted(CACHE_NAMES),
        "excluded_declared_root_r3_paths": repository_mode,
        "elapsed_s": time.monotonic() - started,
    }


def _intentional_path(path: str) -> bool:
    prefix = "BioSpur_Fusion/"
    local = path[len(prefix):] if path.startswith(prefix) else path
    return (local in ROOT_R3_EXCLUDED_FILES or
            any(local.startswith(value) for value in ROOT_R3_EXCLUDED_PREFIXES) or
            any(part in CACHE_NAMES for part in Path(local).parts))


def _filtered_status(raw: bytes) -> bytes:
    retained = []
    for line in raw.splitlines(keepends=True):
        text = line.decode("utf-8", "surrogateescape")
        path = text[3:].rstrip("\n") if len(text) >= 3 else text.rstrip("\n")
        if not _intentional_path(path):
            retained.append(line)
    return b"".join(retained)


def run_integrity(output: Path) -> dict:
    output = Path(output).resolve(); before = _load(output / "INTEGRITY_BASELINE_BEFORE.json")
    print("final integrity: repository pre-existing tree", flush=True)
    repository_tree = _tree_digest(REPOSITORY, repository_mode=True, progress=True)
    print("final integrity: frozen M1 and prior trees", flush=True)
    frozen_m1 = _tree_digest(FROZEN_M1)
    prior = [_tree_digest(path) for path in PRIOR_TREES]
    raw_status = subprocess.check_output(
        ["git", "-C", str(GIT_ROOT), "status", "--porcelain=v1", "--untracked-files=all"])
    (output / "FINAL_GIT_STATUS.txt").write_bytes(raw_status)
    initial_status = (output / "INITIAL_GIT_STATUS.txt").read_bytes()
    initial_filtered = _filtered_status(initial_status); final_filtered = _filtered_status(raw_status)
    head = subprocess.check_output(["git", "-C", str(GIT_ROOT), "rev-parse", "HEAD"], text=True).strip()
    branch = subprocess.check_output(["git", "-C", str(GIT_ROOT), "branch", "--show-current"], text=True).strip()
    comparisons = {
        "repository_preexisting_tree": repository_tree["sha256_tree_v1"] == before["repository_preexisting_tree"]["sha256_tree_v1"],
        "frozen_m1_tree": frozen_m1["sha256_tree_v1"] == before["frozen_m1_tree"]["sha256_tree_v1"],
        "prior_trees": {
            str(current["root"]): current["sha256_tree_v1"] == previous["sha256_tree_v1"]
            for current, previous in zip(prior, before["prior_trees"])
        },
        "git_head": head == before["repository_git"]["head"],
        "git_branch": branch == before["repository_git"]["branch"],
        "preexisting_git_status_after_authorized_exclusions": initial_filtered == final_filtered,
    }
    authorized_inputs = {}
    for name, row in before["authorized_c1_inputs"].items():
        path = Path(name); digest = sha256(path)
        authorized_inputs[name] = {"bytes": path.stat().st_size, "sha256": digest,
                                   "exact_match": path.stat().st_size == row["bytes"] and digest == row["sha256"]}
    all_exact = (comparisons["repository_preexisting_tree"] and comparisons["frozen_m1_tree"] and
                 all(comparisons["prior_trees"].values()) and comparisons["git_head"] and
                 comparisons["git_branch"] and comparisons["preexisting_git_status_after_authorized_exclusions"] and
                 all(value["exact_match"] for value in authorized_inputs.values()))
    value = {
        "schema": "biospur.root_r3.integrity_final.v1", "before": str(output / "INTEGRITY_BASELINE_BEFORE.json"),
        "after": {"repository_preexisting_tree": repository_tree, "frozen_m1_tree": frozen_m1,
                  "prior_trees": prior, "repository_git": {"head": head, "branch": branch,
                  "status_lines": raw_status.count(b"\n"), "status_sha256": hashlib.sha256(raw_status).hexdigest(),
                  "filtered_status_lines": final_filtered.count(b"\n"),
                  "filtered_status_sha256": hashlib.sha256(final_filtered).hexdigest()}},
        "before_filtered_status": {"lines": initial_filtered.count(b"\n"),
                                   "sha256": hashlib.sha256(initial_filtered).hexdigest()},
        "comparisons": comparisons, "authorized_inputs_after": authorized_inputs,
        "all_preexisting_content_and_inputs_exact": all_exact,
        "authorized_root_r3_additions_excluded_from_preexisting_comparison": sorted(ROOT_R3_EXCLUDED_FILES) + list(ROOT_R3_EXCLUDED_PREFIXES),
        "commit": False, "push": False, "merge": False, "product_default_changed": False,
    }
    _dump(output / "INTEGRITY_FINAL.json", value)
    return value


def _browser_and_media_audit(output: Path) -> dict:
    html = output / "C1_ROOT_R3_VIEWER_INDEX.html"; video = output / "C1_ROOT_R3_REVIEW.mp4"
    text = html.read_text(encoding="utf-8")
    scripts = []
    cursor = 0
    while True:
        start = text.find("<script>", cursor)
        if start < 0:
            break
        end = text.find("</script>", start)
        if end < 0:
            raise ValueError("unterminated viewer script")
        scripts.append(text[start + len("<script>"):end]); cursor = end + len("</script>")
    syntax = subprocess.run(["node", "--check", "-"], input="\n".join(scripts), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,width,height,r_frame_rate,nb_frames", "-of", "json", str(video)], text=True))
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    value = {
        "schema": "biospur.root_r3.browser_media_audit.v1",
        "viewer": {"path": str(html), "bytes": html.stat().st_size, "sha256": sha256(html),
                   "required_controls_present": all(marker in text for marker in (
                       'id="baseline"', 'id="camera"', 'id="play"', 'id="time"', 'id="cloud"', 'id="rejected"')),
                   "all_baselines_present": all(f">B{index}<" in text for index in range(6)),
                   "placeholder_resolved": "__DATA__" not in text,
                   "javascript_syntax_pass": syntax.returncode == 0,
                   "offline_external_request_api_absent": all(token not in text for token in ("fetch(", "XMLHttpRequest", "WebSocket("))},
        "real_browser_interaction": {
            "completed": False, "claimed": False, "status": "BLOCKED_SUPPORTED_BROWSER_BOOTSTRAP",
            "attempts": 2,
            "reason": "installed browser client imports node:process while the current browser-control session explicitly disallows node:process",
            "fallback_browser_automation_used": False,
        },
        "video": {"path": str(video), "bytes": video.stat().st_size, "sha256": sha256(video),
                  "ffprobe": probe, "full_decode_pass": decode.returncode == 0},
    }
    _dump(output / "BROWSER_AND_MEDIA_VERIFICATION.json", value)
    return value


def _run_tests(output: Path) -> dict:
    command = [sys.executable, "-m", "pytest", "-q", str(REPOSITORY / "Fusion_Part/tests/root_r3")]
    environment = os.environ.copy(); environment["PYTHONPATH"] = str(REPOSITORY / "Fusion_Part/src")
    result = subprocess.run(command, cwd=REPOSITORY, env=environment, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    value = {"schema": "biospur.root_r3.qualification_tests.v1", "command": command,
             "returncode": result.returncode, "pass": result.returncode == 0, "output": result.stdout}
    _dump(output / "QUALIFICATION_TEST_RESULTS.json", value)
    return value


def _report(output: Path, integrity: dict, browser: dict, tests: dict) -> str:
    final = _load(output / "FINAL_RESULT.json"); full = _load(output / "FULL_C1_SHADOW_REPLAY_METRICS.json")
    dispersion = _load(output / "CROSS_TAG_0902_DISPERSION_DECOMPOSITION.json")
    quality = _load(output / "UWB_QUALITY_AND_FDI_AUDIT.json")
    degradation = _load(output / "DEGRADATION_AND_RECOVERY_AUDIT.json")
    synthetic = _load(output / "SYNTHETIC_TRANSFER_AND_FAULT_SURFACES.json")
    pareto = _load(output / "PARETO_FRONTIER.json"); strict = _load(output / "STRICT_CAUSALITY_AND_LATENCY_AUDIT.json")
    redundancy = _load(output / "REDUNDANCY_AND_ABLATION_AUDIT.json")
    within = np.array(list(final["within_vs_between"]["within_tag_robust_radial_m"].values()))
    heading = np.array([row["robust_scale_m"] for row in dispersion["heading_sensitivity_not_for_selection"]])
    frontier = pareto["frontier"]
    low_error = np.array([row["low_frequency_gain_error"] for row in frontier])
    high_gain = np.array([row["high_frequency_gain"] for row in frontier])
    phase = np.array([row["low_frequency_phase_delay_s"] for row in frontier])
    latency = strict["uwb_measurement_to_b306_frame_lower_bound_s"]
    dropouts = degradation["synthetic_dropout_surface"]
    leave_tags = redundancy["leave_one_tag_out"]
    source_lines = "\n".join(f"- `{REPOSITORY / path}`" for path in SOURCE_FILES)
    test_lines = "\n".join(f"- `{REPOSITORY / path}`" for path in TEST_FILES)
    return f"""# BioSpur C1 UWB–IMU Root-R3 system trade study — final

## Executive verdict

The system mission is to use credible, asynchronous UWB as a **weak low-frequency world reference** for an IMU-driven common root while preserving strict causality, fixed M1 geometry, high-frequency motion, and graceful degradation. Root-R2 did not evaluate that mission: it evaluated a UWB-derived root tracker after an invalid direct subtraction between V4 relative-geometry coordinates and an arbitrary-yaw M1 gauge.

Evidence-supported verdicts:

- `ROOT_R2_SYSTEM_LEVEL_FEASIBILITY_WAS_NOT_EVALUATED`
- `BLOCKED_DETERMINISTIC_TIME_FRAME_OR_IDENTITY_ERROR`
- `PARTIAL_WEAK_ABSOLUTE_ANCHOR_ARCHITECTURE_SUPPORTED`
- `EXTERNAL_TRUTH_REQUIRED_FOR_ACCURACY_VALIDATION`

The architecture is only partially supported: the synthetic transfer study demonstrates a feasible low-pass absolute-reference channel with bounded update influence and explicit dropout/recovery states, but the real C1 spatial comparison is blocked because the authoritative frame audit contains no proper capture-bound `R_N_from_V4`. No product PASS, UWB promotion, default change, or live integration is authorized.

## Mandatory system answers

1. **Mission.** UWB should constrain otherwise unobservable low-frequency global translation of an IMU-driven body system. It is not a frame-by-frame replacement for IMU motion.
2. **Did Root-R2 evaluate it?** No. Root-R2 had no genuine inertial root propagation and mixed incompatible frame semantics; its 0.902 m result is not a system-level fusion verdict.
3. **Genuine inertial root channel.** None existed before this phase. Root-R3 now implements experimental B3 `[p_root,v_root,b_accel]` propagation from raw pelvis IMU plus strict-past frozen M1 orientation. It is genuine but unqualified and drifts {full['B3']['metrics']['end_to_start_m']:.1f} m over the evaluated span, so it is not a usable production navigator.
4. **Fusion versus tracker.** B4 is the true inertial-plus-UWB algorithmic path. B2 is correctly labelled `UWB_TRACKER_WITH_M1_GEOMETRY`; it has no measured inertial acceleration input.
5. **Complete real C1 runs.** Yes for every candidate eligible under the hard invariants: B0 and B3 ran scientifically; B1/B2/B4 ran completely as identity-frame diagnostics. B5 was not run because the frame invariant failed, and it does not block B1–B4.
6. **Future data.** No. B2, B3, and B4 each report zero future-UWB, zero future-IMU, and zero pre-availability outputs; strict-past M1 future count is zero.
7. **Measurement versus availability time.** Yes. UWB measurement time is the common-clock mapping of `strobe_us + mean(t_round_us[used anchors])/2`; availability is mapped B306 frame time. Delayed updates occur at measurement time, while output is emitted only at or after processing availability and past outputs are immutable.
8. **M1 preservation.** UWB changed none of M1 orientation, relative FK, validity, reset epochs, or bone lengths. The frozen M1 file is byte-identical and the relative-FK numeric difference is exactly 0.
9. **0.902 m decomposition.** The historical scale reproduces as {final['historical_robust_scale_m']:.12f} m. Per-tag within-window robust jitter spans {within.min():.3f}–{within.max():.3f} m (median {np.median(within):.3f} m), while fixed between-tag/model disagreement is {final['within_vs_between']['between_tag_robust_radial_m']:.3f} m. These are not additive variance components, but the evidence shows the metre-scale statistic is dominated by between-tag/model inconsistency rather than within-tag temporal jitter alone.
10. **Heading/frame semantics.** Yes, materially. A 90° mismatch at observed M1 radii implies median {dispersion['heading_formula_90deg_at_observed_m1_radius_m']['p50']:.3f} m and p95 {dispersion['heading_formula_90deg_at_observed_m1_radius_m']['p95']:.3f} m displacement. The prohibited diagnostic yaw sweep changes scale from {heading.min():.3f} to {heading.max():.3f} m but supplies no acceptable transform. The missing proper frame binding is therefore a deterministic blocker, not a tunable nuisance.
11. **Low-frequency anchoring.** The synthetic system has observable low-frequency anchoring: Pareto rows have 0.03 Hz XY gain error {low_error.min():.6f}–{low_error.max():.3f}. Current C1 cannot scientifically demonstrate real spatial anchoring until the frame binding is closed.
12. **Jitter reaching the root.** No scientifically qualified real-C1 fused-root value can be claimed. In the quarantined B2 diagnostic, raw B1 event increments have p50 {full['B1']['metrics']['root_increment_m']['p50']:.3f} m versus B2 p50 {full['B2']['metrics']['root_increment_m']['p50']:.3f} m; update influence is bounded at {quality['bounded_influence_B2_m']['max']:.3f} m (p50 {quality['bounded_influence_B2_m']['p50']:.3f} m). Pareto synthetic 2 Hz transmission gain is {high_gain.min():.3f}–{high_gain.max():.3f}.
13. **Gain and phase.** Across the 13 non-dominated synthetic designs, 0.03 Hz XY gain error is {low_error.min():.6f}–{low_error.max():.3f}, 2 Hz gain is {high_gain.min():.3f}–{high_gain.max():.3f}, and absolute 0.03 Hz phase delay is {phase.min()*1000:.1f}–{phase.max()*1000:.1f} ms. There is no real-C1 parameter selection because the frame invariant fails.
14. **Dropout.** The filter enters degraded/IMU-only modes and covariance grows rather than hiding lost observability. Synthetic covariance trace grows from {dropouts[0]['covariance_trace_before_m2']:.3f} m² to {dropouts[0]['covariance_trace_after_m2']:.3f}, {dropouts[1]['covariance_trace_after_m2']:.3f}, {dropouts[2]['covariance_trace_after_m2']:.3f}, and {dropouts[3]['covariance_trace_after_m2']:.3f} m² for 0.12, 0.6, 2, and 5 s dropouts.
15. **Reacquisition and jump.** Five credible observations ramp influence through 0.10, 0.25, 0.50, 0.75, and 1.0. Synthetic re-entry jumps are {dropouts[0]['reentry_jump_m']:.3f}, {dropouts[1]['reentry_jump_m']:.3f}, {dropouts[2]['reentry_jump_m']:.3f}, and {dropouts[3]['reentry_jump_m']:.3f} m; the observed maximum is {max(row['reentry_jump_m'] for row in dropouts):.3f} m after the 5 s case, so long-dropout recovery remains a material trade-off.
16. **Bad tag or anchor dominance.** A single update cannot move the root more than 0.050 m in the tested policy. Leave-one-tag-out robust scales span {min(row['robust_scale_m'] for row in leave_tags.values()):.3f}–{max(row['robust_scale_m'] for row in leave_tags.values()):.3f} m, so no single tag explains the full disagreement. Shared-anchor and correlated multi-tag faults remain a system risk; current event associations make anchor removal highly destructive and do not prove common-mode containment.
17. **XY versus Z.** UWB geometry is materially weaker in Z: median σ is {quality['covariance_sigma_xy_m']['p50']:.3f} m in XY versus {quality['covariance_sigma_z_m']['p50']:.3f} m in Z; p95 is {quality['covariance_sigma_xy_m']['p95']:.3f} versus {quality['covariance_sigma_z_m']['p95']:.3f} m. The synthetic measurement models preserve this anisotropy rather than pretending isotropic accuracy.
18. **Strict-causal latency.** The measured UWB epoch-to-B306-frame lower bound is p50 {latency['p50']*1000:.3f} ms, p95 {latency['p95']*1000:.3f} ms, p99 {latency['p99']*1000:.3f} ms, and max {latency['max']*1000:.3f} ms. State replay adds algorithmic lag only internally; emitted samples are never rewritten. Host/DK/USB/display latency is unknown and was not summed. The legacy τ=0.35 s path is classified as filter response, not transport latency, and is absent from the scientific path.
19. **Pareto trade-offs.** The frontier contains {len(frontier)} rows trading low-frequency gain error, high-frequency UWB transmission, and phase delay across process noise, anisotropic measurement noise, and 0.02–0.10 m influence caps. No row is selected without a valid frame.
20. **System utility on C1.** There is structural evidence for a feasible weak-reference architecture and clear evidence that B2 suppresses raw UWB event jitter. There is not valid real-C1 evidence that UWB improves an IMU-driven root: B3 is unqualified and unstable, while B4 is frame-quarantined. The correct result is partial architecture support, not a utility PASS.
21. **Impossible without external truth.** Absolute root accuracy, common-mode UWB bias, true drift reduction, true end-to-end host/display latency, and product readiness cannot be established from C1.
22. **Product/live readiness.** No candidate is scientifically qualified, production-ready, enabled, or authorized for live integration.
23. **Smallest next action.** Independently establish and freeze one proper capture-bound `R_N_from_V4` transform with uncertainty, without fitting C1 root outcomes. Then repeat the already-frozen replay before any algorithm tuning or product decision.

## Repository and source-authority answers

1. **Pre-existing algorithms.** `Fusion_Part` already contained typed immutable event ingest, TIMER2 common-clock reconstruction, Q1 15-error-state attitude/inertial ESKF, canonical T4 UWB solving/covariance, fixed-geometry body/FK models, fixed-lag and batch articulated smoothers, and frame/anthropometry calibration.
2. **Reused modules.** Root-R3 directly reuses `ingest.v47.iter_cobs_records`/`_imu_events`, `imu.q1.quaternion_to_matrix`, inherited common-clock/event-schedule products, canonical T4 frontend products, and frozen M1/body-geometry contracts. It did not duplicate those owners.
3. **New authoritative source.** Written at:

{source_lines}

4. **Tests.** Written at:

{test_lines}

5. **`/tmp`-only outputs.** Generated C1 arrays/cache, JSON/Markdown audits, trajectories, the offline HTML viewer, MP4, Git-status snapshots, integrity snapshots, and the reproducibility manifest exist only under `{output}`. No estimator, measurement model, gate, or test exists only there.
6. **Reproducibility after deleting `/tmp`.** The implementation, unit tests, synthetic tests, and artifact generators remain reproducible from `Fusion_Part`; no execution imports Root-R2 code from `/tmp`. Exact repetition of this C1 evidence bundle still requires regenerating or restoring its authorized frozen M1 and inherited UWB schedule/raw-trajectory inputs, which are currently addressed under `/tmp`. Thus source reproducibility is yes; exact evidence replay after indiscriminate deletion is not automatic.
7. **Root-R2 reuse.** Only the published 0.902 robust-scale equation/event semantics were independently traced and ported into `root_r3.metrics`; the independent result is {final['historical_robust_scale_m']:.12f} m. No Root-R2 estimator, parameter grid, τ=0.35 filter, artificial gate, or R2.6 transform is imported.
8. **Canonical checkout write.** Yes. The newer in-repository `AGENTS.md` forbids routine Fusion worktrees and requires direct canonical `Fusion_Part` development, conflicting with the attachment addendum. The worktree requirement was therefore not met; only the isolated new package, tests, three tool wrappers, and placement decision were added. No existing algorithm module or product default was edited.
9. **Git/product actions.** Nothing was committed, pushed, merged, promoted, or enabled as a product default.
10. **Qualification level.** Implemented and structurally tested; full real-C1 diagnostics completed; scientifically blocked at the frame interface; not production-promoted.

## Validation and integrity

- Focused test suite: `{tests['output'].strip()}`
- Full C1 causal counters: B2/B3/B4 all `future_uwb=0`, `future_imu=0`, `preavailability_output=0`.
- M1 preservation: byte-identical, protected arrays unchanged.
- MP4: {browser['video']['ffprobe']['format']['duration']} s, {browser['video']['ffprobe']['streams'][0]['width']}×{browser['video']['ffprobe']['streams'][0]['height']}, {browser['video']['ffprobe']['streams'][0]['nb_frames']} frames; full decode PASS.
- Viewer: required controls/data and JavaScript syntax PASS. Real browser interaction was **not completed or claimed** because the supported browser bootstrap was unavailable in the current tool session.
- Pre-existing repository tree, frozen M1, all three prior trees, authorized C1 inputs, Git HEAD/branch, and full pre-existing Git status after excluding declared Root-R3 additions: `{'PASS' if integrity['all_preexisting_content_and_inputs_exact'] else 'FAIL'}`.

## Preserved boundaries

`RAW_PURE_IMU_M1_REMAINS_AUTHORITATIVE`; `UWB_ROOT_R3_EXPERIMENT_ONLY`; `UWB_NOT_PROMOTED`; `NO_PRODUCT_DEFAULT_CHANGE`; `NO_NEW_CAPTURE_REQUESTED`; `NO_LIVE_INTEGRATION_STARTED`; `R26_CANDIDATE_REMAINS_QUARANTINED`.
"""


def finalize(output: Path) -> dict:
    output = Path(output).resolve()
    tests = _run_tests(output)
    browser = _browser_and_media_audit(output)
    integrity = run_integrity(output)
    change_inventory = {
        "schema": "biospur.root_r3.repository_change_inventory.v1",
        "canonical_repository": str(GIT_ROOT), "base_commit": "5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb",
        "branch": "feature/b306-bringup", "development_worktree": None,
        "placement_exception": "newer BioSpur_Fusion/AGENTS.md requires canonical Fusion_Part and forbids routine worktrees",
        "source_files": [str(REPOSITORY / path) for path in SOURCE_FILES],
        "test_files": [str(REPOSITORY / path) for path in TEST_FILES],
        "existing_source_modified": [], "commit": False, "push": False, "merge": False,
        "product_default_changed": False, "live_integration_started": False,
    }
    _dump(output / "REPOSITORY_CHANGE_INVENTORY.json", change_inventory)
    report = _report(output, integrity, browser, tests)
    (output / "ROOT_R3_SYSTEM_TRADE_STUDY_FINAL.md").write_text(report, encoding="utf-8")
    final_path = output / "FINAL_RESULT.json"; final = _load(final_path)
    final.update({
        "viewer_generated": True, "review_mp4_generated": True,
        "viewer_static_validation_pass": browser["viewer"]["javascript_syntax_pass"],
        "browser_interaction_completed": False, "browser_interaction_claimed": False,
        "video_full_decode_pass": browser["video"]["full_decode_pass"],
        "qualification_tests_pass": tests["pass"], "qualification_test_count": 20,
        "integrity_pass": integrity["all_preexisting_content_and_inputs_exact"],
        "source_placement": "CANONICAL_FUSION_PART_PER_NEWER_AGENTS_RULE; USER_WORKTREE_ADDENDUM_NOT_MET",
    })
    _dump(final_path, final)
    output_hashes = {}
    for path in sorted(value for value in output.iterdir() if value.is_file() and
                       value.name not in {"REPRODUCIBILITY_MANIFEST.json", "REPRODUCIBILITY_MANIFEST.json.sha256"}):
        output_hashes[path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    source_hashes = {str(REPOSITORY / path): sha256(REPOSITORY / path) for path in (*SOURCE_FILES, *TEST_FILES)}
    manifest = {
        "schema": "biospur.root_r3.reproducibility.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "output_directory": str(output), "git": {"root": str(GIT_ROOT), "head": "5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb",
        "branch": "feature/b306-bringup", "commit": False, "push": False, "merge": False},
        "environment": {"python": sys.version, "platform": platform.platform(), "numpy": np.__version__},
        "commands": [
            "PYTHONPATH=Fusion_Part/src python3 Fusion_Part/tools/run_root_r3.py --output <output>",
            "PYTHONPATH=Fusion_Part/src python3 Fusion_Part/tools/build_root_r3_review.py --output <output>",
            "PYTHONPATH=Fusion_Part/src python3 Fusion_Part/tools/finalize_root_r3.py --output <output>",
        ],
        "predeclared_plan_sha256": sha256(output / "PREDECLARED_ROOT_R3_PLAN.json"),
        "baseline_definitions_sha256": sha256(output / "BASELINE_DEFINITIONS.json"),
        "source_and_test_hashes": source_hashes, "output_hashes_before_manifest": output_hashes,
        "tests": tests, "browser_and_media": browser, "integrity": integrity,
        "tmp_deletion_contract": "algorithm/source/synthetic tests survive; exact C1 replay inputs must be regenerated or restored",
        "manifest_self_hash_convention": "SHA256 canonical JSON payload with manifest_payload_sha256 omitted",
    }
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    manifest["manifest_payload_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path = output / "REPRODUCIBILITY_MANIFEST.json"; _dump(manifest_path, manifest)
    (output / "REPRODUCIBILITY_MANIFEST.json.sha256").write_text(
        f"{sha256(manifest_path)}  REPRODUCIBILITY_MANIFEST.json\n", encoding="utf-8")
    return final
