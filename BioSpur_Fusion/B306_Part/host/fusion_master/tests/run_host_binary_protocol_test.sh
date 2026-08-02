#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${TMPDIR:-/tmp}/biospur_host_binary_protocol_test"

cc -std=c11 -Wall -Wextra -Werror \
  "$root/tests/test_host_binary_protocol.c" \
  -o "$out"
"$out"
