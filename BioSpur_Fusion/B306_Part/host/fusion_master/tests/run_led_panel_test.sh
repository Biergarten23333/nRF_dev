#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${TMPDIR:-/tmp}/biospur_led_panel_test"

cc -std=c11 -Wall -Wextra -Werror \
  "$root/src/led_panel.c" \
  "$root/tests/test_led_panel.c" \
  -o "$out"
"$out"
