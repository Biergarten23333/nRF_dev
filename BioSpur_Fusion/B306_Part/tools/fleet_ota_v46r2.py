#!/usr/bin/env python3
"""Resumable fleet OTA driver; board truth comes only from durable verification."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ota_build_identity import atomic_write

ROOT = Path(__file__).resolve().parents[2]
B306 = ROOT / "B306_Part"
TOOLS = B306 / "tools"


def run_id() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("fleet_ota_%Y%m%d_%H%M%S")


def patch_command(action: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(B306 / "firmware/patches/sdk_patch.sh"), action],
                          capture_output=True, text=True, check=False)


def require_patch(action: str, expected: str | None = None) -> str:
    result = patch_command(action)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or (expected is not None and expected not in output):
        raise RuntimeError(f"SDK patch {action} failed rc={result.returncode}: {output}")
    return output


@contextmanager
def pristine_sdk(ledger: dict, write_ledger):
    try:
        ledger["sdk"]["revert"] = require_patch("revert")
        write_ledger()
        yield
    finally:
        try:
            ledger["sdk"]["apply"] = require_patch("apply")
            ledger["sdk"]["verify"] = require_patch("verify")
            ledger["sdk"]["restored"] = True
        except BaseException as exc:
            ledger["sdk"]["restored"] = False
            ledger["sdk"]["restore_error"] = f"{type(exc).__name__}: {exc}"
            write_ledger()
            raise
        write_ledger()


def durable_result(path: Path, node: str, expected_fwid: str,
                   expected_image_sha256: str | None = None) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    samples = value.get("samples") or []
    last = samples[-1] if samples else {}
    return (
        value.get("status") == "PASS"
        and value.get("board_state") == "TARGET_CONFIRMED"
        and value.get("node") == node
        and value.get("expected_fwid") == expected_fwid
        and last.get("node") == node
        and last.get("fwid") == expected_fwid
        and (expected_image_sha256 is None
             or last.get("image_sha256") == expected_image_sha256)
        and "confirmed=1" in str(last.get("boot_confirm"))
    )


def verifier_state(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "UNREACHABLE" if not path.exists() else "UNKNOWN"
    state = str(value.get("board_state", "UNKNOWN"))
    allowed = {"OLD_CONFIRMED", "TARGET_RUNNING_UNCONFIRMED", "TARGET_CONFIRMED",
               "TARGET_IDENTITY_MISMATCH", "ROLLBACK_OBSERVED", "UNREACHABLE", "UNKNOWN"}
    return state if state in allowed else "UNKNOWN"


def classify(txn_rc: int | None, durable: bool, observed: str = "UNKNOWN") -> str:
    if durable and txn_rc not in (None, 0):
        return "DURABLE_PASS_WITH_TXN_ERROR"
    if durable:
        return "TARGET_CONFIRMED"
    return observed


def run_live_verifier(command_template, *, node: str, out_dir: Path,
                      identity_path: Path, absolute_deadline: float,
                      timeout_s: float) -> tuple[int | None, Path, str | None]:
    """Invoke a fresh live verifier; never reuse transaction result JSON."""
    out_dir.mkdir(parents=True, exist_ok=False)
    result_path = out_dir / "result.json"
    command = [part.format(node=node, out_dir=str(out_dir),
                           identity_manifest=str(identity_path),
                           absolute_deadline=absolute_deadline)
               for part in command_template]
    try:
        with (out_dir / "console.log").open("xb") as log:
            completed = subprocess.run(command, cwd=ROOT, stdout=log,
                                       stderr=subprocess.STDOUT, check=False,
                                       timeout=timeout_s)
        return completed.returncode, result_path, None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return None, result_path, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", required=True, type=Path,
                        help="explicit JSON containing nodes, identity manifest, and commands")
    parser.add_argument("--out-root", type=Path, default=B306 / "logs")
    args = parser.parse_args()
    campaign = json.loads(args.campaign.read_text(encoding="utf-8"))
    identity_path = Path(campaign["identity_manifest"])
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    expected_fwid = str(identity["fwid"])
    expected_image_sha = str(identity["mcuboot_image_sha256"])
    nodes = list(campaign["nodes"])
    out = args.out_root / run_id()
    out.mkdir(parents=True, exist_ok=False)
    ledger_path = out / "ledger.json"
    ledger = {
        "schema": "biospur-fleet-ota-ledger-v1", "run_id": out.name,
        "campaign": str(args.campaign), "expected_fwid": expected_fwid,
        "expected_payload_sha256": identity["signed_payload_sha256"],
        "expected_image_sha256": expected_image_sha,
        "boards": {node: {"state": "UNKNOWN", "transitions": []} for node in nodes},
        "sdk": {},
    }

    def save() -> None:
        atomic_write(ledger_path, ledger)

    save()
    # Building updater images is the only operation requiring a pristine SDK.
    # Commands are explicit arrays in the campaign; no stale embedded paths or
    # restore hashes can silently enter this driver.
    try:
        with pristine_sdk(ledger, save):
            for command in campaign.get("build_commands", []):
                completed = subprocess.run(command, cwd=ROOT, check=False,
                                           capture_output=True, text=True,
                                           timeout=float(campaign["build_timeout_s"]))
                if completed.returncode != 0:
                    raise RuntimeError(f"updater build failed rc={completed.returncode}")
    except BaseException as exc:
        ledger["driver_error"] = f"{type(exc).__name__}: {exc}"
        save()
        return 2

    for node in nodes:
        row = ledger["boards"][node]
        board_dir = out / node
        board_dir.mkdir(exist_ok=False)
        transaction_started = time.monotonic()
        absolute_deadline = transaction_started + float(campaign["critical_deadline_s"])
        row["transaction_started_monotonic"] = transaction_started
        row["absolute_confirm_deadline"] = absolute_deadline
        try:
            command = [part.format(node=node, out_dir=str(board_dir),
                                   identity_manifest=str(identity_path))
                       for part in campaign["transaction_command"]]
            log_path = board_dir / "transaction_console.log"
            with log_path.open("xb") as log:
                completed = subprocess.run(command, cwd=ROOT, stdout=log,
                                           stderr=subprocess.STDOUT, check=False,
                                           timeout=float(campaign["transaction_timeout_s"]))
            row["txn_rc"] = completed.returncode
            row["transaction_log"] = str(log_path)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            row["txn_rc"] = None
            row["transaction_error"] = f"{type(exc).__name__}: {exc}"
        row["transitions"].append({"state": "VERIFYING", "txn_rc": row["txn_rc"]})
        save()
        # Always attempt a separate live rescue. If the production master was
        # not restored its explicit marker gate fails without touching B306.
        rescue_rc, rescue_path, rescue_error = run_live_verifier(
            campaign["verifier_command"], node=node,
            out_dir=board_dir / "confirm_rescue", identity_path=identity_path,
            absolute_deadline=absolute_deadline,
            timeout_s=float(campaign["verifier_timeout_s"]))
        row["rescue_rc"] = rescue_rc
        row["rescue_error"] = rescue_error
        row["rescue_result"] = str(rescue_path)
        durable = durable_result(rescue_path, node, expected_fwid, expected_image_sha)
        row["state"] = classify(row["txn_rc"], durable, verifier_state(rescue_path))
        row["transitions"].append({"state": row["state"]})
        save()

    # Genuinely live independent final pass: each board gets a new connection,
    # identity query, and BOOT CONFIRM STATUS in a separate evidence directory.
    final_root = out / "final_live_verification"
    final_root.mkdir(exist_ok=False)
    ledger["final_verification"] = {"schema": "biospur-fleet-live-final-v1",
                                    "boards": {}}
    for node in nodes:
        deadline = time.monotonic() + float(campaign["final_verify_deadline_s"])
        rc, result_path, error = run_live_verifier(
            campaign["verifier_command"], node=node,
            out_dir=final_root / node, identity_path=identity_path,
            absolute_deadline=deadline,
            timeout_s=float(campaign["verifier_timeout_s"]))
        passed = durable_result(result_path, node, expected_fwid, expected_image_sha)
        ledger["final_verification"]["boards"][node] = {
            "durable": passed, "rc": rc, "error": error,
            "result": str(result_path)}
        save()
    save()
    return 0 if ledger["sdk"].get("restored") and all(
        row["durable"] for row in ledger["final_verification"]["boards"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
