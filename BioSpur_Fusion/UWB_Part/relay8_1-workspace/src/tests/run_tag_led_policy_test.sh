#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
binary="$(mktemp /tmp/tag_led_policy_test.XXXXXX)"
trap 'rm -f "$binary"' EXIT

cc -std=c11 -Wall -Wextra -Werror \
  "$script_dir/test_tag_led_policy.c" \
  -o "$binary"
"$binary"
echo "TAG_LED_POLICY_PASS"
