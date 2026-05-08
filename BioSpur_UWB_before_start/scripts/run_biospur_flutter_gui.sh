#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$ROOT/.tooling/bin:$ROOT/.tooling/flutter/bin:$PATH"

cd "$ROOT/flutter_ui"
exec flutter run -d linux
