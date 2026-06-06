#!/usr/bin/env bash
set -euo pipefail

cd /home/zekaixiao/Documents/nRF_dev/BioSpur_RGB_LASER/OpenPose

# VS Code installed as a Snap exports runtime variables that can make OpenPose
# load /snap/core20 libraries instead of the system glibc/Qt libraries.
while IFS='=' read -r name _; do
  case "$name" in
    SNAP*|GTK_PATH|LOCPATH|LD_LIBRARY_PATH|VIRTUAL_ENV)
      unset "$name"
      ;;
  esac
done < <(env)

exec ./build/examples/openpose/openpose.bin \
  --camera 0 \
  --camera_resolution 640x480 \
  --model_pose BODY_25 \
  --net_resolution -1x96 \
  --process_real_time \
  --number_people_max 1 \
  "$@"
