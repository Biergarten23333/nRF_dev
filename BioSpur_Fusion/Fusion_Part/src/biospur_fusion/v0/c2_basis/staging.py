"""Shared bounded-stage deadlines and causal execution traces."""
from __future__ import annotations

from dataclasses import dataclass, field
import signal
import threading
import time
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class StageDeadlineExceeded(RuntimeError):
    """Fail-closed cancellation of work that exceeded its preregistered wall."""

    def __init__(self, stage: str, wall_limit_s: float, elapsed_s: float, label: str):
        self.stage = stage
        self.wall_limit_s = float(wall_limit_s)
        self.elapsed_s = float(elapsed_s)
        self.label = label
        super().__init__(
            f"{stage} exceeded {wall_limit_s:.3f}s during {label} "
            f"(elapsed={elapsed_s:.3f}s); work cancelled"
        )


@dataclass
class BoundedStage:
    """One monotonic shared deadline; callbacks are cancellable on POSIX."""

    name: str
    wall_limit_s: float
    started_s: float = field(default_factory=time.monotonic)
    trace: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not self.name or float(self.wall_limit_s) <= 0.0:
            raise ValueError("bounded stage requires a name and positive wall limit")
        self.wall_limit_s = float(self.wall_limit_s)
        self.trace.append({"event": "BEGIN", "elapsed_s": 0.0})

    def elapsed_s(self) -> float:
        return float(time.monotonic() - self.started_s)

    def remaining_s(self) -> float:
        return max(0.0, self.wall_limit_s - self.elapsed_s())

    def checkpoint(self, label: str, **details: Any) -> None:
        elapsed = self.elapsed_s()
        self.trace.append({"event": "CHECKPOINT", "label": label, "elapsed_s": elapsed, **details})
        if elapsed > self.wall_limit_s:
            self.cancelled = True
            raise StageDeadlineExceeded(self.name, self.wall_limit_s, elapsed, label)

    def _run(self, label: str, callback: Callable[[], T], *, record: bool) -> T:
        if record:
            self.checkpoint(f"{label}:begin")
        elif self.elapsed_s() > self.wall_limit_s:
            self.cancelled = True
            raise StageDeadlineExceeded(
                self.name, self.wall_limit_s, self.elapsed_s(), label,
            )
        remaining = self.remaining_s()
        if remaining <= 0.0:
            self.cancelled = True
            raise StageDeadlineExceeded(self.name, self.wall_limit_s, self.elapsed_s(), label)
        can_interrupt = (
            threading.current_thread() is threading.main_thread()
            and hasattr(signal, "setitimer")
            and hasattr(signal, "ITIMER_REAL")
        )
        previous_handler = None
        previous_timer = None
        if can_interrupt:
            previous_handler = signal.getsignal(signal.SIGALRM)
            previous_timer = signal.getitimer(signal.ITIMER_REAL)

            def alarm_handler(_signum, _frame):
                self.cancelled = True
                raise StageDeadlineExceeded(
                    self.name, self.wall_limit_s, self.elapsed_s(), label,
                )

            signal.signal(signal.SIGALRM, alarm_handler)
            signal.setitimer(signal.ITIMER_REAL, remaining)
        try:
            result = callback()
        except StageDeadlineExceeded:
            if record:
                self.trace.append({
                    "event": "CANCELLED", "label": label,
                    "elapsed_s": self.elapsed_s(),
                })
            raise
        finally:
            if can_interrupt:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
                signal.signal(signal.SIGALRM, previous_handler)
                if previous_timer and previous_timer[0] > 0.0:
                    signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        if record:
            self.checkpoint(f"{label}:complete")
        return result

    def run(self, label: str, callback: Callable[[], T]) -> T:
        """Run and trace one callback under the remaining POSIX wall deadline."""

        return self._run(label, callback, record=True)

    def run_quiet(self, label: str, callback: Callable[[], T]) -> T:
        """Run a fine-grained callback under the deadline without trace expansion."""

        return self._run(label, callback, record=False)

    def report(self, *, complete: bool = True) -> dict[str, Any]:
        elapsed = self.elapsed_s()
        return {
            "stage": self.name,
            "wall_limit_s": self.wall_limit_s,
            "elapsed_s": elapsed,
            "within_wall_limit": elapsed <= self.wall_limit_s,
            "complete": bool(complete),
            "cancelled": self.cancelled,
            "deadline_is_shared_not_per_callback": True,
            "trace": list(self.trace),
        }


def pipeline_wall_limit(config: dict | Any, mode: str, stage: str) -> float:
    try:
        value = config["bounded_pipeline"]["wall_limits_s"][mode][stage]
    except KeyError as exc:
        raise ValueError(f"missing bounded pipeline wall: {mode}.{stage}") from exc
    value = float(value)
    if value <= 0.0:
        raise ValueError(f"nonpositive bounded pipeline wall: {mode}.{stage}")
    return value
