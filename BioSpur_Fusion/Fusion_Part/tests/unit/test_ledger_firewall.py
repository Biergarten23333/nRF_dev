import numpy as np

from biospur_fusion.ingest.split import LedgerWindow, materialize_payload_firewall


def test_calibration_and_heldout_payloads_are_physically_separate(tmp_path):
    dtype = np.dtype([
        ("global_time_ns", "<i8"), ("raw_record_index", "<u8"),
        ("status", "u1"), ("payload", "<i4"),
    ])
    source = np.array([(t, i, 1, i * 7) for i, t in enumerate(range(10))], dtype=dtype)
    full = tmp_path / "full.npz"; np.savez(full, imu_node=source)
    cal = tmp_path / "calibration" / "ledger.npz"
    held = tmp_path / "heldout" / "ledger.npz"
    manifest = materialize_payload_firewall(
        full, cal, held, calibration_window=LedgerWindow("calibration", 1, 4),
        heldout_windows=(LedgerWindow("walk", 7, 8), LedgerWindow("final_still", 9, 9)),
    )
    with np.load(cal) as left, np.load(held) as right:
        assert left["imu_node"]["payload"].tolist() == [7, 14, 21, 28]
        assert right["imu_node"]["payload"].tolist() == [49, 56, 63]
    assert manifest["payload_overlap"] is False
    assert manifest["calibration"]["sha256"] != manifest["heldout"]["sha256"]
