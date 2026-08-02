#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${TMPDIR:-/tmp}/biospur_timer_epoch_test"

cc -std=c11 -Wall -Wextra -Werror \
  "$root/tests/test_timer_epoch.c" \
  -o "$out"
"$out"
