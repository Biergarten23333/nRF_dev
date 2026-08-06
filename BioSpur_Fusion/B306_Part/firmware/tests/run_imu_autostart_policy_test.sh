#!/bin/sh
set -eu
cc -std=c99 -Wall -Wextra -Werror \
  -I../src -I../../include test_imu_autostart_policy.c \
  -o /tmp/biospur_test_imu_autostart_policy
/tmp/biospur_test_imu_autostart_policy
