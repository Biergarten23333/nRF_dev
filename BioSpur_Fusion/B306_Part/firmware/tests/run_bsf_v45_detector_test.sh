#!/usr/bin/env bash
set -euo pipefail
test_dir="$(cd "$(dirname "$0")" && pwd)"
cc -std=c11 -Wall -Wextra -Werror \
  -I"$test_dir/../src" "$test_dir/test_bsf_v45_detector.c" \
  -o /tmp/bsf_v45_detector_test
/tmp/bsf_v45_detector_test
