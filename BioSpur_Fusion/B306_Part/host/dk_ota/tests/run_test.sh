#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../../.." && pwd)"
out="$root/builds/host-tests/ota_image_state_verify_test"
mkdir -p "$(dirname "$out")"
cc -std=c11 -Wall -Wextra -Werror \
  "$root/host/dk_ota/tests/test_ota_image_state_verify.c" -o "$out"
"$out"
python3 "$root/host/dk_ota/tests/test_no_direct_confirm.py"
python3 "$root/host/dk_ota/tests/test_graduated_read_retry.py"
