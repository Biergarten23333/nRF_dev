#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$(mktemp)"
trap 'rm -f "$out"' EXIT

cc -std=c11 -Wall -Wextra -Werror \
  -I"$repo_root/include" -I"$repo_root/src" \
  "$repo_root/tests/test_tag_relay8_2.c" \
  "$repo_root/src/uwb_beacon.c" -o "$out"
"$out"
