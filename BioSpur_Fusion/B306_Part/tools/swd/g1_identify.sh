#!/usr/bin/env bash
# G1 -- target identification. READ ONLY: no reset, no halt, no write.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: g1_identify.sh <logdir> [expected-board]}"
EXPECT="${2:-BSF6C53}"
mkdir -p "$LOG"
"$HERE/run_jlink.sh" id_target.jlink "$LOG"
latest="$(ls -t "$LOG"/id_target_*.log | head -1)"
"$HERE/decode_target_id.py" "$latest" --expect "$EXPECT" | tee "$LOG/g1_target_id.txt"
