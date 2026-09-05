#!/usr/bin/env python3
"""Finalize a checkpointed Capture2 held-link diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from biospur_fusion.c2_uwb_calibration.held_link_summary import (
    summarize_held_links,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    source = run / "HELD_LINK_CALIBRATION.json"
    status = json.loads((run / "RUN_STATUS.json").read_text())
    if status.get("status") != "COMPLETE" or status.get("remaining_tasks") != 0:
        raise ValueError("held-link run is incomplete")
    result = summarize_held_links(json.loads(source.read_text()))
    result["source"] = str(source)
    result["source_sha256"] = _sha256(source)
    result_path = run / "DIAGNOSTIC_RESULT.json"
    result_path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")

    overall = result["overall"]
    anchors = ", ".join(result["systematic_anchor_candidates"]) or "none"
    report = f"""# C2 UWB held-link diagnostic

All 80 node-anchor leave-one-anchor tasks completed with the sealed Beacon-only
B306 TIMER2 clock. LPD/LRD did not enter the clock or range model.

The independent-node solver is not promoted. It improved
{overall['new_better_links']}/80 links relative to T4. The median held-out
absolute residual was {overall['median_new_abs_residual_m']:.6f} m, versus
{overall['median_t4_abs_residual_m']:.6f} m for T4. These residuals combine the
held link with errors inherited from the seven positioning links and are not
direct NLOS truth.

Anchors with positive held residuals on at least eight of ten body nodes:
{anchors}. This cross-node structure requires separation of anchor-common
terms from node-anchor NLOS terms before body fusion.

Next: one shared FK/IK body/root posterior. Reliable UWB nodes constrain the
shared root; unavailable nodes receive kinematic propagation with increasing
uncertainty. No independent ten-position solution is accepted as final state.

Status: `DIAGNOSTIC_COMPLETE_INDEPENDENT_NODE_SOLVER_NOT_PROMOTED`.
`scientific_pass=false`.
"""
    (run / "DIAGNOSTIC_REPORT.md").write_text(report)

    artifacts = [
        run / "RUN_CONTRACT.json",
        run / "RUN_STATUS.json",
        run / "CALIBRATION_INPUT_LINEAGE.json",
        run / "FULL_Q_ACCEL.json",
        source,
        result_path,
        run / "DIAGNOSTIC_REPORT.md",
        *sorted((run / "HELD_LINK_CHECKPOINTS").glob("*.json")),
    ]
    (run / "SHA256SUMS").write_text("".join(
        f"{_sha256(path)}  {path.relative_to(run)}\n" for path in artifacts
    ))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
