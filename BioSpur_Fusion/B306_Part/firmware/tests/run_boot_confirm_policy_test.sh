#!/bin/sh
set -eu
cc -std=c11 -Wall -Wextra -Werror \
  "$(dirname "$0")/test_boot_confirm_policy.c" \
  -o /tmp/biospur_boot_confirm_policy_test
/tmp/biospur_boot_confirm_policy_test
