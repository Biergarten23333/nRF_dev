#!/usr/bin/env python3
"""Deprecated historical CM extraction helper.

Current captures write TR rows and optional CIR output directly. There is no
separate CM extraction stage in the current AutoPos flow.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "deprecated: current captures use TR rows plus optional CIR output; "
        "CM extraction is retired.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
