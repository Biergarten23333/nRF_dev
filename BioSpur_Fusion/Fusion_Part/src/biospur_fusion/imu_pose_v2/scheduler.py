from __future__ import annotations

from collections.abc import Iterable

from .estimator import ContinuousArticulatedEstimator
from .types import FrontendFrame, PoseTick


PERIOD_NS_50HZ = 20_000_000


def fixed_grid(start_ns: int, stop_ns: int) -> range:
    if stop_ns < start_ns:
        raise ValueError("negative output domain")
    return range(start_ns, stop_ns, PERIOD_NS_50HZ)


def scheduled_replay(
    estimator: ContinuousArticulatedEstimator,
    frames: Iterable[FrontendFrame],
    node_ids: tuple[str, ...],
    start_ns: int,
    stop_ns: int,
    *,
    unavailable_after_ns: int = 2_000_000_000,
) -> tuple[PoseTick, ...]:
    ordered = sorted(frames, key=lambda row: (row.sample_time_ns, row.node_id, row.sample_uid))
    latest: dict[str, FrontendFrame] = {}
    cursor = 0
    output: list[PoseTick] = []
    for scheduled_ns in fixed_grid(start_ns, stop_ns):
        while cursor < len(ordered) and ordered[cursor].sample_time_ns <= scheduled_ns:
            latest[ordered[cursor].node_id] = ordered[cursor]
            cursor += 1
        if set(latest) != set(node_ids):
            # The formal denominator starts only after the declared all-node
            # initialization condition; calling earlier is a contract error.
            raise ValueError("all-node initialization condition not met")
        routed = {}
        for node in node_ids:
            row = latest[node]
            if scheduled_ns - row.sample_time_ns > unavailable_after_ns and row.status != "UNAVAILABLE":
                row = FrontendFrame(
                    row.node_id, row.boot_epoch, row.sample_uid, row.sample_time_ns,
                    row.q_WI, row.gyro_bias, row.accel_bias, row.covariance,
                    row.rest_detected, "UNAVAILABLE", row.input_age_ns, row.reset_epoch,
                )
            routed[node] = row
        output.append(estimator.update(scheduled_ns, routed))
    return tuple(output)
