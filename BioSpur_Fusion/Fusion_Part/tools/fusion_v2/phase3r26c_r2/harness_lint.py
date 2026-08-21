from __future__ import annotations

import ast
import json
from pathlib import Path
import sys


PROJECTION_CLASSES = (
    "K_INVARIANT_PROJECTION",
    "H_PSI_COVARIANT_PROJECTION",
    "STATIC_METADATA_PROJECTION",
    "REPRESENTATION_DERIVED_PROJECTION",
)
REQUIRED_PROJECTION_FIELDS = {
    "typed_k_map", "protocol_relative_measurement", "reduced_residual",
    "reachable_node_k", "scores", "preferences", "score_order", "selected_decision",
    "psi_protocol_to_common_rad", "h_common_rad", "R_GI", "schema",
    "coordinate_order", "factor_kind", "source_symbol", "measurement_semantic_class",
    "frame_labels", "units", "source_commit", "non_gauge_configuration",
    "canonical_state_bytes", "state_sha256", "cache_key", "serialized_payload_sha256",
}
FORBIDDEN_K_KEYS = {
    "psi", "psi_grid", "psi_protocol_to_common_rad", "h", "h_common_rad",
    "heading", "heading_gauge_state", "state", "state_sha", "cache_key", "r_gi",
}


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _contains_call(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Call) and _call_name(child) == name for child in ast.walk(node))


def _lint_test_source(path: Path, errors: list[str]) -> None:
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "production_reduced_factor_residual":
            if len(node.args) != 3 or any(isinstance(arg, ast.Starred) for arg in node.args):
                errors.append(f"{path}: direct K call must have exactly three explicit positional arguments")
            if node.keywords:
                errors.append(f"{path}: direct K call may not use keywords or **kwargs")
            rendered = ast.unparse(node).lower()
            if any(token in rendered for token in ("psi", "h_common", "heading_state", "full_state")):
                errors.append(f"{path}: direct K call carries gauge-bearing input")
        if isinstance(node, ast.Try) and any(
            isinstance(handler.type, ast.Name) and handler.type.id == "TypeError"
            for handler in node.handlers if handler.type is not None
        ):
            errors.append(f"{path}: TypeError compatibility dispatch forbidden")
        if isinstance(node, ast.If) and _contains_call(node.test, "signature"):
            errors.append(f"{path}: signature-dependent dispatch forbidden")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "adapter" in node.name.lower():
            errors.append(f"{path}: compatibility adapter forbidden")
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Constant) and node.test.value is True:
            errors.append(f"{path}: literal True may not serve as oracle")


def _walk_keys(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def lint_root(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.glob("test_*.py")):
        _lint_test_source(path, errors)

    k_fixture = json.loads((root / "k_kernel_fixture.json").read_text())
    bad_keys = sorted(set(_walk_keys(k_fixture)).intersection(FORBIDDEN_K_KEYS))
    if bad_keys:
        errors.append(f"K fixture contains forbidden gauge keys: {bad_keys}")

    for oracle_name in ("oracle_k.py", "oracle_h.py"):
        oracle_path = root / oracle_name
        tree = ast.parse(oracle_path.read_text(), filename=str(oracle_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.startswith("biospur_fusion") for name in names):
                errors.append(f"{oracle_path}: independent oracle imports production")

    projection = json.loads((root / "gauge_projection_contract.json").read_text())
    owners: dict[str, list[str]] = {}
    for class_name in PROJECTION_CLASSES:
        for field in projection.get(class_name, []):
            owners.setdefault(field, []).append(class_name)
    if set(owners) != REQUIRED_PROJECTION_FIELDS:
        errors.append("projection field classification is incomplete or has unknown fields")
    duplicates = {field: classes for field, classes in owners.items() if len(classes) != 1}
    if duplicates:
        errors.append(f"projection fields are not uniquely classified: {duplicates}")
    k_fields = set(projection.get("K_INVARIANT_PROJECTION", []))
    if any(field.lower().startswith(("psi", "h_common", "r_gi")) for field in k_fields):
        errors.append("H/psi field appears in K invariant projection")

    expected = json.loads((root / "expected_red.json").read_text())["failures"]
    nodeids = [row.get("nodeid") for row in expected]
    if len(nodeids) != len(set(nodeids)) or any(not nodeid for nodeid in nodeids):
        errors.append("expected RED nodeids are missing or duplicated")
    for row in expected:
        text = json.dumps(row).lower()
        if row.get("classification") != "PRODUCTION_SEMANTIC_ASSERTION":
            errors.append("expected RED includes a non-semantic failure")
        if not row.get("semantic_failure_id"):
            errors.append("expected RED semantic failure ID is missing")
        diagnostic = row.get("diagnostic", {})
        kind = diagnostic.get("kind")
        if kind == "assertion_marker" and not diagnostic.get("text"):
            errors.append("expected RED assertion marker is missing")
        elif kind == "pytest_did_not_raise" and diagnostic.get("exception") != "HeadingGaugeValidationError":
            errors.append("expected RED did-not-raise exception is not HeadingGaugeValidationError")
        elif kind not in {"assertion_marker", "pytest_did_not_raise"}:
            errors.append("expected RED diagnostic kind is unsupported")
        if any(token in text for token in ("collection error", "import error", "harness failure")):
            errors.append("expected RED includes collection/import/harness failure")

    comparator = (root / "recursive_compare.py").read_text()
    required_comparator_fragments = (
        "set(actual) != set(expected)",
        "type(actual) is not type(expected)",
        "math.isfinite",
        "mode == \"MODULO_PI\"",
    )
    for fragment in required_comparator_fragments:
        if fragment not in comparator:
            errors.append(f"comparator control missing: {fragment}")
    return errors


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    errors = lint_root(root)
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
