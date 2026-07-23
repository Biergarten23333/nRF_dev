#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
out="$root/builds/host-tests/strobe_timer_math_test"
mkdir -p "$(dirname "$out")"
cc -std=c11 -Wall -Wextra -Werror \
  "$root/firmware/tests/test_strobe_timer_math.c" -o "$out"
"$out"
