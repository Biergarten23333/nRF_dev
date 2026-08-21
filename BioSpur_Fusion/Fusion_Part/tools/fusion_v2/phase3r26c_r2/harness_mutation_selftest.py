from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from harness_lint import lint_root


def mutate_file(path: Path, old: str, new: str) -> None:
    source = path.read_text()
    if old not in source:
        raise RuntimeError(f"mutation anchor missing in {path}: {old}")
    path.write_text(source.replace(old, new))


def main() -> int:
    test_root = Path(sys.argv[1]).resolve()
    scratch_root = Path(sys.argv[2]).resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    cases = [
        ("direct_call_add_psi", "test_frozen_contract.py", "edge, typed_k, row[\"measurement_protocol_relative\"]\n    )", "edge, typed_k, row[\"measurement_protocol_relative\"], psi=0.1\n    )"),
        ("direct_call_add_full_state", "test_frozen_contract.py", "edge, typed_k, row[\"measurement_protocol_relative\"]\n    )", "edge, typed_k, row[\"measurement_protocol_relative\"], full_state=pipeline_state()\n    )"),
        ("h_in_k_projection", "gauge_projection_contract.json", "\"selected_decision\"]", "\"selected_decision\", \"h_common_rad\"]"),
        ("psi_in_k_projection", "gauge_projection_contract.json", "\"selected_decision\"]", "\"selected_decision\", \"psi_protocol_to_common_rad\"]"),
        ("oracle_import_production", "oracle_k.py", "import math", "import math\nimport biospur_fusion"),
        ("comparator_ignore_extra_key", "recursive_compare.py", "set(actual) != set(expected)", "set(actual) < set(expected)"),
        ("comparator_ignore_wrong_type", "recursive_compare.py", "type(actual) is not type(expected)", "False"),
        ("comparator_accept_nan", "recursive_compare.py", "math.isfinite", "math.isnan"),
        ("remove_modulo_pi", "recursive_compare.py", "mode == \"MODULO_PI\"", "mode == \"REMOVED_MODULO_PI\""),
        ("expected_red_harness_failure", "expected_red.json", "\"PRODUCTION_SEMANTIC_ASSERTION\"", "\"harness failure\""),
        ("compatibility_adapter", "test_frozen_contract.py", "def _parameter_names", "def old_new_adapter():\n    return None\n\n\ndef _parameter_names"),
        ("signature_dispatch", "test_frozen_contract.py", "def test_k_kernel_signature_is_exact():", "def test_k_kernel_signature_is_exact():\n    if inspect.signature(core.production_reduced_factor_residual):\n        pass"),
    ]
    results = []
    for case_id, relative, old, new in cases:
        target = scratch_root / case_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(test_root, target)
        mutate_file(target / relative, old, new)
        errors = lint_root(target)
        results.append({"mutation_id": case_id, "detected": bool(errors), "errors": errors})
    payload = {
        "status": "PASS" if all(row["detected"] for row in results) else "FAIL",
        "executed_count": len(results),
        "detected_count": sum(row["detected"] for row in results),
        "results": results,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
