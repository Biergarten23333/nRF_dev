# Root-R4 source placement decision

Root-R4 is an isolated, experimental host-side analysis package. The current
`AGENTS.md` requires Fusion algorithm work to run directly in the canonical
`Fusion_Part` and forbids per-attempt worktrees. The disk gate passed before
implementation (257 GB free on `/mnt/nrf_ssd`, 51 GB free on `/`, projected
growth below 5 GB), so the authorized additions are limited to:

```text
Fusion_Part/src/biospur_fusion/root_r4/
Fusion_Part/tests/root_r4/
Fusion_Part/tools/run_root_r4.py
Fusion_Part/tools/build_root_r4_review.py
Fusion_Part/tools/finalize_root_r4.py
Fusion_Part/SOURCE_PLACEMENT_DECISION_ROOT_R4.md
```

Generated evidence is repository-external under the timestamped Root-R4 `/tmp`
directory. Root-R3, frozen M1, the canonical UWB frontend, prior evidence,
firmware, hardware configuration, product runners, and defaults are inputs only.
No worktree, branch, commit, push, merge, promotion, or product-default change is
part of Root-R4.
