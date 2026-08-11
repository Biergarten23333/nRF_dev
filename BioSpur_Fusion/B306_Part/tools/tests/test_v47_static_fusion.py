import sys
import unittest
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from v47_real_data_adapter import IMU_DTYPE, UWB_DTYPE, sequence_gap_count  # noqa: E402
from v47_static_fusion import (  # noqa: E402
    InertialConfig, RangeConfig, euler_rpy_deg, normalize_quaternion,
    quaternion_from_two_vectors, quaternion_step, quaternion_to_matrix,
    range_jacobian, replay_inertial, replay_range_space,
    validate_bound_geometry_manifest,
)


def synthetic_imu(seconds=12, gyro_dps=(0.0, 0.0, 0.0)):
    count = seconds * 200
    raw = np.zeros(count, dtype=IMU_DTYPE)
    raw["b306_us"] = np.arange(count, dtype=np.uint64) * 5000 + 1_000_000
    raw["master_ms"] = 1000 + np.arange(count) // 200
    raw["seq"] = np.arange(count, dtype=np.uint16)
    raw["delta_us"] = (np.arange(count) % 5) * 5000
    raw["batch_n"] = 5
    acc = np.tile([0.0, 0.0, 1.0], (count, 1))
    gyro = np.tile(np.asarray(gyro_dps, dtype=float), (count, 1))
    times = np.arange(count) / 200.0
    return raw, acc, gyro, times


def synthetic_uwb(imu, count=100):
    raw = np.zeros(count, dtype=UWB_DTYPE)
    indices = np.linspace(10, len(imu) - 10, count, dtype=int)
    raw["strobe_us"] = imu["b306_us"][indices] + 1000
    raw["frame_us"] = raw["strobe_us"] + 15000
    raw["master_ms"] = 77_860_264 + np.arange(count) * 100
    raw["anchor_id"] = np.arange(8)
    raw["rank"] = np.arange(8)
    raw["valid_mask"] = 0xFF
    raw["range_mm"] = 2000
    raw["range_mm"][count // 2:, 0] = 2200
    raw["quality"] = 100
    raw["t_round_us"] = 1200 + np.arange(8) * 1000
    return raw


class StaticFusionTests(unittest.TestCase):
    def test_timestamp_order_batch_boundary_and_wrap(self):
        raw, *_ = synthetic_imu()
        self.assertTrue(np.all(np.diff(raw["b306_us"].astype(np.int64)) > 0))
        self.assertEqual(np.sum(np.diff(raw["delta_us"].astype(int)) < 0), len(raw) // 5 - 1)
        self.assertEqual(sequence_gap_count(np.array([65534, 65535, 0, 1], dtype=np.uint16), 65536), 0)

    def test_quaternion_normalization_and_gravity_convention(self):
        q = quaternion_from_two_vectors(np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(np.linalg.norm(q), 1.0, places=12)
        np.testing.assert_allclose(quaternion_to_matrix(q) @ np.array([1.0, 0.0, 0.0]), [0, 0, 1], atol=1e-12)
        q2 = quaternion_step(q, np.array([0.1, -0.2, 0.3]), .005)
        self.assertAlmostEqual(np.linalg.norm(normalize_quaternion(q2)), 1.0, places=12)

    def test_stationary_imu_and_gyro_bias(self):
        raw, acc, gyro, times = synthetic_imu(12, gyro_dps=(.5, -.25, .1))
        result = replay_inertial(raw, acc, gyro, times, np.ones(len(raw), bool), InertialConfig(zupt=True))
        np.testing.assert_allclose(np.degrees(result["initial_gyro_bias_rad_s"]), [.5, -.25, .1], atol=1e-12)
        valid = result["valid_snapshot_mask"]
        self.assertLess(np.nanmax(np.linalg.norm(result["velocity_mps"][valid], axis=1)), 1e-8)
        self.assertTrue(result["finite"])

    def test_zupt_is_measurement_update_and_suppresses_transient(self):
        raw, acc, gyro, times = synthetic_imu(14)
        acc[(times >= 7) & (times < 7.5), 0] = .2
        stationary = np.ones(len(raw), bool)
        stationary[(times >= 7) & (times < 7.5)] = False
        m1 = replay_inertial(raw, acc, gyro, times, stationary, InertialConfig(zupt=False))
        m2 = replay_inertial(raw, acc, gyro, times, stationary, InertialConfig(zupt=True))
        self.assertGreater(m2["zupt_updates"], 0)
        self.assertLess(np.linalg.norm(m2["velocity_mps"][12]), np.linalg.norm(m1["velocity_mps"][12]) * .1)
        self.assertGreater(np.nanmax(np.linalg.norm(m2["velocity_mps"][7:9], axis=1)), 0)

    def test_covariance_psd_and_determinism(self):
        raw, acc, gyro, times = synthetic_imu()
        cfg = InertialConfig(zupt=True)
        a = replay_inertial(raw, acc, gyro, times, np.ones(len(raw), bool), cfg)
        b = replay_inertial(raw, acc, gyro, times, np.ones(len(raw), bool), cfg)
        self.assertGreaterEqual(a["covariance_min_eigenvalue"], -1e-12)
        self.assertLessEqual(a["covariance_max_asymmetry"], 1e-14)
        np.testing.assert_array_equal(a["position_m"], b["position_m"])
        np.testing.assert_array_equal(a["cov_max_eig"], b["cov_max_eig"])

    def test_range_jacobian_matches_finite_difference(self):
        p = np.array([1.2, -0.7, 2.5]); a = np.array([-.2, .4, 1.0])
        analytic = range_jacobian(p, a)
        eps = 1e-6
        numeric = np.array([(np.linalg.norm(p + np.eye(3)[i] * eps - a) -
                             np.linalg.norm(p - np.eye(3)[i] * eps - a)) / (2 * eps) for i in range(3)])
        np.testing.assert_allclose(analytic, numeric, atol=1e-9)

    def test_uwb_accounting_async_insertion_and_reposition(self):
        imu, *_ = synthetic_imu()
        uwb = synthetic_uwb(imu)
        audit, result = replay_range_space(uwb, imu["b306_us"], np.ones(len(uwb), bool),
                                           np.full(8, 25.0), 25.0,
                                           RangeConfig(gate_enabled=False))
        self.assertEqual(result["slots_total"], 800)
        self.assertEqual(result["accepted"] + result["rejected"], result["valid"])
        self.assertEqual(result["valid"] + result["invalid"], result["slots_total"])
        self.assertEqual(result["insertion_errors"], 0)
        self.assertTrue(result["state_changed"])
        self.assertEqual(audit, [])

    def test_invalid_and_rejected_observations_are_audited(self):
        imu, *_ = synthetic_imu()
        uwb = synthetic_uwb(imu)
        uwb["valid_mask"][0] &= 0xFE
        uwb["range_mm"][-1, 1] = 65000
        audit, result = replay_range_space(uwb, imu["b306_us"], np.arange(len(uwb)) < 40,
                                           np.full(8, 20.0), 20.0, RangeConfig(gate_enabled=True))
        self.assertTrue(result["accounting_closed"])
        self.assertTrue(any(row["rejection_reason"] == "INVALID_MASK" for row in audit))
        self.assertTrue(any(row["rejection_reason"] == "NIS_GATE" for row in audit))

    def test_geometry_manifest_collision_and_mismatch_fail_closed(self):
        base = {"binding_status": "BOUND", "capture_id": "right", "coordinate_unit": "mm",
                "coordinate_frame": "room", "anchors": [
                    {"id": i, "x_mm": i, "y_mm": 0, "z_mm": 0, "delay_mm": 0} for i in range(8)],
                "provenance": {"source_sha256": "abc", "git_commit": "def"}}
        validate_bound_geometry_manifest(base, "right")
        with self.assertRaises(ValueError):
            validate_bound_geometry_manifest(base, "wrong")
        collision = {**base, "anchors": [dict(a) for a in base["anchors"]]}
        collision["anchors"][7]["id"] = 6
        with self.assertRaises(ValueError):
            validate_bound_geometry_manifest(collision, "right")
        with self.assertRaises(ValueError):
            validate_bound_geometry_manifest({"binding_status": "BLOCKED_GEOMETRY_BINDING"}, "right")


if __name__ == "__main__":
    unittest.main()

