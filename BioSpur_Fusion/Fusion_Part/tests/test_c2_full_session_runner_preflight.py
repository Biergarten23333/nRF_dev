from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


RUNNER = Path(__file__).parents[1] / "tools/run_c2_full_session_root_ab_coordinator.py"


def _module():
    spec = importlib.util.spec_from_file_location("full_session_runner_preflight", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registration(module, root: Path, name: str = "attempt"):
    attempt = root / name
    attempt.mkdir()
    document = {
        "attempt_directory": {
            "path": name,
            "identity": list(module._directory_identity(attempt)),
            "initially_empty": True,
        }
    }
    return attempt, document


def test_claim_is_exclusive_and_second_invocation_cannot_truncate(tmp_path: Path) -> None:
    module = _module()
    module.WORKSPACE = tmp_path
    attempt, document = _registration(module, tmp_path)
    claim = module._claim_attempt(document, "a" * 64, attempt)
    before = claim.read_bytes()
    with pytest.raises(RuntimeError, match="preregistered empty|identity changed"):
        module._claim_attempt(document, "a" * 64, attempt)
    assert claim.read_bytes() == before
    assert sorted(row.name for row in attempt.iterdir()) == ["CLAIM.json"]


def test_claim_rejects_symlink_foreign_and_preexisting_output(tmp_path: Path) -> None:
    module = _module()
    module.WORKSPACE = tmp_path
    attempt, document = _registration(module, tmp_path, "registered")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    with pytest.raises(RuntimeError, match="foreign"):
        module._claim_attempt(document, "b" * 64, foreign)
    linked = tmp_path / "linked"
    linked.symlink_to(attempt, target_is_directory=True)
    linked_document = {"attempt_directory": {
        "path": "linked", "identity": list(module._directory_identity(attempt)),
        "initially_empty": True,
    }}
    with pytest.raises(RuntimeError, match="foreign|symlink"):
        module._claim_attempt(linked_document, "b" * 64, linked)
    (attempt / "run").mkdir()
    with pytest.raises(RuntimeError, match="identity changed|empty|artifact"):
        module._claim_attempt(document, "b" * 64, attempt)
    assert not (attempt / "CLAIM.json").exists()


def test_stale_sidecar_and_all_claim_failures_never_open_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    module.WORKSPACE = tmp_path
    manifest = tmp_path / "PRE_RUN.json"
    manifest.write_text(json.dumps({"schema": "test"}) + "\n")
    sidecar = tmp_path / "SHA256SUMS"
    sidecar.write_text(f"{'0' * 64}  PRE_RUN.json\n")
    opened: list[str] = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777):
        opened.append(os.fspath(path))
        return real_open(path, flags, mode)

    monkeypatch.setattr(module.os, "open", recording_open)
    with pytest.raises(RuntimeError, match="manifest SHA-256 mismatch"):
        module._load_prerun(manifest, sidecar)
    attempt, document = _registration(module, tmp_path)
    (attempt / "STDOUT.txt").write_bytes(b"stale")
    with pytest.raises(RuntimeError, match="identity changed|empty|artifact"):
        module._claim_attempt(document, hashlib.sha256(manifest.read_bytes()).hexdigest(), attempt)
    assert all("fusion_host_raw.cobs.bin" not in path for path in opened)
    assert not (attempt / "CLAIM.json").exists()
