import hashlib
from pathlib import Path

from biospur_fusion.root_r3.finalize import _filtered_status, _tree_digest


def test_tree_digest_is_content_and_symlink_target_sensitive(tmp_path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "link").symlink_to("a.txt")
    first = _tree_digest(tmp_path)
    assert first["regular_files"] == 1
    assert first["symlinks"] == 1
    (tmp_path / "a.txt").write_text("bravo", encoding="utf-8")
    second = _tree_digest(tmp_path)
    assert first["sha256_tree_v1"] != second["sha256_tree_v1"]


def test_status_filter_removes_only_declared_root_r3_and_cache_paths():
    raw = (
        b" M BioSpur_Fusion/AGENTS.md\n"
        b"?? BioSpur_Fusion/Fusion_Part/src/biospur_fusion/root_r3/models.py\n"
        b"?? BioSpur_Fusion/.pytest_cache/x\n"
    )
    assert _filtered_status(raw) == b" M BioSpur_Fusion/AGENTS.md\n"
