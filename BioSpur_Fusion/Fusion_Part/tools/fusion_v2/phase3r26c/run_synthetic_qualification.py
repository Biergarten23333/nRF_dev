#!/usr/bin/env python3
"""Retired R2.6C generator retained only as a historical command boundary.

The original implementation embedded claimed test counts, mutation results,
access events, and red/green output. R2.6C-R1 refuses to replay or rewrite that
bundle. The R1 report builder derives claims from captured command manifests.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "REFUSED: historical R2.6C generator contained hard-coded computed claims; "
        "use tools/fusion_v2/phase3r26c_r1/build_report.py",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
