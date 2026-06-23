#!/usr/bin/env python3
"""Deprecated historical sweep-then-CM helper.

Current AutoPos runs sweep and Tag capture as separate steps. CIR is selected
per capture with `-cir off|compact|full`.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "deprecated: run `sweep`, then `static|roto|wand|free "
        "-cir off|compact|full`.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
