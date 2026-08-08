#!/usr/bin/env bash
# G2 -- the no-reset proof. Connect and halt only.
#
# This script proves nothing on its own. The proof is three-way and two of the
# three legs are outside it: the master must show no disconnect, and the
# operator must see no re-connect blink. This leg captures the third -- the
# board's own boot counter and reset_reason, before and after -- and refuses if
# J-Link fell back to connect-under-reset.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: g2_noreset.sh <logdir>}"
mkdir -p "$LOG"
echo "=== BEFORE: record master link state + node uptime/reset_reason now ==="
echo "    (operator: note the PAIRED LED state too)"
"$HERE/run_jlink.sh" attach_noreset.jlink "$LOG"
echo
echo "=== AFTER: re-read the same three and compare ==="
echo "  1. master: no FUSION_DISCONNECTED, delivery resumed with no gap"
echo "  2. node:   uptime kept climbing, boot counter and reset_reason unchanged"
echo "  3. LED:    no re-connect blink pattern"
echo "ANY of the three showing a reset => STOP. Do not attach to a wedged board."
