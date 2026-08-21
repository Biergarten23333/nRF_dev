"""Pure fail-closed comparisons shared by the wrapper and negative controls."""

from __future__ import annotations

from typing import Mapping, Sequence


CONTRACT_FIELDS = (
    "absolute_python_executable",
    "python_version",
    "argv",
    "cwd",
    "REPO_ROOT",
    "FUSION_ROOT",
    "PYTHONPATH",
    "expected_sys_path_prefix",
    "sitecustomize_origin",
    "sitecustomize_sha256",
    "wrapper_path",
    "wrapper_sha256",
    "dispatcher_sha256",
    "command_specs_sha256",
    "tracing_mode",
    "report_early_stop_bytes",
    "report_hard_limit_bytes",
    "canonical_branch",
    "canonical_head",
    "canonical_tree",
    "worktree_registry_sha256",
    "non_fusion_status_sha256",
    "sealed_file_sha256",
    "source_capsule_sha256",
)

RUNTIME_FIELDS = (
    "absolute_python_executable",
    "python_version",
    "cwd",
    "PYTHONPATH",
    "sys_path",
    "sitecustomize_origin",
    "sitecustomize_sha256",
    "module_origins",
    "pytest",
)


class EnvironmentGateError(RuntimeError):
    """A frozen command contract differs from the live command."""


def differing_fields(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    fields: Sequence[str],
) -> list[str]:
    return [field for field in fields if expected.get(field) != actual.get(field)]


def assert_contract_matches(
    frozen: Mapping[str, object], actual: Mapping[str, object]
) -> None:
    different = differing_fields(frozen, actual, CONTRACT_FIELDS)
    if frozen.get("environment_sha256") != actual.get("environment_sha256"):
        different.append("environment_sha256")
    if different:
        raise EnvironmentGateError(
            "formal command environment differs from freeze: "
            + ", ".join(sorted(set(different)))
        )


def assert_runtime_matches(
    frozen: Mapping[str, object], actual: Mapping[str, object]
) -> None:
    different = differing_fields(frozen, actual, RUNTIME_FIELDS)
    if different:
        raise EnvironmentGateError(
            "live runtime preflight differs from freeze: "
            + ", ".join(sorted(set(different)))
        )


def assert_collection_matches(
    expected_nodeids: Sequence[str], actual_nodeids: Sequence[str]
) -> None:
    if list(expected_nodeids) != list(actual_nodeids):
        raise EnvironmentGateError("pytest collection drift detected")
