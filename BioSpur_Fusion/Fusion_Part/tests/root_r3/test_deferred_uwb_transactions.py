import numpy as np

from biospur_fusion.root_r3 import CausalDelayedRootFilter, ImuSample
from biospur_fusion.root_r3.models import PositionObservation, RootState


def test_received_position_keeps_measurement_horizon_and_twenty_native_frames():
    source_ns = 234_841_692_976_175
    measurement_ns = 234_841_694_492_553
    availability_ns = 234_841_761_816_071
    root = CausalDelayedRootFilter(
        RootState(source_ns * 1e-9, np.zeros(9), np.eye(9)),
    )
    observation = PositionObservation(
        measurement_ns * 1e-9, availability_ns * 1e-9,
        np.array([0.01, 0.0, 0.0]), np.eye(3), "BSFC2CC",
        (0, 1, 2, 3), source_sequence=70464,
    )
    plan = root.prepare_received_position_at_measurement_horizon(observation)
    assert plan.processing_time_s == measurement_ns * 1e-9
    assert plan.last_availability_s == availability_ns * 1e-9
    root._apply_prevalidated_position(root._prevalidate_position_plan(plan))
    assert root.current_state.time_s == measurement_ns * 1e-9
    assert root.current_state.time_s < availability_ns * 1e-9

    for sequence in range(1, 21):
        native_ns = source_ns + sequence * 5_000_000
        assert root.add_imu(ImuSample(
            native_ns * 1e-9,
            max(availability_ns + sequence, native_ns) * 1e-9,
            np.array([0.0, 0.0, 9.80665]), np.eye(3), sequence,
        ))
    assert root.late_imu_rejected == 0
    assert root.current_state.time_s == (source_ns + 20 * 5_000_000) * 1e-9
