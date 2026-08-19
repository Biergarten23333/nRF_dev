#!/usr/bin/env python3
import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ[_name] = "1"

from biospur_fusion.heading_anchor_audit_v1.pipeline import main

if __name__ == "__main__":
    main()
