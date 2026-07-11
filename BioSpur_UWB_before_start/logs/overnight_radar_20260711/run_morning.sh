#!/bin/bash
# Morning sequence for the overnight_radar_20260711 capture.
# Run after the capture completes (~12:59).  Quicklook first (fast health check),
# then the full analysis.
set -e
cd /mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start

echo "=== QUICKLOOK ==="
python3 logs/overnight_radar_20260711/quicklook_v2.py

echo ""
echo "=== FULL ANALYSIS ==="
python3 logs/overnight_radar_20260711/analysis/run_full_analysis.py

echo ""
echo "=== DONE ==="
