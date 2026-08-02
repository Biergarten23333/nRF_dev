#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$(mktemp)"
trap 'rm -f "$out"' EXIT

cc -std=c11 -Wall -Wextra -Werror \
  "$root/tests/test_imu_pull_diag_math.c" -o "$out"
"$out"
