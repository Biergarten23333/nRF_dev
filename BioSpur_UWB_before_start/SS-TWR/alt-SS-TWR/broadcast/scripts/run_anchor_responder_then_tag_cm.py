#!/usr/bin/env python3
"""Deprecated historical CM helper.

Current AutoPos uses capture scenes plus an independent runtime CIR switch:
`static|roto|wand|free -cir off|compact|full`.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "deprecated: use the Erlangen capture helpers with "
        "`-cir off|compact|full`; direct CM mode is retired.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
