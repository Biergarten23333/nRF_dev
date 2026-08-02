#!/bin/sh
set -eu
cc -std=c11 -Wall -Wextra -Werror \
  "$(dirname "$0")/test_imu_batch_roundtrip.c" \
  -o /tmp/biospur_imu_batch_roundtrip_test
/tmp/biospur_imu_batch_roundtrip_test
