"""Fail-closed assertion for every formal Fusion capture configuration."""
from __future__ import annotations

from fusion_session import parse_fields


def assert_formal_window_contract(*, tag_cfg: dict[str, object], fleet: dict[str, object],
                                  beacon_result: dict[str, object], expected_count: int,
                                  expected_period_ms: int, expected_beacon_us: int,
                                  expected_slots: dict[str, int] | None = None,
                                  max_wire_bytes: int = 191,
                                  acceptance: dict[str, object] | None = None) -> dict[str, object]:
    errors: list[str] = []
    aggregate = fleet.get("aggregate", fleet.get("listing", {}).get("aggregate", {}))
    generation = int(aggregate.get("spacing_generation", "-1"), 0)
    if aggregate.get("spacing") != "ON" or aggregate.get("spacing_us") != "5000":
        errors.append(f"spacing={aggregate.get('spacing')}/{aggregate.get('spacing_us')}")
    if generation <= 0:
        errors.append(f"spacing_generation={generation}")
    period_token = f"period_{expected_beacon_us // 1000}_seen"
    if not beacon_result.get(period_token):
        errors.append(f"beacon {expected_beacon_us} us not proven by {period_token}")
    for node, row in tag_cfg.items():
        command = str(row.get("command", ""))
        reply = str(row.get("reply", ""))
        fields = parse_fields(reply)
        required_command = (f"COUNT={expected_count}", f"PERIOD={expected_period_ms}",
                            "BEACON_SYNC=1", "RUN=1")
        if any(token not in command for token in required_command):
            errors.append(f"{node} command mismatch: {command}")
        slot = fields.get("SLOT", fields.get("slot", ""))
        accepted_by = (acceptance or {}).get(node, {}).get("accepted_by")
        if accepted_by not in (None, "reply", "behaviour"):
            errors.append(f"{node} invalid acceptance path={accepted_by}")
        if acceptance is not None and accepted_by is None:
            errors.append(f"{node} has no behavioral/reply acceptance")
        # Only the reply fast path must echo fields. A TIMEOUT accepted by the
        # data-plane witness deliberately has no reply fields to inspect.
        if accepted_by in (None, "reply"):
            if f"/{expected_count}" not in slot or fields.get("PERIOD") != str(expected_period_ms):
                errors.append(f"{node} reply mismatch: {reply}")
            if fields.get("BEACON_SYNC") != "1" or fields.get("RUN") != "1":
                errors.append(f"{node} sync/run mismatch: {reply}")
        if len(command.encode("ascii")) > max_wire_bytes:
            errors.append(f"{node} command wire length={len(command.encode('ascii'))}")
        if len(reply.encode("ascii")) > max_wire_bytes:
            errors.append(f"{node} reply wire length={len(reply.encode('ascii'))}")
        if expected_slots is not None:
            intended = expected_slots.get(node)
            command_slot = f"SLOT={intended}" if intended is not None else ""
            reply_slot = slot.split("/", 1)[0]
            reply_slot_bad = accepted_by in (None, "reply") and reply_slot != str(intended)
            if intended is None or command_slot not in command or reply_slot_bad:
                errors.append(f"{node} slot mismatch intended={intended}: {command} / {reply}")
    if expected_slots is not None:
        occupied = set(expected_slots.values())
        if set(tag_cfg) != set(expected_slots):
            errors.append("configured node set differs from intended slot map")
        if len(expected_slots) == 10 and occupied != set(range(1, 11)):
            errors.append(f"occupied slots={sorted(occupied)}, expected 1..10")
        if 11 in occupied:
            errors.append("guard slot 11 is occupied")
    result = {
        "expected": {"count": expected_count, "period_ms": expected_period_ms,
                     "beacon_period_us": expected_beacon_us, "spacing": "ON",
                     "spacing_us": 5000},
        "spacing_generation": generation,
        "nodes": len(tag_cfg),
        "occupied_slots": sorted(expected_slots.values()) if expected_slots else None,
        "guard_slot_11_empty": expected_slots is not None and 11 not in expected_slots.values(),
        "max_wire_bytes": max_wire_bytes,
        "pass": not errors,
        "errors": errors,
    }
    if errors:
        raise ValueError(f"formal-window contract mismatch: {errors}")
    return result
