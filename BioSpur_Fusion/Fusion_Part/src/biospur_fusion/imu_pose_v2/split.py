from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable


STATIC = frozenset({"00_initial_still", "02_t_pose"})
FINAL_STILL = "17_final_still"
CLASSES = frozenset({"CALIBRATION_FIT", "CALIBRATION_VALIDATION", "PROPAGATION_ONLY", "EXCLUDED_CORRUPT"})


@dataclass(frozen=True, slots=True)
class WindowSample:
    uid: str
    action_id: str
    phase: str
    cycle_id: str | None
    common_time_ns: int


def assign_frozen_split(rows: Iterable[WindowSample], *, master_seed: str) -> dict[str, str]:
    ordered = sorted(rows, key=lambda row: (row.action_id, row.common_time_ns, row.uid))
    if len({row.uid for row in ordered}) != len(ordered):
        raise ValueError("duplicate UID must be resolved before splitting")
    result: dict[str, str] = {}
    by_action: dict[str, list[WindowSample]] = {}
    for row in ordered:
        by_action.setdefault(row.action_id, []).append(row)
    for action, action_rows in by_action.items():
        formal = [row for row in action_rows if row.phase == "FORMAL_ACTION"]
        for row in action_rows:
            if row.phase != "FORMAL_ACTION":
                result[row.uid] = "PROPAGATION_ONLY"
        if action == FINAL_STILL:
            result.update({row.uid: "CALIBRATION_VALIDATION" for row in formal})
        elif action in STATIC:
            count = len(formal); fit_stop = int(count * .55); guard_stop = int(count * .65)
            for index, row in enumerate(formal):
                result[row.uid] = (
                    "CALIBRATION_FIT" if index < fit_stop else
                    "PROPAGATION_ONLY" if index < guard_stop else
                    "CALIBRATION_VALIDATION"
                )
        else:
            if any(row.cycle_id is None for row in formal):
                raise ValueError("dynamic formal sample lacks stable cycle ID")
            for row in formal:
                digest = hashlib.sha256(f"{master_seed}|{action}|{row.cycle_id}".encode()).digest()
                result[row.uid] = "CALIBRATION_FIT" if digest[0] & 1 == 0 else "CALIBRATION_VALIDATION"
    if set(result) != {row.uid for row in ordered} or not set(result.values()) <= CLASSES:
        raise RuntimeError("split is not an exact cover")
    return result


def deduplicate_uid_bytes(rows: Iterable[tuple[str, bytes]]) -> tuple[tuple[str, bytes], ...]:
    selected: dict[str, bytes] = {}
    for uid, payload in rows:
        if uid in selected and selected[uid] != payload:
            raise ValueError("conflicting duplicate UID")
        selected[uid] = payload
    return tuple(sorted(selected.items()))


def assert_validation_sealed_before_candidate(split: dict[str, str], numeric_opened_uids: Iterable[str]) -> None:
    invalid = {uid for uid in numeric_opened_uids if split.get(uid) == "CALIBRATION_VALIDATION"}
    if invalid:
        raise RuntimeError("validation measurement visibility before candidate is forbidden")


def assert_h_payload_sealed(candidate_frozen: bool, pre_h_gates_passed: bool) -> None:
    if not candidate_frozen or not pre_h_gates_passed:
        raise RuntimeError("H numeric decode is sealed until candidate and pre-H gates")
