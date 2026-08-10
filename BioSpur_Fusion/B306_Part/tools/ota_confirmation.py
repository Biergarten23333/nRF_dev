"""Pure, testable B306 durable-confirmation state machine."""

from __future__ import annotations

import enum
import re
import time
from dataclasses import asdict, dataclass
from typing import Callable


class BoardState(str, enum.Enum):
    OLD_CONFIRMED = "OLD_CONFIRMED"
    TARGET_RUNNING_UNCONFIRMED = "TARGET_RUNNING_UNCONFIRMED"
    TARGET_CONFIRMED = "TARGET_CONFIRMED"
    TARGET_IDENTITY_MISMATCH = "TARGET_IDENTITY_MISMATCH"
    ROLLBACK_OBSERVED = "ROLLBACK_OBSERVED"
    UNREACHABLE = "UNREACHABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class ExpectedIdentity:
    node: str
    firmware_marker: str
    fwid: str
    image_sha256: str
    source_fwid: str | None = None
    source_image_sha256: str | None = None


@dataclass
class Sample:
    elapsed_s: float
    reply: str | None = None
    error: str | None = None
    node: str | None = None
    firmware_marker: str | None = None
    fwid: str | None = None
    image_sha256: str | None = None
    boot_confirm: str | None = None


FIELDS = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
HEX64 = re.compile(r"[0-9a-f]{64}")
RETRYABLE = ("bridge_not_ready", "not_connected", "reason=syntax", "truncated")


def fields(text: str) -> dict[str, str]:
    return dict(FIELDS.findall(text))


class ConfirmationTimeout(RuntimeError):
    def __init__(self, message: str, state: BoardState, samples: list[dict[str, object]]):
        super().__init__(message)
        self.state = state
        self.samples = samples


def timeout_message(elapsed: float, samples: list[Sample]) -> str:
    last = samples[-1] if samples else Sample(elapsed_s=elapsed)
    return (
        f"confirmation deadline expired elapsed_s={elapsed:.3f} "
        f"samples={len(samples)} last_reply={last.reply!r} "
        f"last_error={last.error!r} last_identity={last.fwid!r} "
        f"last_image_sha256={last.image_sha256!r} "
        f"last_boot_confirm={last.boot_confirm!r}"
    )


def confirm_until_durable(
    expected: ExpectedIdentity,
    query_ping: Callable[[], str],
    query_status: Callable[[], str],
    prepare_commit: Callable[[], None],
    *,
    absolute_deadline: float,
    poll_s: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[BoardState, list[dict[str, object]]]:
    """Poll identity and confirmation under one deadline.

    The caller supplies transport functions, making retries deterministic in
    tests. Identity is exact only when the running application reports the
    manifest FWID; markers and command capabilities are deliberately ignored.
    """
    started = clock()
    samples: list[Sample] = []
    target_seen = False
    commit_sent = False
    while True:
        now = clock()
        elapsed = now - started
        if now >= absolute_deadline:
            state = (BoardState.TARGET_RUNNING_UNCONFIRMED if target_seen
                     else BoardState.UNREACHABLE)
            raise ConfirmationTimeout(timeout_message(elapsed, samples), state,
                                      [asdict(s) for s in samples])
        sample = Sample(elapsed_s=round(elapsed, 6))
        try:
            reply = query_ping()
            sample.reply = reply
            parsed = fields(reply)
            sample.node = parsed.get("name")
            sample.firmware_marker = parsed.get("fw")
            sample.fwid = parsed.get("fwid")
            sample.image_sha256 = parsed.get("image_sha")
            if sample.node != expected.node:
                samples.append(sample)
                return BoardState.TARGET_IDENTITY_MISMATCH, [asdict(s) for s in samples]
            target_shape = (
                sample.firmware_marker == expected.firmware_marker
                and HEX64.fullmatch(sample.fwid or "") is not None
                and sample.fwid != "0" * 64
                and HEX64.fullmatch(sample.image_sha256 or "") is not None
                and sample.image_sha256 != "0" * 64
            )
            target_identity = (target_shape and sample.fwid == expected.fwid and
                               sample.image_sha256 == expected.image_sha256)
            source_identity = (
                expected.source_fwid is not None
                and sample.fwid == expected.source_fwid
                and sample.image_sha256 == expected.source_image_sha256
            )
            if not target_identity:
                if target_seen:
                    samples.append(sample)
                    return BoardState.ROLLBACK_OBSERVED, [asdict(s) for s in samples]
                if source_identity:
                    status = query_status()
                    sample.boot_confirm = status
                    samples.append(sample)
                    if fields(status).get("confirmed") == "1":
                        return BoardState.OLD_CONFIRMED, [asdict(s) for s in samples]
                    return BoardState.UNKNOWN, [asdict(s) for s in samples]
                if sample.firmware_marker == expected.firmware_marker:
                    samples.append(sample)
                    return BoardState.TARGET_IDENTITY_MISMATCH, [asdict(s) for s in samples]
                samples.append(sample)
                sleep(poll_s)
                continue
            target_seen = True
            status = query_status()
            sample.boot_confirm = status
            samples.append(sample)
            status_fields = fields(status)
            if status_fields.get("confirmed") == "1":
                return BoardState.TARGET_CONFIRMED, [asdict(s) for s in samples]
            if status_fields.get("required") != "1":
                return BoardState.TARGET_IDENTITY_MISMATCH, [asdict(s) for s in samples]
            if not commit_sent:
                prepare_commit()
                commit_sent = True
        except Exception as exc:  # transport errors are evidence, then retried
            sample.error = f"{type(exc).__name__}: {exc}"
            samples.append(sample)
            if not any(token in str(exc) for token in RETRYABLE):
                # A transient controller timeout is indistinguishable from
                # reachability loss and remains bounded by the outer deadline.
                pass
        sleep(poll_s)
