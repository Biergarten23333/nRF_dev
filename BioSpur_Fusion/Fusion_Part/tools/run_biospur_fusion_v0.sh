#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 1 ]]; then
  echo "usage: $0 <new-output-directory>" >&2
  exit 64
fi
PYTHONPATH="${root_dir}/src" exec "${root_dir}/.venv-v0/bin/python" \
  "${root_dir}/tools/run_biospur_fusion_v0.py" \
  --config "${root_dir}/config/biospur_fusion_v0/config.json" \
  --output "$1"
