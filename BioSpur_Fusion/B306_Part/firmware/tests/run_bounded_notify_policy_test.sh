#!/usr/bin/env bash
set -euo pipefail
out="$(mktemp)"
trap 'rm -f "$out"' EXIT
cc -std=c99 -Wall -Wextra -Werror -I"$(dirname "$0")/../src" \
  "$(dirname "$0")/test_bounded_notify_policy.c" -o "$out"
"$out"
