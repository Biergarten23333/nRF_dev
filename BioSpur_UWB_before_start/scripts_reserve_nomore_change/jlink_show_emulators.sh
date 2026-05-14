#!/usr/bin/env bash
set -euo pipefail
# Non-interactive probe listing that does NOT trigger SEGGER probe-selection GUI.
# Works even when nrfjprog backend is unhappy.
cmdfile="/tmp/jlink_show_emulators_$$.cmd"
trap 'rm -f "$cmdfile"' EXIT
cat >"$cmdfile" <<'EOF'
ShowEmuList USB
Exit
EOF
JLinkExe -NoGui 1 -CommanderScript "$cmdfile"
