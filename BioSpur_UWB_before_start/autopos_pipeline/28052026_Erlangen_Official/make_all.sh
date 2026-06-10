#!/usr/bin/env bash
set -euo pipefail

python3 audit.py --data-dir . --out-dir reports
python3 run_phase1.py --data-dir . --out-dir reports
