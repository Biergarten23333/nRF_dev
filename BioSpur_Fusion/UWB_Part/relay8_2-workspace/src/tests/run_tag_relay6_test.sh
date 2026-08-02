#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${TMPDIR:-/tmp}/biospur-relay6-test"

cc -std=c11 -Wall -Wextra -Werror \
  -I"$root/include" \
  "$root/tests/test_tag_relay6.c" \
  "$root/src/uwb_beacon.c" \
  -o "$out"
"$out"
rm -f "$out"
