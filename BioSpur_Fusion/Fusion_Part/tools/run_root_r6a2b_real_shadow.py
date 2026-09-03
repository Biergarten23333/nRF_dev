#!/usr/bin/env python3
"""Select and execute the bounded Root-R6A2B real-data shadow."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

FUSION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FUSION / "src"))

from biospur_fusion.root_r6a2b.real_shadow import (  # noqa: E402
    finalize_existing_real_shadow,
    run_bounded_real_shadow,
    select_real_windows,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--finalize-existing", action="store_true")
    args = parser.parse_args()
    if sum((args.select_only, args.execute, args.finalize_existing)) != 1:
        parser.error("select exactly one of --select-only, --execute, or --finalize-existing")
    args.result_dir.mkdir(parents=True, exist_ok=True)
    if args.select_only:
        path = select_real_windows(FUSION, args.result_dir)
    elif args.finalize_existing:
        path = finalize_existing_real_shadow(FUSION, args.result_dir)
    else:
        path = run_bounded_real_shadow(FUSION, args.result_dir)
    print(path)


if __name__ == "__main__":
    main()
