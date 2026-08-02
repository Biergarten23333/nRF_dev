#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${TMPDIR:-/tmp}/biospur_led_fault_window_test"

cc -std=c11 -Wall -Wextra -Werror \
  "$root/tests/test_led_fault_window.c" \
  -o "$out"
"$out"

