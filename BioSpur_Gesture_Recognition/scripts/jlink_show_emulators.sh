#!/usr/bin/env bash
set -euo pipefail

JLINK_EXE="${JLINK_EXE:-JLinkExe}"

"${JLINK_EXE}" <<'EOF'
ShowEmuList
q
EOF
