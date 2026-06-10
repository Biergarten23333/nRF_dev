# Individuelle Report Bundle 2026-06-10

This directory collects the standalone individual-report work so the dataset
root stays clean.

## Layout

- `report/`: Markdown source, generated LaTeX/PDF, phase reports, figures, and
  CSV tables for the individual report.
- `code/`: analysis and report-generation scripts used to produce the frozen
  phase records. These are archived here to keep them separate from production
  solver code. This includes the Phase 0/1 scripts (`audit.py`,
  `run_phase1.py`, `verify_anchor_mapping.py`, `asymmetry.py`,
  `pair_bias_vs_distance.py`, `tag_link_bias.py`) and the generated
  `data_config.py` anchor-ID mapping.

## Main Deliverables

- `report/03_individual_report_draft.md`: source-of-record draft.
- `report/03_individual_report_draft.tex`: standalone LaTeX export generated
  from the Markdown draft.
- `report/03_individual_report_draft.pdf`: compiled standalone PDF.

## Rebuild The PDF

From this directory:

```bash
python3 code/markdown_report_to_latex.py
latexmk -cd -pdf -interaction=nonstopmode -halt-on-error report/03_individual_report_draft.tex
latexmk -cd -c report/03_individual_report_draft.tex
```

## Re-run A Phase Script

The archived phase scripts still depend on helper modules from the Erlangen
dataset root. Run them from the dataset root with both the root and this `code/`
directory on `PYTHONPATH`, for example:

```bash
PYTHONPATH="$PWD/Analysis/individuelle_report_10062026/code:$PWD" \
python3 Analysis/individuelle_report_10062026/code/phase2_15_common_mode_bias.py \
  --data-dir . \
  --out-dir Analysis/individuelle_report_10062026/report
```

The phase reports remain diagnostic records only. No production solver files are
modified by this bundle.

## Re-run Phase 0/1

The original root-level `make_all.sh` was archived into `code/` to keep the
dataset root clean. To reproduce Phase 0/1 into this bundle:

```bash
PYTHONPATH="$PWD/Analysis/individuelle_report_10062026/code:$PWD" \
python3 Analysis/individuelle_report_10062026/code/audit.py \
  --data-dir . \
  --out-dir Analysis/individuelle_report_10062026/report

PYTHONPATH="$PWD/Analysis/individuelle_report_10062026/code:$PWD" \
python3 Analysis/individuelle_report_10062026/code/run_phase1.py \
  --data-dir . \
  --out-dir Analysis/individuelle_report_10062026/report
```

`verify_anchor_mapping.py` writes `data_config.py` to the dataset root when
Phase 1 is re-run, because the shared `scripts.phase1_common` helper expects it
there. The archived verified mapping is kept at `code/data_config.py`; copy it
back temporarily or regenerate it if a later archived phase script requires
`data_config.py` in the dataset root.
