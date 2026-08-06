#!/usr/bin/env python3
"""F3: deploy relay8.3-fix1 to the nine tags not handled by F2."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from relay8_1_batch_ota import bounded_readback, run_ota
from relay8_batch_ota import witness_no_target_uwb
from relay8_3_t567_batch_ota import ORDER, cfg_status

OLD_MARKER = "tag-fusion-link-relay8.3"
OLD_HASH = "7d9664d67a3bc87e2c00a9162f3e022baba68f508b89e009e47a625fd29b2009"
NEW_MARKER = "tag-fusion-link-relay8.3-fix1"
NEW_HASH = "b7395454e971aae771ec28aa469614c7bcbe5acef8f1d4f85f5852fcedc10530"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def assert_image(result: dict, marker: str, image_hash: str) -> dict[str, str]:
    version = result["query"]["version"]["reply"]["text"]
    imgstat = result["query"]["imgstat"]["reply"]["text"]
    if f"fw={marker}" not in version:
        raise RuntimeError(f"marker mismatch: {version}")
    if image_hash not in imgstat or "confirmed=1" not in imgstat:
        raise RuntimeError(f"image/confirmation mismatch: {imgstat}")
    return {"version": version, "imgstat": imgstat}


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    args = parser.parse_args()
    root = args.evidence_root
    root.mkdir(parents=True, exist_ok=False)
    ledger: list[dict] = []
    failures = 0
    for ordinal, (node, target) in enumerate(ORDER, start=2):
        board = root / f"{ordinal:02d}_{node.lower()}"
        board.mkdir()
        row: dict = {"node": node, "started": now(), "status": "IN_PROGRESS", "ota_write_retries": 0}
        ledger.append(row)
        write(root / "ledger.json", ledger)
        print(f"{node} START", flush=True)
        try:
            before = bounded_readback(node, board / "preota_query", discontinuity_epoch=None, poll_window_s=120.0)
            assert_image(before, OLD_MARKER, OLD_HASH)
            ota = run_ota(target, board)
            time.sleep(15.0)
            after = bounded_readback(node, board / "postota_query", discontinuity_epoch=None, poll_window_s=360.0)
            evidence = assert_image(after, NEW_MARKER, NEW_HASH)
            cfg = cfg_status(node, board / "postota_cfg_status")
            witness_no_target_uwb(node, board / "postota_silent_witness", duration_s=6.0)
            fields = cfg["fields"]
            row.update(
                ended=now(), status="COMPLETE", marker=NEW_MARKER,
                imgstat_hash=NEW_HASH, confirmed=1,
                boot=int(fields["boot"], 0), resetreas=fields["resetreas"],
                silent_state=fields["state"], silent_reason=fields["reason"],
                read_query_attempts={
                    "preota_version": len(before.get("version_attempts", [])),
                    "preota_imgstat": len(before.get("imgstat_attempts", [])),
                    "postota_version": len(after.get("version_attempts", [])),
                    "postota_imgstat": len(after.get("imgstat_attempts", [])),
                    "postota_cfg_status": len(cfg.get("attempts", [])),
                },
                version=evidence["version"], imgstat=evidence["imgstat"],
                ota_classification=ota.get("classification"), safe_state="unprovisioned_silent",
            )
            print(f"{node} COMPLETE boot={row['boot']} resetreas={row['resetreas']}", flush=True)
        except Exception as exc:
            failures += 1
            row.update(
                ended=now(), status="QUARANTINED",
                error=f"{type(exc).__name__}: {exc}",
                state_change_after_failure="none", ota_write_retries=0,
            )
            print(f"{node} QUARANTINED {row['error']}", flush=True)
        write(root / "ledger.json", ledger)
    summary = {"ended": now(), "failures": failures, "complete": len(ORDER) - failures, "ledger": ledger}
    write(root / "summary.json", summary)
    print(json.dumps({"complete": summary["complete"], "failures": failures}, indent=2))
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
