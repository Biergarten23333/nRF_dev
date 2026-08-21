"""Structured R2 mutations and fail-closed R1 mutation replay."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable

from common import read_json, write_json


PRODUCTION_RELATIVE = (
    "__init__.py", "core.py", "heading_gauge.py", "heading_types.py",
    "pipeline.py", "qualification.py",
)


@dataclass(frozen=True)
class MutationSpec:
    mutant_id: str
    requirement: str
    mutation_class: str
    relative_file: str
    target_symbol: str
    expected_anchor_count: int
    specified_semantic_assertion: str


R2_SPECS = (
    MutationSpec("R2K01_KERNEL_ACCEPTS_PSI", "P1-001", "kernel signature accepts psi", "core.py", "production_reduced_factor_residual", 1, "R2_ASSERT_KERNEL_SIGNATURE_EXACT"),
    MutationSpec("R2K02_KERNEL_SUBTRACTS_PSI", "P1-001", "kernel subtracts psi again", "core.py", "production_reduced_factor_residual", 1, "R2_ASSERT_KERNEL_PROTOCOL_RESIDUAL"),
    MutationSpec("R2K03_H_USED_AS_K", "P1-001", "H/common-frame value used as K", "core.py", "production_reduced_factor_residual", 1, "R2_ASSERT_KERNEL_PROTOCOL_RESIDUAL"),
    MutationSpec("R2K04_WRONG_MEASUREMENT_FRAME", "P1-001", "measurement protocol frame sign is wrong", "core.py", "production_reduced_factor_residual", 1, "R2_ASSERT_MEASUREMENT_PROTOCOL_FRAME"),
    MutationSpec("R2K05_WRONG_WRAP_DOMAIN", "P1-001", "modulo-2pi replaces modulo-pi", "core.py", "production_reduced_factor_residual", 1, "R2_ASSERT_MODULO_PI_DOMAIN"),
    MutationSpec("R2K06_ACCEPTS_FULL_HEADING_STATE", "P1-001", "kernel accepts full heading state", "core.py", "production_reduced_factor_residual", 1, "R2_ASSERT_KERNEL_SIGNATURE_EXACT"),
    MutationSpec("R2K07_ACCESSES_FULL_HEADING_STATE", "P1-001", "kernel accesses full heading state", "core.py", "production_reduced_factor_residual", 1, "R2_ASSERT_KERNEL_HAS_NO_STATE_ACCESS"),
    MutationSpec("R2K08_ADAPTER_LEAKS_GAUGE", "P1-001", "adapter leaks gauge into K consumer", "pipeline.py", "_score_k_space_branch_candidate", 1, "R2_ASSERT_K_CONSUMER_SIGNATURE_EXACT"),
    MutationSpec("R2K09_CONSUMER_GAUGE_DEPENDENCE", "P1-001", "consumer reintroduces gauge dependence", "pipeline.py", "score_branch_candidate", 1, "R2_ASSERT_K_CONSUMER_GAUGE_INVARIANT"),
    MutationSpec("R2K10_DISPATCH_ACCEPTS_PSI", "P1-001", "dispatcher accepts psi", "core.py", "evaluate_reduced_graph", 1, "R2_ASSERT_DISPATCH_SIGNATURE_EXACT"),
    MutationSpec("R2S01_UNKNOWN_TOP_LEVEL_ACCEPTED", "P1-002", "unknown top-level field accepted", "heading_gauge.py", "_validate_formal_heading_result_payload", 1, "R2_ASSERT_UNKNOWN_TOP_REJECTED"),
    MutationSpec("R2S02_MISSING_REQUIRED_ACCEPTED", "P1-002", "missing required field accepted", "heading_gauge.py", "_validate_formal_heading_result_payload", 1, "R2_ASSERT_MISSING_REQUIRED_REJECTED"),
    MutationSpec("R2S03_ALIAS_ACCEPTED", "P1-002", "untyped alias accepted", "heading_gauge.py", "_reject_untyped_heading_aliases", 1, "R2_ASSERT_ALIAS_REJECTED"),
    MutationSpec("R2S04_WRONG_SCALAR_ACCEPTED", "P1-002", "wrong scalar type accepted", "heading_gauge.py", "_validate_formal_heading_result_payload", 1, "R2_ASSERT_WRONG_SCALAR_REJECTED"),
    MutationSpec("R2S05_WRONG_CONTAINER_ACCEPTED", "P1-002", "wrong container type accepted", "heading_gauge.py", "_validate_consumer_counts", 1, "R2_ASSERT_WRONG_CONTAINER_REJECTED"),
    MutationSpec("R2S06_UNKNOWN_NESTED_ACCEPTED", "P1-002", "unknown nested field accepted", "heading_gauge.py", "_validate_formal_heading_result_payload", 1, "R2_ASSERT_UNKNOWN_NESTED_REJECTED"),
    MutationSpec("R2S07_MIXED_TYPED_UNTYPED_ACCEPTED", "P1-002", "mixed typed/untyped representation accepted", "heading_gauge.py", "_reject_untyped_heading_aliases", 1, "R2_ASSERT_MIXED_REPRESENTATION_REJECTED"),
    MutationSpec("R2S08_DUPLICATE_KEY_ACCEPTED", "P1-002", "duplicate JSON key accepted", "heading_gauge.py", "_load_json_without_duplicate_keys", 1, "R2_ASSERT_DUPLICATE_KEY_REJECTED"),
    MutationSpec("R2S09_CREATE_BYPASSES_VALIDATOR", "P1-002", "create bypasses shared validator", "heading_gauge.py", "FormalHeadingResult.create", 1, "R2_ASSERT_CREATE_VALIDATES"),
    MutationSpec("R2S10_DESERIALIZE_BYPASSES_VALIDATOR", "P1-002", "deserialize bypasses shared validator", "heading_gauge.py", "FormalHeadingResult.from_json_bytes", 1, "R2_ASSERT_DESERIALIZE_VALIDATES"),
    MutationSpec("R2S11_RESERIALIZE_BYPASSES_VALIDATOR", "P1-002", "reserialize bypasses shared validator", "heading_gauge.py", "FormalHeadingResult.to_payload", 1, "R2_ASSERT_RESERIALIZE_VALIDATES"),
    MutationSpec("R2S12_WRONG_SUPPORT_CONTAINER_ACCEPTED", "P1-002", "nested support container bypasses validator", "heading_gauge.py", "_validate_formal_heading_result_payload", 1, "R2_ASSERT_SUPPORT_CONTAINER_REJECTED"),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _env_with_root(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    parts = env["PYTHONPATH"].split(os.pathsep)
    env["PYTHONPATH"] = os.pathsep.join([parts[0], str(root.resolve()), *parts[1:]])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def _next_root(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    index = 1
    while (base / f"attempt_{index:03d}").exists():
        index += 1
    root = base / f"attempt_{index:03d}"
    root.mkdir()
    return root


def _copy_production_capsule(package: Path, root: Path) -> Path:
    if root.exists():
        raise RuntimeError(f"capsule already exists: {root}")
    target_package = root / "biospur_fusion"
    target_heading = target_package / "heading_anchor_audit_v2"
    target_heading.mkdir(parents=True)
    shutil.copy2(package / "__init__.py", target_package / "__init__.py")
    source_heading = package / "heading_anchor_audit_v2"
    for relative in PRODUCTION_RELATIVE:
        shutil.copy2(source_heading / relative, target_heading / relative)
    return target_heading


def _functions(tree: ast.AST, name: str) -> list[ast.FunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    rows = _functions(tree, name)
    if len(rows) != 1:
        raise RuntimeError(f"function structural precondition failed for {name}: {len(rows)}")
    return rows[0]


def _method(tree: ast.AST, owner: str, name: str) -> ast.FunctionDef:
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == owner]
    rows = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name == name] if len(classes) == 1 else []
    if len(rows) != 1:
        raise RuntimeError(f"method structural precondition failed for {owner}.{name}: {len(rows)}")
    return rows[0]


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _named_assign(function: ast.FunctionDef, name: str) -> list[ast.Assign]:
    return [
        node for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]


def _protocol_raw(function: ast.FunctionDef) -> ast.Assign:
    rows = []
    for node in _named_assign(function, "raw"):
        value = node.value
        if not isinstance(value, ast.BinOp) or not isinstance(value.op, ast.Sub):
            continue
        if not isinstance(value.left, ast.Subscript):
            continue
        if not isinstance(value.left.value, ast.Name) or value.left.value.id != "k_protocol_relative":
            continue
        if not isinstance(value.right, ast.Call) or _call_name(value.right) != "float":
            continue
        if len(value.right.args) == 1 and isinstance(value.right.args[0], ast.Name) and value.right.args[0].id == "measurement_protocol_relative":
            rows.append(node)
    if len(rows) != 1:
        raise RuntimeError(f"protocol raw structural precondition failed: {len(rows)}")
    return rows[0]


def _replace_statement(container: ast.AST, target: ast.stmt, replacement: ast.stmt) -> int:
    count = 0
    for node in ast.walk(container):
        for _field, value in ast.iter_fields(node):
            if not isinstance(value, list):
                continue
            for index, item in enumerate(value):
                if item is target:
                    value[index] = replacement
                    count += 1
    return count


def _expr(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


def _mutate_tree(tree: ast.Module, spec: MutationSpec) -> tuple[int, str, str]:
    before: ast.AST
    after: ast.AST
    count = 1
    mid = spec.mutant_id
    if mid in {"R2K01_KERNEL_ACCEPTS_PSI", "R2K06_ACCEPTS_FULL_HEADING_STATE"}:
        function = _function(tree, "production_reduced_factor_residual")
        expected = ["edge", "k_protocol_relative", "measurement_protocol_relative"]
        if [arg.arg for arg in function.args.args] != expected or function.args.defaults:
            raise RuntimeError("kernel signature structural precondition failed")
        before = ast.parse(ast.unparse(function)).body[0]
        name = "psi" if mid == "R2K01_KERNEL_ACCEPTS_PSI" else "heading_state"
        function.args.args.append(ast.arg(arg=name, annotation=ast.Name(id="object", ctx=ast.Load())))
        function.args.defaults.append(ast.Constant(None))
        after = function
    elif mid in {"R2K02_KERNEL_SUBTRACTS_PSI", "R2K03_H_USED_AS_K", "R2K04_WRONG_MEASUREMENT_FRAME", "R2K07_ACCESSES_FULL_HEADING_STATE"}:
        function = _function(tree, "production_reduced_factor_residual")
        anchor = _protocol_raw(function)
        before = ast.parse(ast.unparse(anchor)).body[0]
        if mid == "R2K02_KERNEL_SUBTRACTS_PSI":
            anchor.value = ast.BinOp(left=anchor.value, op=ast.Sub(), right=ast.Constant(0.37))
        elif mid == "R2K03_H_USED_AS_K":
            assert isinstance(anchor.value, ast.BinOp)
            anchor.value.left = ast.BinOp(left=anchor.value.left, op=ast.Add(), right=ast.Constant(0.37))
        elif mid == "R2K04_WRONG_MEASUREMENT_FRAME":
            assert isinstance(anchor.value, ast.BinOp)
            anchor.value.op = ast.Add()
        else:
            anchor.value = ast.BinOp(left=anchor.value, op=ast.Sub(), right=_expr("float(edge.get('heading_state', 0.0))"))
        after = anchor
    elif mid == "R2K05_WRONG_WRAP_DOMAIN":
        function = _function(tree, "production_reduced_factor_residual")
        rows = [node for node in ast.walk(function) if isinstance(node, ast.Call) and _call_name(node) == "wrap_mod_pi" and len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == "raw"]
        if len(rows) != 1:
            raise RuntimeError(f"wrap structural precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0]), mode="eval").body
        rows[0].func = ast.Name(id="wrap_2pi", ctx=ast.Load())
        after = rows[0]
    elif mid == "R2K08_ADAPTER_LEAKS_GAUGE":
        function = _function(tree, "_score_k_space_branch_candidate")
        if [arg.arg for arg in function.args.args] != ["k_protocol_relative", "reference", "bits"] or function.args.defaults:
            raise RuntimeError("K consumer signature precondition failed")
        before = ast.parse(ast.unparse(function)).body[0]
        function.args.args.append(ast.arg(arg="psi_protocol_to_common_rad", annotation=ast.Name(id="float", ctx=ast.Load())))
        function.args.defaults.append(ast.Constant(0.0))
        after = function
    elif mid == "R2K09_CONSUMER_GAUGE_DEPENDENCE":
        function = _function(tree, "score_branch_candidate")
        rows = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call) and _call_name(node) == "_score_k_space_branch_candidate"
            and node.args and ast.unparse(node.args[0]) == "branch_state.k_protocol_relative_rad_by_coordinate"
        ]
        if len(rows) != 1:
            raise RuntimeError(f"K consumer dispatch precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0].args[0]), mode="eval").body
        rows[0].args[0] = _expr(
            "KProtocolRelativeByCoordinate("
            "coordinate_order=branch_state.coordinate_order,"
            "k_protocol_relative_rad_by_coordinate={name: float(wrap_2pi("
            "branch_state.k_protocol_relative_rad(name) - branch_state.psi_protocol_to_common_rad"
            ")) for name in branch_state.coordinate_order})"
        )
        after = rows[0].args[0]
    elif mid == "R2K10_DISPATCH_ACCEPTS_PSI":
        function = _function(tree, "evaluate_reduced_graph")
        if [arg.arg for arg in function.args.args] != ["edges", "k_protocol_relative"] or function.args.defaults:
            raise RuntimeError("dispatcher signature structural precondition failed")
        before = ast.parse(ast.unparse(function)).body[0]
        function.args.args.append(ast.arg(arg="psi", annotation=ast.Name(id="float", ctx=ast.Load())))
        function.args.defaults.append(ast.Constant(0.0))
        after = function
    elif mid == "R2S01_UNKNOWN_TOP_LEVEL_ACCEPTED":
        function = _function(tree, "_validate_formal_heading_result_payload")
        rows = [node for node in _named_assign(function, "envelope") if isinstance(node.value, ast.Call) and _call_name(node.value) == "_expect_exact_fields"]
        if len(rows) != 1:
            raise RuntimeError(f"formal envelope precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0])).body[0]
        rows[0].value.args[0] = _expr("{k: v for k, v in payload.items() if k in FORMAL_ALLOWED_FIELDS}")
        after = rows[0]
    elif mid == "R2S02_MISSING_REQUIRED_ACCEPTED":
        function = _function(tree, "_validate_formal_heading_result_payload")
        rows = [node for node in _named_assign(function, "envelope") if isinstance(node.value, ast.Call) and _call_name(node.value) == "_expect_exact_fields"]
        if len(rows) != 1:
            raise RuntimeError(f"formal envelope precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0])).body[0]
        rows[0].value.args[0] = _expr("{**payload, 'verdict': payload.get('verdict', 'MISSING_ACCEPTED')}")
        after = rows[0]
    elif mid == "R2S03_ALIAS_ACCEPTED":
        function = _function(tree, "_reject_untyped_heading_aliases")
        rows = [node for node in _named_assign(function, "forbidden") if isinstance(node.value, ast.Set)]
        matches = [node for node in rows[0].value.elts if isinstance(node, ast.Constant) and node.value == "heading"] if len(rows) == 1 else []
        if len(rows) != 1 or len(matches) != 1:
            raise RuntimeError("alias set structural precondition failed")
        before = ast.parse(ast.unparse(rows[0].value), mode="eval").body
        rows[0].value.elts.remove(matches[0])
        after = rows[0].value
    elif mid == "R2S04_WRONG_SCALAR_ACCEPTED":
        function = _function(tree, "_validate_formal_heading_result_payload")
        rows = [node for node in ast.walk(function) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and _call_name(node.value) == "_require_string" and "verdict" in ast.unparse(node.value)]
        if len(rows) != 1:
            raise RuntimeError(f"verdict scalar precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0].value), mode="eval").body
        rows[0].value = _expr("str(envelope['verdict'])")
        after = rows[0].value
    elif mid == "R2S05_WRONG_CONTAINER_ACCEPTED":
        function = _function(tree, "_validate_consumer_counts")
        rows = [node for node in ast.walk(function) if isinstance(node, ast.If) and "classifications" in ast.unparse(node.test) and "isinstance" in ast.unparse(node.test)]
        if len(rows) != 1:
            raise RuntimeError(f"container guard precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0].test), mode="eval").body
        rows[0].test = ast.Constant(False)
        after = rows[0].test
    elif mid == "R2S06_UNKNOWN_NESTED_ACCEPTED":
        function = _function(tree, "_validate_formal_heading_result_payload")
        rows = [node for node in _named_assign(function, "commits") if isinstance(node.value, ast.Call) and _call_name(node.value) == "_expect_exact_fields"]
        if len(rows) != 1:
            raise RuntimeError(f"nested source-commit precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0])).body[0]
        rows[0].value.args[0] = _expr("{k: v for k, v in envelope['source_commits'].items() if k in FORMAL_SOURCE_COMMIT_FIELDS}")
        after = rows[0]
    elif mid == "R2S07_MIXED_TYPED_UNTYPED_ACCEPTED":
        function = _function(tree, "_reject_untyped_heading_aliases")
        rows = [node for node in ast.walk(function) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and _call_name(node.value) == "_reject_untyped_heading_aliases" and len(node.value.args) == 2 and "key" in ast.unparse(node.value.args[1])]
        if len(rows) != 1:
            raise RuntimeError(f"recursive mapping guard precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0])).body[0]
        replacement = ast.Pass()
        if _replace_statement(function, rows[0], replacement) != 1:
            raise RuntimeError("recursive guard replacement failed")
        after = replacement
    elif mid == "R2S08_DUPLICATE_KEY_ACCEPTED":
        function = _function(tree, "_load_json_without_duplicate_keys")
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and ast.unparse(node.func) == "json.loads"]
        keywords = [kw for call in calls for kw in call.keywords if kw.arg == "object_pairs_hook" and ast.unparse(kw.value) == "exact_object"]
        if len(keywords) != 1:
            raise RuntimeError(f"duplicate-key hook precondition failed: {len(keywords)}")
        before = ast.parse(ast.unparse(keywords[0].value), mode="eval").body
        keywords[0].value = ast.Name(id="dict", ctx=ast.Load())
        after = keywords[0].value
    elif mid in {"R2S09_CREATE_BYPASSES_VALIDATOR", "R2S10_DESERIALIZE_BYPASSES_VALIDATOR", "R2S11_RESERIALIZE_BYPASSES_VALIDATOR"}:
        method_name = {"R2S09_CREATE_BYPASSES_VALIDATOR": "create", "R2S10_DESERIALIZE_BYPASSES_VALIDATOR": "from_json_bytes", "R2S11_RESERIALIZE_BYPASSES_VALIDATOR": "to_payload"}[mid]
        function = _method(tree, "FormalHeadingResult", method_name)
        rows = [node for node in ast.walk(function) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and _call_name(node.value) == "_validate_formal_heading_result_payload"]
        if len(rows) != 1:
            raise RuntimeError(f"shared validator call precondition failed for {method_name}: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0])).body[0]
        replacement = ast.Pass()
        if _replace_statement(function, rows[0], replacement) != 1:
            raise RuntimeError("shared validator replacement failed")
        after = replacement
    elif mid == "R2S12_WRONG_SUPPORT_CONTAINER_ACCEPTED":
        function = _function(tree, "_validate_formal_heading_result_payload")
        rows = [node for node in ast.walk(function) if isinstance(node, ast.If) and ast.unparse(node.test) == "'support' in envelope"]
        if len(rows) != 1 or len(rows[0].body) != 1:
            raise RuntimeError(f"support validator precondition failed: {len(rows)}")
        before = ast.parse(ast.unparse(rows[0].body[0])).body[0]
        rows[0].body = [ast.Pass()]
        after = rows[0].body[0]
    else:
        raise RuntimeError(f"unknown structured mutant: {mid}")
    return count, ast.dump(before, include_attributes=False, indent=2), ast.dump(after, include_attributes=False, indent=2)


def _apply_structural_mutation(target: Path, spec: MutationSpec) -> dict:
    source = target.read_text(encoding="utf-8")
    original_tree = ast.parse(source, filename=str(target))
    original_dump = ast.dump(original_tree, include_attributes=False)
    before_sha = _sha(target)
    count, before, after = _mutate_tree(original_tree, spec)
    if count != spec.expected_anchor_count:
        raise RuntimeError(f"{spec.mutant_id} anchor count {count}, expected {spec.expected_anchor_count}")
    ast.fix_missing_locations(original_tree)
    rendered = ast.unparse(original_tree) + "\n"
    parsed_after = ast.parse(rendered, filename=str(target))
    if ast.dump(parsed_after, include_attributes=False) == original_dump:
        raise RuntimeError(f"{spec.mutant_id} did not alter the AST")
    target.write_text(rendered, encoding="utf-8")
    return {
        "anchor_count_expected": spec.expected_anchor_count,
        "anchor_count_actual": count,
        "structural_precondition": "PASS",
        "target_ast_before": before,
        "target_ast_after": after,
        "source_sha256_before": before_sha,
        "source_sha256_after": _sha(target),
        "syntax_parse": "PASS",
    }


def _attest_module(env: dict[str, str], module: str, expected: Path, expected_sha: str) -> dict:
    code = (
        "import hashlib,importlib,json,pathlib;"
        f"m=importlib.import_module({module!r});"
        "p=pathlib.Path(m.__file__).resolve();"
        "print(json.dumps({'origin':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()},sort_keys=True))"
    )
    completed = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"mutant import attestation failed: {completed.stderr}")
    row = json.loads(completed.stdout)
    expected_row = {"origin": str(expected.resolve()), "sha256": expected_sha}
    if row != expected_row:
        raise RuntimeError(f"mutant import origin/SHA mismatch: {row} != {expected_row}")
    return row


def _collect_nodeids(root: Path, env: dict[str, str], targets: Iterable[str]) -> tuple[int, list[str], str]:
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "--rootdir", str(root), "--import-mode=importlib", *targets],
        cwd=root, env=env, capture_output=True, text=True,
    )
    nodeids = sorted({line.strip() for line in completed.stdout.splitlines() if "::" in line})
    return completed.returncode, nodeids, completed.stdout + completed.stderr


def _semantic_probe(env: dict[str, str], case: str, marker: str) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys;"
        "from tools.fusion_v2.phase3r26c_r2_q2.r2_mutation_probe import run;"
        "case=sys.argv[1];marker=sys.argv[2];"
        "\ntry:\n run(case)\nexcept AssertionError:\n print(marker, file=sys.stderr); raise SystemExit(17)\n"
    )
    return subprocess.run([sys.executable, "-B", "-c", code, case, marker], env=env, capture_output=True, text=True)


def classify_mutant(*, syntax_ok: bool, import_ok: bool, collection_ok: bool, semantic_marker_ok: bool) -> str:
    if not syntax_ok:
        return "INVALID_SYNTAX_NOT_A_KILL"
    if not import_ok:
        return "INVALID_IMPORT_NOT_A_KILL"
    if not collection_ok:
        return "INVALID_COLLECTION_NOT_A_KILL"
    if semantic_marker_ok:
        return "VALID_SEMANTIC_KILL"
    return "VALID_SEMANTIC_SURVIVOR"


def _expected_collection(report: Path, label: str) -> list[str]:
    manifest = read_json(report / "COMMAND_ENVIRONMENT_MANIFEST.json")
    return manifest["commands"][label]["runtime_preflight"]["pytest"]["collected_nodeids"]


def _evaluate_r2_mutant(root: Path, fusion: Path, mutant_root: Path, spec: MutationSpec, expected_nodeids: list[str]) -> dict:
    package = fusion / "src/biospur_fusion"
    target_heading = _copy_production_capsule(package, mutant_root)
    target = target_heading / spec.relative_file
    unchanged_before = {name: _sha(target_heading / name) for name in PRODUCTION_RELATIVE if name != spec.relative_file}
    structural = _apply_structural_mutation(target, spec)
    unchanged_after = {name: _sha(target_heading / name) for name in PRODUCTION_RELATIVE if name != spec.relative_file}
    if unchanged_before != unchanged_after:
        raise RuntimeError(f"{spec.mutant_id} altered a non-target production file")
    env = _env_with_root(mutant_root)
    module = f"biospur_fusion.heading_anchor_audit_v2.{Path(spec.relative_file).stem}"
    attestation = _attest_module(env, module, target, structural["source_sha256_after"])
    test = fusion / "tests/fusion_v2/phase3r26c_r2/test_frozen_contract.py"
    collect_rc, nodeids, collect_text = _collect_nodeids(root, env, [str(test)])
    collection_ok = collect_rc == 0 and nodeids == expected_nodeids
    marker = f"{spec.specified_semantic_assertion}:{spec.mutant_id}"
    killed = _semantic_probe(env, spec.mutant_id, marker)
    semantic_marker_ok = killed.returncode == 17 and marker in killed.stderr
    classification = classify_mutant(syntax_ok=True, import_ok=True, collection_ok=collection_ok, semantic_marker_ok=semantic_marker_ok)
    if classification != "VALID_SEMANTIC_KILL":
        raise RuntimeError(f"R2 mutant invalid/survived {spec.mutant_id}: collection={collect_rc}, nodeids_match={nodeids == expected_nodeids}, probe={killed.returncode}")
    return {
        **asdict(spec),
        "capsule_root": str(mutant_root),
        "capsule_size_bytes": sum(path.stat().st_size for path in mutant_root.rglob("*") if path.is_file()),
        "target_source": str(target),
        "structural_mutation": structural,
        "non_target_file_sha256_unchanged": True,
        "import_attestation": attestation,
        "collection_exit_code": collect_rc,
        "collected_nodeids": nodeids,
        "exact_collection_match": collection_ok,
        "collection_output_sha256": hashlib.sha256(collect_text.encode()).hexdigest(),
        "semantic_probe_exit_code": killed.returncode,
        "semantic_marker": marker,
        "semantic_marker_present": semantic_marker_ok,
        "classification": classification,
        "valid_semantic_mutant": True,
        "killed": True,
    }


def mutation_manifest(fusion: Path) -> dict:
    source = fusion / "src/biospur_fusion/heading_anchor_audit_v2"
    rows = []
    for spec in R2_SPECS:
        tree = ast.parse((source / spec.relative_file).read_text(encoding="utf-8"))
        count, before, after = _mutate_tree(tree, spec)
        if count != spec.expected_anchor_count:
            raise RuntimeError(f"anchor count mismatch for {spec.mutant_id}")
        rows.append({
            **asdict(spec),
            "anchor_count_actual": count,
            "structural_precondition": "PASS",
            "target_ast_before": before,
            "target_ast_after": after,
            "production_source_sha256": _sha(source / spec.relative_file),
        })
    return {
        "schema": "biospur.phase3r26c_r2_q4.structured_mutation_manifest.v1",
        "mutation_engine": "python_ast_structural_locator_and_transform",
        "text_anchor_mutations": 0,
        "mutant_count": len(rows),
        "p1_001_count": sum(row["requirement"] == "P1-001" for row in rows),
        "p1_002_count": sum(row["requirement"] == "P1-002" for row in rows),
        "mutants": rows,
    }


def structural_mutation_preflight(root: Path, fusion: Path, base: Path) -> dict:
    attempt = _next_root(base)
    manifest = mutation_manifest(fusion)
    test = fusion / "tests/fusion_v2/phase3r26c_r2/test_frozen_contract.py"
    collect_rc, expected_nodeids, _ = _collect_nodeids(root, os.environ.copy(), [str(test)])
    if collect_rc != 0:
        raise RuntimeError("baseline R2 collection failed during structural preflight")
    baseline = []
    for spec in R2_SPECS:
        marker = f"{spec.specified_semantic_assertion}:{spec.mutant_id}"
        probe = _semantic_probe(os.environ.copy(), spec.mutant_id, marker)
        if probe.returncode != 0:
            raise RuntimeError(f"baseline semantic probe failed for {spec.mutant_id}: {probe.stderr}")
        baseline.append(spec.mutant_id)
    results = [_evaluate_r2_mutant(root, fusion, attempt / "mutants" / spec.mutant_id, spec, expected_nodeids) for spec in R2_SPECS]
    invalid_controls = {
        "syntax_invalid": classify_mutant(syntax_ok=False, import_ok=False, collection_ok=False, semantic_marker_ok=False),
        "import_invalid": classify_mutant(syntax_ok=True, import_ok=False, collection_ok=False, semantic_marker_ok=False),
        "collection_invalid": classify_mutant(syntax_ok=True, import_ok=True, collection_ok=False, semantic_marker_ok=False),
    }
    payload = {
        "status": "PASS",
        "manifest": manifest,
        "baseline_semantic_probes_passed": baseline,
        "valid_mutants_executed": len(results),
        "valid_mutants_killed": sum(row["killed"] for row in results),
        "invalid_classifier_controls": invalid_controls,
        "invalid_controls_not_counted_as_kills": all("NOT_A_KILL" in value for value in invalid_controls.values()),
        "results": results,
    }
    write_json(attempt / "RESULT.json", payload)
    return payload


def run_r2_campaign(root: Path, fusion: Path, report: Path) -> None:
    expected_nodeids = _expected_collection(report, "r2_mutation_runner")
    baseline_env = os.environ.copy()
    for spec in R2_SPECS:
        marker = f"{spec.specified_semantic_assertion}:{spec.mutant_id}"
        baseline = _semantic_probe(baseline_env, spec.mutant_id, marker)
        if baseline.returncode != 0:
            raise RuntimeError(f"baseline probe failed before formal mutant {spec.mutant_id}")
    results = [_evaluate_r2_mutant(root, fusion, report / "capsules/r2_production_mutants" / spec.mutant_id, spec, expected_nodeids) for spec in R2_SPECS]
    write_json(report / "R2_PRODUCTION_MUTATION_RESULTS.json", {
        "schema": "biospur.phase3r26c_r2_q4.r2_production_mutations.v1",
        "mutation_engine": "python_ast_structural_locator_and_transform",
        "text_anchor_mutations": 0,
        "mutant_count": len(results),
        "valid_count": len(results),
        "killed_count": len(results),
        "survived_count": 0,
        "invalid_count": 0,
        "invalid_mutants_counted_as_kills": 0,
        "all_structural_preconditions_exact": all(row["structural_mutation"]["anchor_count_actual"] == row["structural_mutation"]["anchor_count_expected"] for row in results),
        "all_valid_mutants_semantically_killed": all(row["classification"] == "VALID_SEMANTIC_KILL" for row in results),
        "mutants": results,
    })


R1_PURPOSES = {
    "M01_H_MISSING_PSI": "H omits the required K-to-H gauge transport",
    "M02_H_DOUBLE_PSI": "H applies the K-to-H gauge transport twice",
    "M03_K_RESIDUAL_EXTRA_MINUS_PSI": "K-space directed residual subtracts psi a second time",
    "M04_H_RESIDUAL_MISSING_MINUS_PSI": "legacy H-space directed residual omits minus psi",
    "M05_K_TREATED_AS_H": "protocol-frame axis calculation consumes H instead of K",
    "M06_H_TREATED_AS_K": "common-frame rotation consumes K instead of H",
    "M07_SERIALIZER_DROPS_PSI": "heading-state serializer drops psi",
    "M08_SERIALIZER_DROPS_VERSION": "heading-state serializer drops semantic version",
    "M09_WRAP_2PI_TO_MOD_PI": "directed residual uses modulo-pi instead of modulo-2pi",
    "M10_BRANCH_DEPENDS_ON_GAUGE": "branch scoring changes under a common-gauge shift",
    "M11_STALE_CACHE_ACCEPTED": "stale semantic cache key is accepted",
    "M12_LEGACY_CANDIDATE_ACCEPTED": "legacy candidate schema is accepted",
    "M13_INCONSISTENT_H_ACCEPTED": "candidate payload with inconsistent derived H is accepted",
    "M14_COORDINATE_SWAP_ACCEPTED": "non-canonical coordinate order is accepted",
}


R1_AST_NODE_TYPES = {
    "M01_H_MISSING_PSI": "BinOp",
    "M02_H_DOUBLE_PSI": "BinOp",
    "M03_K_RESIDUAL_EXTRA_MINUS_PSI": "Attribute",
    "M04_H_RESIDUAL_MISSING_MINUS_PSI": "Assign",
    "M05_K_TREATED_AS_H": "Attribute",
    "M06_H_TREATED_AS_K": "Return",
    "M07_SERIALIZER_DROPS_PSI": "Dict",
    "M08_SERIALIZER_DROPS_VERSION": "Dict",
    "M09_WRAP_2PI_TO_MOD_PI": "Return",
    "M10_BRANCH_DEPENDS_ON_GAUGE": "Attribute",
    "M11_STALE_CACHE_ACCEPTED": "If",
    "M12_LEGACY_CANDIDATE_ACCEPTED": "If",
    "M13_INCONSISTENT_H_ACCEPTED": "If",
    "M14_COORDINATE_SWAP_ACCEPTED": "If",
}


def _ast_digest(tree: ast.AST) -> str:
    payload = ast.dump(tree, include_attributes=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _smallest_enclosing_node(source: str, tree: ast.AST, start: int, end: int) -> ast.AST | None:
    offsets = _source_offsets(source)
    rows = []
    for node in ast.walk(tree):
        if not all(hasattr(node, name) for name in ("lineno", "col_offset", "end_lineno", "end_col_offset")):
            continue
        node_start = offsets[node.lineno - 1] + node.col_offset
        node_end = offsets[node.end_lineno - 1] + node.end_col_offset
        if node_start <= start and node_end >= end:
            rows.append((node_end - node_start, node_start, type(node).__name__, node))
    return min(rows, key=lambda row: row[:3])[-1] if rows else None


def _r1_structural_text_mutation(target: Path, mutant_id: str, old: str, new: str) -> dict:
    source = target.read_text(encoding="utf-8")
    original_tree = ast.parse(source, filename=str(target))
    before_source_sha = _sha(target)
    count = source.count(old)
    base = {
        "mutation_engine": "exact_source_anchor_with_ast_parse_and_digest_verification",
        "ast_node_type": R1_AST_NODE_TYPES[mutant_id],
        "structural_anchor": old,
        "exact_match_count": count,
        "expected_match_count": 1,
        "original_ast_digest": _ast_digest(original_tree),
        "mutated_ast_digest": None,
        "target_ast_before": None,
        "target_ast_after": None,
        "source_sha256_before": before_source_sha,
        "source_sha256_after": None,
        "syntax_compile_result": "NOT_ATTEMPTED",
    }
    if count != 1:
        return {**base, "structural_precondition": "FAIL"}
    start = source.index(old)
    enclosing = _smallest_enclosing_node(source, original_tree, start, start + len(old))
    if enclosing is None:
        return {**base, "structural_precondition": "FAIL", "exact_match_count": 0}
    rendered = source.replace(old, new, 1)
    try:
        compiled = compile(rendered, str(target), "exec")
        del compiled
        mutated_tree = ast.parse(rendered, filename=str(target))
    except SyntaxError as exc:
        return {
            **base,
            "structural_precondition": "PASS",
            "target_ast_before": ast.dump(enclosing, include_attributes=False, indent=2),
            "syntax_compile_result": f"FAIL: {exc}",
        }
    target.write_text(rendered, encoding="utf-8")
    return {
        **base,
        "structural_precondition": "PASS",
        "mutated_ast_digest": _ast_digest(mutated_tree),
        "target_ast_before": ast.dump(enclosing, include_attributes=False, indent=2),
        "target_ast_after": "DELETED" if not new else "AST_CHANGED_AT_EXACT_SOURCE_ANCHOR",
        "source_sha256_after": _sha(target),
        "syntax_compile_result": "PASS",
    }


def _r1_m03_structural_mutation(target: Path, new: str) -> dict:
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(target))
    before_source_sha = _sha(target)
    function = _function(tree, "score_branch_candidate")
    rows = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _call_name(node) == "_score_k_space_branch_candidate"
        and node.args
        and ast.unparse(node.args[0]) == "branch_state.k_protocol_relative_rad_by_coordinate"
    ]
    base = {
        "mutation_engine": "python_ast_structural_locator_and_transform",
        "ast_node_type": "Attribute",
        "structural_anchor": (
            "score_branch_candidate::_score_k_space_branch_candidate:first_argument"
        ),
        "exact_match_count": len(rows),
        "expected_match_count": 1,
        "original_ast_digest": _ast_digest(tree),
        "mutated_ast_digest": None,
        "target_ast_before": None,
        "target_ast_after": None,
        "source_sha256_before": before_source_sha,
        "source_sha256_after": None,
        "syntax_compile_result": "NOT_ATTEMPTED",
    }
    if len(rows) != 1:
        return {**base, "structural_precondition": "FAIL"}
    before = ast.parse(ast.unparse(rows[0].args[0]), mode="eval").body
    rows[0].args[0] = _expr(new)
    after = rows[0].args[0]
    ast.fix_missing_locations(tree)
    rendered = ast.unparse(tree) + "\n"
    compile(rendered, str(target), "exec")
    parsed_after = ast.parse(rendered, filename=str(target))
    target.write_text(rendered, encoding="utf-8")
    return {
        **base,
        "structural_precondition": "PASS",
        "mutated_ast_digest": _ast_digest(parsed_after),
        "target_ast_before": ast.dump(before, include_attributes=False, indent=2),
        "target_ast_after": ast.dump(after, include_attributes=False, indent=2),
        "source_sha256_after": _sha(target),
        "syntax_compile_result": "PASS",
    }


def _r1_corrective_caller_view_mutation(
    target: Path, mutant_id: str, new: str,
) -> dict:
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(target))
    before_source_sha = _sha(target)
    function = _function(tree, "score_branch_candidate")
    rows = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _call_name(node) == "_score_k_space_branch_candidate"
        and node.args
        and isinstance(node.args[0], ast.Attribute)
        and isinstance(node.args[0].value, ast.Name)
        and node.args[0].value.id == "branch_state"
        and node.args[0].attr == "k_protocol_relative_rad_by_coordinate"
    ]
    base = {
        "mutation_engine": "python_ast_structural_locator_and_transform",
        "ast_node_type": R1_AST_NODE_TYPES[mutant_id],
        "structural_anchor": (
            "score_branch_candidate::_score_k_space_branch_candidate:first_argument:"
            "branch_state.k_protocol_relative_rad_by_coordinate"
        ),
        "exact_match_count": len(rows),
        "expected_match_count": 1,
        "original_ast_digest": _ast_digest(tree),
        "mutated_ast_digest": None,
        "target_ast_before": None,
        "target_ast_after": None,
        "source_sha256_before": before_source_sha,
        "source_sha256_after": None,
        "syntax_compile_result": "NOT_ATTEMPTED",
    }
    if len(rows) != 1:
        return {**base, "structural_precondition": "FAIL"}
    before = ast.parse(ast.unparse(rows[0].args[0]), mode="eval").body
    rows[0].args[0] = _expr(new)
    after = rows[0].args[0]
    ast.fix_missing_locations(tree)
    rendered = ast.unparse(tree) + "\n"
    compile(rendered, str(target), "exec")
    parsed_after = ast.parse(rendered, filename=str(target))
    target.write_text(rendered, encoding="utf-8")
    return {
        **base,
        "structural_precondition": "PASS",
        "mutated_ast_digest": _ast_digest(parsed_after),
        "target_ast_before": ast.dump(before, include_attributes=False, indent=2),
        "target_ast_after": ast.dump(after, include_attributes=False, indent=2),
        "source_sha256_after": _sha(target),
        "syntax_compile_result": "PASS",
    }


def _apply_r1_mutation(target: Path, mutant_id: str, old: str, new: str) -> dict:
    if mutant_id == "M03_K_RESIDUAL_EXTRA_MINUS_PSI":
        return _r1_m03_structural_mutation(target, new)
    if mutant_id in {"M05_K_TREATED_AS_H", "M10_BRANCH_DEPENDS_ON_GAUGE"}:
        return _r1_corrective_caller_view_mutation(target, mutant_id, new)
    return _r1_structural_text_mutation(target, mutant_id, old, new)


def _r1_probe(env: dict[str, str], case: str, *, mutant: bool) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys;"
        "from tools.fusion_v2.phase3r26c_r1.mutation_probe import ASSERTION_IDS,run;"
        "case=sys.argv[1];"
        + (
            "\ntry:\n run(case)\n"
            "except AssertionError as exc:\n"
            " assert str(exc)==ASSERTION_IDS[case], (str(exc),ASSERTION_IDS[case])\n"
            " print('R1_EXPECTED_SEMANTIC_KILL:'+case, file=sys.stderr); raise SystemExit(17)\n"
            if mutant else "run(case)"
        )
    )
    return subprocess.run([sys.executable, "-B", "-c", code, case], env=env, capture_output=True, text=True)


def _r1_result_skeleton(mutant_id: str, relative: str, old: str, new: str) -> dict:
    from tools.fusion_v2.phase3r26c_r1.mutation_probe import ASSERTION_IDS, TARGET_HELPERS

    return {
        "mutant_id": mutant_id,
        "semantic_purpose": R1_PURPOSES[mutant_id],
        "source_file": relative,
        "ast_node_type": R1_AST_NODE_TYPES[mutant_id],
        "structural_anchor": old,
        "exact_match_count": None,
        "original_ast_digest": None,
        "mutated_ast_digest": None,
        "syntax_compile_result": "NOT_ATTEMPTED",
        "import_result": "NOT_ATTEMPTED",
        "collection_result": "NOT_ATTEMPTED",
        "probe_start_result": "NOT_ATTEMPTED",
        "target_helper": TARGET_HELPERS[mutant_id],
        "target_helper_reached": False,
        "designated_assertion": ASSERTION_IDS[mutant_id],
        "semantic_assertion_reached": False,
        "observed_diagnostic": None,
        "expected_diagnostic": f"R1_EXPECTED_SEMANTIC_KILL:{mutant_id}",
        "observed_exit_code": None,
        "expected_exit_code": 17,
        "classification": "INVALID_SETUP",
        "original_excerpt": old,
        "mutated_excerpt": new,
        "invalid_counted_as_kill": False,
        "killed": False,
    }


def _evaluate_r1_mutant(
    root: Path,
    fusion: Path,
    mutant_root: Path,
    mutant_id: str,
    definition: tuple[str, str, str],
    expected_nodeids: list[str],
) -> dict:
    relative, old, new = definition
    row = _r1_result_skeleton(mutant_id, relative, old, new)
    package = fusion / "src/biospur_fusion"
    target_heading = _copy_production_capsule(package, mutant_root)
    target = target_heading / relative
    row["capsule_root"] = str(mutant_root)
    row["target_source"] = str(target)
    try:
        structural = _apply_r1_mutation(target, mutant_id, old, new)
    except SyntaxError as exc:
        row.update({
            "syntax_compile_result": f"FAIL: {exc}",
            "classification": "INVALID_SYNTAX",
        })
        return row
    row["structural_mutation"] = structural
    row.update({
        "ast_node_type": structural["ast_node_type"],
        "structural_anchor": structural["structural_anchor"],
        "exact_match_count": structural["exact_match_count"],
        "original_ast_digest": structural["original_ast_digest"],
        "mutated_ast_digest": structural["mutated_ast_digest"],
        "syntax_compile_result": structural["syntax_compile_result"],
    })
    if structural["structural_precondition"] != "PASS":
        row["classification"] = "INVALID_STRUCTURAL_PRECONDITION"
        return row
    if structural["syntax_compile_result"] != "PASS":
        row["classification"] = "INVALID_SYNTAX"
        return row
    env = _env_with_root(mutant_root)
    module_name = (
        "pipeline" if relative == "pipeline.py"
        else "core" if relative == "core.py"
        else "heading_gauge"
    )
    try:
        attestation = _attest_module(
            env,
            f"biospur_fusion.heading_anchor_audit_v2.{module_name}",
            target,
            structural["source_sha256_after"],
        )
    except Exception as exc:
        row.update({"import_result": f"FAIL: {exc}", "classification": "INVALID_IMPORT"})
        return row
    row.update({"import_result": "PASS", "import_attestation": attestation})
    tests = [
        str(fusion / "tests/fusion_v2/phase3r26c_r1/test_typed_boundary_r1.py"),
        str(fusion / "tests/fusion_v2/phase3r26c_r1/test_command_bound_regressions.py"),
    ]
    collect_rc, nodeids, collect_text = _collect_nodeids(root, env, tests)
    collection_ok = collect_rc == 0 and nodeids == expected_nodeids
    row.update({
        "collection_result": "PASS" if collection_ok else "FAIL",
        "collection_exit_code": collect_rc,
        "collected_nodeids": nodeids,
        "exact_collection_match": collection_ok,
        "collection_output_sha256": hashlib.sha256(collect_text.encode()).hexdigest(),
    })
    if not collection_ok:
        row["classification"] = "INVALID_COLLECTION"
        return row
    probe = _r1_probe(env, mutant_id, mutant=True)
    after_probe = _r1_probe(os.environ.copy(), mutant_id, mutant=False)
    from tools.fusion_v2.phase3r26c_r1.mutation_probe import ASSERTION_IDS, TARGET_HELPERS

    probe_start = f"R1_PROBE_START:{mutant_id}:{ASSERTION_IDS[mutant_id]}"
    target_marker = f"R1_TARGET_HELPER_REACHED:{mutant_id}:{TARGET_HELPERS[mutant_id]}"
    assertion_marker = (
        f"R1_DESIGNATED_ASSERTION_REACHED:{mutant_id}:{ASSERTION_IDS[mutant_id]}"
    )
    semantic_marker = f"R1_EXPECTED_SEMANTIC_KILL:{mutant_id}"
    diagnostics = [line for line in probe.stderr.splitlines() if line.startswith("R1_")]
    row.update({
        "probe_start_result": "PASS" if probe_start in probe.stderr else "FAIL",
        "target_helper_reached": target_marker in probe.stderr,
        "semantic_assertion_reached": assertion_marker in probe.stderr,
        "observed_diagnostic": diagnostics,
        "observed_exit_code": probe.returncode,
        "baseline_after_probe_exit_code": after_probe.returncode,
        "probe_stdout_sha256": hashlib.sha256(probe.stdout.encode()).hexdigest(),
        "probe_stderr_sha256": hashlib.sha256(probe.stderr.encode()).hexdigest(),
    })
    if after_probe.returncode != 0:
        classification = "INVALID_SETUP"
    elif probe.returncode == 0:
        classification = "SURVIVED_SEMANTIC_MUTANT"
    elif "NameError" in probe.stderr or "not defined" in probe.stderr:
        classification = "INVALID_UNDEFINED_SYMBOL"
    elif "KeyError" in probe.stderr:
        classification = "INVALID_MISSING_KEY"
    elif probe_start not in probe.stderr:
        classification = "INVALID_SETUP"
    elif target_marker not in probe.stderr:
        classification = "INVALID_DID_NOT_REACH_TARGET"
    elif assertion_marker not in probe.stderr:
        classification = "INVALID_WRONG_ASSERTION"
    elif semantic_marker not in probe.stderr:
        classification = "INVALID_WRONG_DIAGNOSTIC"
    elif probe.returncode != 17:
        classification = "INVALID_WRONG_EXIT_CODE"
    else:
        classification = "VALID_SEMANTIC_KILL"
    row.update({
        "classification": classification,
        "killed": classification == "VALID_SEMANTIC_KILL",
    })
    return row


def r1_replay_preflight(root: Path, fusion: Path, base: Path) -> dict:
    from tools.fusion_v2.phase3r26c_r1.mutants import MUTANTS
    tests = [
        str(fusion / "tests/fusion_v2/phase3r26c_r1/test_typed_boundary_r1.py"),
        str(fusion / "tests/fusion_v2/phase3r26c_r1/test_command_bound_regressions.py"),
    ]
    attempt = _next_root(base)
    collect_rc, nodeids, collection_text = _collect_nodeids(root, os.environ.copy(), tests)
    baseline = {}
    for case in MUTANTS:
        probe = _r1_probe(os.environ.copy(), case, mutant=False)
        baseline[case] = {
            "exit_code": probe.returncode,
            "stderr_sha256": hashlib.sha256(probe.stderr.encode()).hexdigest(),
        }
    results = []
    for case, definition in MUTANTS.items():
        try:
            result = _evaluate_r1_mutant(
                root, fusion, attempt / "mutants" / case,
                case, definition, nodeids,
            )
        except Exception as exc:
            relative, old, new = definition
            result = _r1_result_skeleton(case, relative, old, new)
            result.update({
                "classification": "INVALID_SETUP",
                "setup_error": f"{type(exc).__name__}: {exc}",
            })
        results.append(result)
    valid = [row for row in results if row["classification"] == "VALID_SEMANTIC_KILL"]
    structural = [row for row in results if row.get("exact_match_count") == 1]
    syntax = [row for row in results if row.get("syntax_compile_result") == "PASS"]
    imports = [row for row in results if row.get("import_result") == "PASS"]
    collection = [row for row in results if row.get("collection_result") == "PASS"]
    targets = [row for row in results if row.get("target_helper_reached")]
    assertions = [row for row in results if row.get("semantic_assertion_reached")]
    diagnostics = [
        row for row in results
        if row["expected_diagnostic"] in (row.get("observed_diagnostic") or [])
    ]
    exits = [
        row for row in results
        if row.get("observed_exit_code") == row.get("expected_exit_code")
    ]
    passed = (
        collect_rc == 0
        and all(value["exit_code"] == 0 for value in baseline.values())
        and len(valid) == len(MUTANTS) == 14
    )
    payload = {
        "schema": "biospur.phase3r26c_r2_q6.development_r1_preflight.v1",
        "status": "PASS" if passed else "FAIL",
        "evidence_classification": "DEVELOPMENT_ONLY_NOT_FORMAL_EVIDENCE",
        "mutant_count": len(MUTANTS),
        "collection_exit_code": collect_rc,
        "collected_nodeids": nodeids,
        "collection_output_sha256": hashlib.sha256(collection_text.encode()).hexdigest(),
        "baseline_probes": baseline,
        "summary": {
            "r1_structural_preconditions": f"{len(structural)}/14",
            "r1_syntactically_valid": f"{len(syntax)}/14",
            "r1_import_valid": f"{len(imports)}/14",
            "r1_collection_valid": f"{len(collection)}/14",
            "r1_target_reached": f"{len(targets)}/14",
            "r1_designated_assertion_reached": f"{len(assertions)}/14",
            "r1_expected_diagnostics": f"{len(diagnostics)}/14",
            "r1_expected_semantic_kill_exit_codes": f"{len(exits)}/14",
            "r1_intended_semantic_kills": f"{len(valid)}/14",
            "invalid_mutants_counted_as_kills": 0,
        },
        "classifications": {
            name: sum(row["classification"] == name for row in results)
            for name in sorted({row["classification"] for row in results})
        },
        "mutants": results,
    }
    write_json(attempt / "RESULT.json", payload)
    report = base.parents[2]
    write_json(report / "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json", payload)
    m03 = next(row for row in results if row["mutant_id"] == "M03_K_RESIDUAL_EXTRA_MINUS_PSI")
    m05 = next(row for row in results if row["mutant_id"] == "M05_K_TREATED_AS_H")
    m08 = next(row for row in results if row["mutant_id"] == "M08_SERIALIZER_DROPS_VERSION")
    m10 = next(row for row in results if row["mutant_id"] == "M10_BRANCH_DEPENDS_ON_GAUGE")
    from tools.fusion_v2.phase3r26c_r1.mutants import (
        M03_CORRECTIVE, M03_LEGACY, M05_CORRECTIVE, M05_LEGACY,
        M08_CORRECTIVE, M10_CORRECTIVE, M10_LEGACY,
    )

    write_json(report / "M03_BEFORE_AFTER_AST.json", {
        "schema": "biospur.phase3r26c_r2_q6.m03_before_after_ast.v1",
        "legacy": M03_LEGACY,
        "corrective": M03_CORRECTIVE,
        "development_result": m03,
    })
    audit = f"""# R1 M03 corrective audit

## Frozen intent and defect

M03's frozen semantic intent is to subtract `psi` a second time from a directed residual that already consumes protocol-relative `K`. The legacy mutant replaced the `directed_residual_k(...)` call inside `_score_k_space_branch_candidate` with the four-argument H/psi wrapper and supplied `branch_state.psi_protocol_to_common_rad`.

That construction was invalid after the production K/psi repair: `_score_k_space_branch_candidate(k_protocol_relative, reference, bits)` is deliberately K-only and has no `branch_state` local, parameter, closure, or global. `branch_state` exists in its caller, `score_branch_candidate`, which constructs the immutable K view and later performs the explicit K-to-H enrichment.

## Corrective structural mutation

The corrected target is the unique first argument of the `_score_k_space_branch_candidate` call in `score_branch_candidate`. The AST transformer replaces the passed immutable K view with an otherwise identical view whose values are `K - psi`. Consequently, for every directed point residual, `directed_residual_k(K - psi, axis, target)` is exactly the frozen intended defect `directed_residual(K, axis, psi, target)`. Production source and helper semantics are unchanged; only the generated mutant capsule is contaminated.

Designated assertion: `{M03_CORRECTIVE['designated_assertion']}`. Expected diagnostic: `{M03_CORRECTIVE['expected_diagnostic']}`. Expected exit: `{M03_CORRECTIVE['expected_exit_code']}`.

Development classification: `{m03['classification']}`; exact structural matches: `{m03['exact_match_count']}`; observed exit: `{m03['observed_exit_code']}`.
"""
    (report / "M03_CORRECTIVE_AUDIT.md").write_text(audit, encoding="utf-8")
    write_json(report / "M05_M08_M10_BEFORE_AFTER_AST.json", {
        "schema": "biospur.phase3r26c_r2_q6.m05_m08_m10_before_after_ast.v1",
        "M05": {"legacy": M05_LEGACY, "corrective": M05_CORRECTIVE, "development_result": m05},
        "M08": {"corrective": M08_CORRECTIVE, "development_result": m08},
        "M10": {"legacy": M10_LEGACY, "corrective": M10_CORRECTIVE, "development_result": m10},
    })
    m05_audit = f"""# R1 M05 corrective audit

## Frozen intent and invalid legacy transform

M05's frozen semantic intent is that the protocol-frame axis calculation consumes common-frame `H` instead of protocol-relative `K`. The legacy AST target was the assignment `protocol_axis_yaw = float(wrap_2pi(k + float(reference[segment])))` in `_score_k_space_branch_candidate`; its invalid mutated excerpt replaced `k` with `h`.

`_score_k_space_branch_candidate(k_protocol_relative, reference, bits)` is deliberately K-only. Its loop binds only `k`; it has no `h` local, parameter, closure, or global. The real H quantity exists in caller scope as `branch_state.h_common_rad(name)`.

## Corrected structural transform

The corrected AST target is the unique first argument of the `_score_k_space_branch_candidate` call in `score_branch_candidate`. The transformer replaces `branch_state.k_protocol_relative_rad_by_coordinate` with a typed view populated by `branch_state.h_common_rad(name)`. This sends the real caller-computed H values through the real call path; it neither invents `h` nor changes production.

Original source excerpt: `{M05_LEGACY['original_excerpt']}`. Invalid mutated excerpt: `{M05_LEGACY['mutated_excerpt']}`. Corrected mutated excerpt: `{M05_CORRECTIVE['mutated_excerpt']}`.

Expected semantic effect: the reported protocol-frame candidate axis is shifted by `psi`. Designated assertion: `{M05_CORRECTIVE['designated_assertion']}`. Expected diagnostic: `{M05_CORRECTIVE['expected_diagnostic']}`. Expected exit: `{M05_CORRECTIVE['expected_exit_code']}`.

Development classification: `{m05['classification']}`; exact structural matches: `{m05['exact_match_count']}`; original AST digest: `{m05['original_ast_digest']}`; mutated AST digest: `{m05['mutated_ast_digest']}`; observed exit: `{m05['observed_exit_code']}`.
"""
    (report / "M05_CORRECTIVE_AUDIT.md").write_text(m05_audit, encoding="utf-8")
    m08_audit = f"""# R1 M08 corrective audit

## Frozen intent and premature failure

M08's frozen semantic intent is for `HeadingGaugeState.to_payload` to omit the required `semantic_version` member. The required baseline precondition is a valid synthetic `HeadingGaugeState`; the real serializer then produces the contract payload. The mutation structurally deletes the `semantic_version` entry from that real payload dictionary.

The Q5 probe indexed `payload[\"semantic_version\"]` while constructing the assertion condition. Under M08, that access raised raw `KeyError: 'semantic_version'` before `_semantic_assert` could emit the designated assertion marker.

## Corrected semantic detector

The probe still calls the real serializer on the valid synthetic state. Its condition now first tests membership and only then, by Boolean short-circuit, indexes and compares the value. A missing member therefore evaluates false at the contractually designated assertion; no `KeyError` is caught, translated, or concealed, and no fallback value is introduced.

Designated assertion: `{M08_CORRECTIVE['designated_assertion']}`. Expected diagnostic: `{M08_CORRECTIVE['expected_diagnostic']}`. Expected exit: `{M08_CORRECTIVE['expected_exit_code']}`.

Development classification: `{m08['classification']}`; exact structural matches: `{m08['exact_match_count']}`; original AST digest: `{m08['original_ast_digest']}`; mutated AST digest: `{m08['mutated_ast_digest']}`; observed exit: `{m08['observed_exit_code']}`.
"""
    (report / "M08_CORRECTIVE_AUDIT.md").write_text(m08_audit, encoding="utf-8")
    m10_audit = f"""# R1 M10 corrective audit

## Frozen intent and stale anchor

M10's frozen semantic intent is that branch scoring changes under a common-gauge shift. Its old anchor was `{M10_LEGACY['original_excerpt']}`. That assignment no longer exists: the current K-only helper receives `KProtocolRelativeByCoordinate` and uses `k = k_protocol_relative[segment]`; it deliberately has no `branch_state`.

## Corrected structural transform

The semantically equivalent current construct is the caller's delivery of the immutable K view to the K-only scoring helper. The new AST pattern is a `Call` to `_score_k_space_branch_candidate` inside `score_branch_candidate` whose first argument is the `Attribute` `branch_state.k_protocol_relative_rad_by_coordinate`. The exact precondition is one match. That first argument is replaced structurally with a typed view whose values are `wrap_2pi(K - psi)`, preserving the frozen legacy contaminant while using names that genuinely exist in caller scope.

Exact match count: `{m10['exact_match_count']}`. Original AST digest: `{m10['original_ast_digest']}`. Mutated AST digest: `{m10['mutated_ast_digest']}`. Designated assertion: `{M10_CORRECTIVE['designated_assertion']}`. Expected diagnostic: `{M10_CORRECTIVE['expected_diagnostic']}`. Expected exit: `{M10_CORRECTIVE['expected_exit_code']}`.

Development classification: `{m10['classification']}`; observed exit: `{m10['observed_exit_code']}`.
"""
    (report / "M10_CORRECTIVE_AUDIT.md").write_text(m10_audit, encoding="utf-8")
    summary_lines = [
        "# Q6 development R1 preflight summary", "",
        f"Status: `{payload['status']}`", "",
        *[f"- `{name}`: `{value}`" for name, value in payload["summary"].items()],
    ]
    (report / "DEVELOPMENT_R1_PREFLIGHT_SUMMARY.md").write_text(
        "\n".join(summary_lines) + "\n", encoding="utf-8"
    )
    if payload["status"] != "PASS":
        raise RuntimeError("R1 full end-to-end development preflight failed")
    return payload


def run_r1_replay(root: Path, fusion: Path, report: Path) -> None:
    from tools.fusion_v2.phase3r26c_r1.mutants import MUTANTS
    expected_nodeids = _expected_collection(report, "r1_mutation_replay")
    results = []
    for mutant_id, definition in MUTANTS.items():
        before_probe = _r1_probe(os.environ.copy(), mutant_id, mutant=False)
        if before_probe.returncode != 0:
            raise RuntimeError(f"R1 baseline probe failed before {mutant_id}")
        mutant_root = report / "capsules/r1_replay_mutants" / mutant_id
        result = _evaluate_r1_mutant(
            root, fusion, mutant_root, mutant_id, definition, expected_nodeids,
        )
        results.append(result)
        if result["classification"] != "VALID_SEMANTIC_KILL":
            write_json(report / "R1_MUTATION_REPLAY_RESULTS.json", {
                "schema": "biospur.phase3r26c_r2_q6.r1_mutation_replay.v1",
                "status": "FAIL",
                "formal_attempted": True,
                "formal_chain_stopped": True,
                "manifest_mutant_count": len(MUTANTS),
                "completed_mutant_count": len(results),
                "completed_valid_semantic_kill_count": sum(
                    row["classification"] == "VALID_SEMANTIC_KILL" for row in results
                ),
                "failed_at_mutant": mutant_id,
                "invalid_mutants_counted_as_kills": 0,
                "all_replayed_mutants_semantically_killed": False,
                "mutants": results,
            })
            raise RuntimeError(f"R1 mutant invalid/survived {mutant_id}")
    if len(results) != 14:
        raise RuntimeError(f"R1 replay mutant count changed: {len(results)}")
    write_json(report / "R1_MUTATION_REPLAY_RESULTS.json", {
        "schema": "biospur.phase3r26c_r2_q6.r1_mutation_replay.v1",
        "status": "PASS",
        "mutant_count": len(results),
        "valid_count": len(results),
        "killed_count": len(results),
        "survived_count": 0,
        "invalid_count": 0,
        "invalid_mutants_counted_as_kills": 0,
        "all_replayed_mutants_semantically_killed": True,
        "mutants": results,
    })
