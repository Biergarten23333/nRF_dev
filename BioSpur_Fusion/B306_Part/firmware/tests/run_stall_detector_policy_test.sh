#!/usr/bin/env bash
set -euo pipefail
test_dir="$(cd "$(dirname "$0")" && pwd)"
cc -std=c11 -Wall -Wextra -Werror \
  -I"$test_dir/../src" "$test_dir/test_stall_detector_policy.c" \
  -o /tmp/bsf_stall_detector_policy_test
/tmp/bsf_stall_detector_policy_test
