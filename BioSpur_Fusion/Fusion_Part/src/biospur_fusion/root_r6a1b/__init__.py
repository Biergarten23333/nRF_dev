"""Root-R6A1B calibration-authority and observability contracts."""

from .contracts import (
    AUTHORITY_CLASSES,
    AUTHORITY_COUNTS,
    CATEGORY_COUNTS,
    NODES_TO_SEGMENTS,
    build_contracts,
    resolve_sources,
    run_synthetic_qualification,
)

__all__ = [
    "AUTHORITY_CLASSES",
    "AUTHORITY_COUNTS",
    "CATEGORY_COUNTS",
    "NODES_TO_SEGMENTS",
    "build_contracts",
    "resolve_sources",
    "run_synthetic_qualification",
]
