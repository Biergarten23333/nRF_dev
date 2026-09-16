from tools.profile_c2_full_session_reader_prefix import (
    TARGETS, _complete_records, _evaluate, _nearest_boundary,
    _residual_profile_processes,
)


def test_checkpoint_helpers_preserve_complete_boundaries_and_pass_stable_probe():
    assert TARGETS == tuple(value << 20 for value in
                            (2, 4, 6, 8, 16, 24, 32, 40, 48, 56, 64))
    buffer = bytearray(b"abc\0defg\0h\0")
    assert _complete_records(bytes(buffer)) == 3
    assert _nearest_boundary(buffer, 6, len(buffer)) == 4
    shape = {
        "last_timer": 20, "last_sequence": 20, "last_availability": 10,
        "last_ordering": 10, "pending_events": 0,
        "pending_event_digests": 0, "pending_sensor_digests": 0,
        "region_counts": 4, "imu_region_maps": 37,
    }
    checkpoints = []
    for byte_count, wall_s, rss in (
        (8 << 20, 1.0, 100_000), (16 << 20, 2.0, 101_000),
        (32 << 20, 4.0, 102_000), (64 << 20, 8.0, 103_000),
    ):
        checkpoints.append({
            "bytes": byte_count, "wall_s": wall_s, "rss_kib": rss,
            "cpu_s": wall_s,
            "fixed_container_sizes": dict(shape),
            "stage_cpu_s": {
                "route_inclusive": wall_s * 0.10,
                "delivery_inclusive": wall_s * 0.05,
                "digest_nested": wall_s * 0.08,
                "digest_in_route": wall_s * 0.05,
                "region_lookup_nested": wall_s * 0.01,
            },
        })
    result = _evaluate(checkpoints)
    assert result["all_gates_pass"] is True
    assert result["last_three_throughput_ratio"] == 1.0
    assert result["conservative_full_source_projection_s"] <= 360.0


def test_residual_inventory_excludes_self_and_ancestors_but_keeps_real_workers(tmp_path):
    rows = {
        100: (50, b"python\0profile_c2_full_session_reader_prefix.py\0seal\0"),
        50: (10, b"bash\0-c\0profile_c2_full_session_reader_prefix.py profile\0"),
        10: (1, b"timeout\060s\0profile_c2_full_session_reader_prefix.py\0"),
        200: (1, b"python\0profile_c2_full_session_reader_prefix.py\0profile\0"),
        300: (1, b"python\0-m\0pytest\0tests/test_c2_reader_prefix_profile.py\0"),
        400: (1, b"python\0unrelated.py\0"),
    }
    for pid, (parent, command) in rows.items():
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "status").write_text(f"Name:\ttest\nPPid:\t{parent}\n")
        (directory / "cmdline").write_bytes(command)
    observed = _residual_profile_processes(tmp_path, current_pid=100)
    assert tuple(row.split(":", 1)[0] for row in observed) == ("200", "300")
