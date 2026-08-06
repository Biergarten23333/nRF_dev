"""Per-connection loss baselines and independent UWB/IMU liveness alarms."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Mapping

STREAMS = ("uwb", "imu")


def u32_delta(before: int, after: int) -> int:
    return (after - before) & 0xFFFFFFFF


@dataclass
class ConnectionEpochLoss:
    baselines: dict[str, dict[str, int]] = field(default_factory=dict)

    def connected(self, node: str, snapshot: Mapping[str, int | str] | None = None) -> None:
        self.baselines[node] = {}
        if snapshot is not None:
            self.observe(node, snapshot)

    def disconnected(self, node: str) -> None:
        self.baselines.pop(node, None)

    def observe(self, node: str, snapshot: Mapping[str, int | str]) -> dict[str, int | None]:
        base = self.baselines.setdefault(node, {})
        result: dict[str, int | None] = {}
        for stream in STREAMS:
            key = f"q_drop_{stream}"
            current = int(snapshot[key], 0) if isinstance(snapshot[key], str) else int(snapshot[key])
            if key not in base:
                base[key] = current
                result[stream] = None
            else:
                result[stream] = u32_delta(base[key], current)
        return result


@dataclass
class StreamLiveness:
    threshold_s: float = 3.0
    last_uwb: dict[str, float] = field(default_factory=dict)
    last_imu: dict[str, float] = field(default_factory=dict)
    active_alarm: set[str] = field(default_factory=set)

    def connected(self, node: str, now: float) -> None:
        self.last_uwb[node] = now
        self.last_imu[node] = now
        self.active_alarm.discard(node)

    def note(self, node: str, stream: str, now: float) -> None:
        (self.last_uwb if stream == "uwb" else self.last_imu)[node] = now
        if stream == "imu":
            self.active_alarm.discard(node)

    def check(self, node: str, now: float) -> dict[str, float] | None:
        uwb_age = now - self.last_uwb.get(node, now)
        imu_age = now - self.last_imu.get(node, now)
        if uwb_age <= self.threshold_s and imu_age > self.threshold_s:
            if node not in self.active_alarm:
                self.active_alarm.add(node)
                return {"uwb_age_s": uwb_age, "imu_age_s": imu_age,
                        "threshold_s": self.threshold_s}
        else:
            self.active_alarm.discard(node)
        return None
