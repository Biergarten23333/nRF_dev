"""Frozen Stage 3 causal stationarity detector, reused without retuning."""

from pure_imu_baseline.stage3.stationarity import detect_native, map_to_display

__all__ = ["detect_native", "map_to_display"]
