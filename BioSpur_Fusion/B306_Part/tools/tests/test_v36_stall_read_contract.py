from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dk_v31_specific_reader_is_bounded_and_independent():
    source = (ROOT / "host/fusion_master/src/main.c").read_text()
    assert 'FUSION_MASTER_MARKER "dk-fusion-imu-relay-v31"' in source
    assert "BSF_BLE_UUID_STALL_W32" in source
    assert "bt_gatt_read(peer->conn, &peer->stall_read_params)" in source
    assert "peer->stall_read_params.func != NULL" in source
    assert "compatibility=pre_v36" in source
    assert "peer->telemetry_value_handle + 2u" in source
    callback = source[source.index("static uint8_t stall_read_cb("):]
    callback = callback[:callback.index("static int start_stall_read(")]
    assert "FUSION_STALL_READ" in callback
    assert "bt_gatt_write_without_response" not in callback


def test_capture_reads_immediately_and_every_five_seconds():
    source = (ROOT / "tools/n3_overnight_capture.py").read_text()
    assert "DATA_PLANE_SILENT" in source
    assert "ch.send(f'{n} STALL READ')" in source
    assert "next_stall_read[n]=now+5" in source
    assert "reason':'silent_retry'" in source
    assert "next_stall_read.pop(n,None)" in source
