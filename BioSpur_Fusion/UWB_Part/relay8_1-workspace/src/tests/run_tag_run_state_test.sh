#!/bin/sh
set -eu

test_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
include_dir=$(CDPATH= cd -- "$test_dir/../include" && pwd)
out=${TMPDIR:-/tmp}/tag_run_state_test

cc -std=c11 -Wall -Wextra -Werror \
  -I"$include_dir" \
  "$test_dir/test_tag_run_state.c" -o "$out"
"$out"
rm -f "$out"
