#!/usr/bin/env bash

# Resolve every writable fusion-link build into UWB_Part/builds/. This file is
# sourced by build wrappers; it deliberately contains no top-level mutation.

BIOSPUR_UWB_BUILDS_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd
)/builds"

biospur_uwb_build_dir()
{
  local requested="${1:-}"
  local name
  local candidate

  if [ -z "$requested" ]; then
    echo "build name must not be empty" >&2
    return 2
  fi

  case "$requested" in
    /*)
      candidate="$(realpath -m "$requested")"
      ;;
    */*)
      echo "relative build name must not contain '/': $requested" >&2
      return 2
      ;;
    *)
      name="${requested#build-}"
      if [ -z "$name" ]; then
        echo "build name must not be only 'build-'" >&2
        return 2
      fi
      candidate="$BIOSPUR_UWB_BUILDS_ROOT/$name"
      ;;
  esac

  case "$candidate" in
    "$BIOSPUR_UWB_BUILDS_ROOT"/*) ;;
    *)
      echo "UWB build must stay under $BIOSPUR_UWB_BUILDS_ROOT: $candidate" >&2
      return 2
      ;;
  esac

  mkdir -p "$BIOSPUR_UWB_BUILDS_ROOT"
  printf '%s\n' "$candidate"
}
