#!/bin/sh
set -eu

test_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=${TMPDIR:-/tmp}/broadcast_tdma_math_test

cc -std=c11 -Wall -Wextra -Werror \
  "$test_dir/test_broadcast_tdma_math.c" -o "$out"
"$out"
rm -f "$out"
