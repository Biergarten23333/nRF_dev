# BioSpur AutoPos Layout Evaluation

Draft workspace for turning AutoPos sweep outputs into a layout evaluation and
ranking pipeline.

Current priority:

1. collect raw captures and analysis artifacts
2. clean layouts into a unified schema
3. extract geometry and DOP features
4. validate feature/error correlations with OptiTrack data
5. rank candidate layouts with an interpretable scoring engine

ML should come later as a layout error predictor, not as a replacement for the
geometry solver.

## Directory Tree

```text
DATASETS/
  raw_captures/          # Drop zone for unsorted original captures and old analysis outputs.
  raw_layouts/           # Normalized or source layout files ready for ingestion.
  optitrack_validation/  # Limited ground-truth validation data.
  processed/             # Cleaned canonical datasets generated from raw inputs.
  features/              # Extracted layout feature tables.
outputs/
  reports/               # Human-readable validation and ranking reports.
  top_layouts/           # Selected top-N layout candidates.
scripts/                 # Data cleaning, feature extraction, validation, ranking.
docs/                    # Architecture notes and data schema docs.
```

## Raw Data Policy

Put messy historical test folders into `DATASETS/raw_captures/` first. Keep the
original directory names and files intact. Cleaning scripts should read from
there and write normalized outputs elsewhere.

## Inventory

Generate the raw capture inventory and manifest:

```bash
python3 scripts/inventory_raw_captures.py
```

Outputs:

- `DATASETS/processed/raw_inventory.csv`
- `DATASETS/processed/raw_inventory.json`
- `DATASETS/processed/capture_manifest.json`
- `outputs/reports/raw_inventory.md`

Run the current CPU-only evaluation pipeline:

```bash
python3 scripts/run_cpu_pipeline.py
```

This runs inventory, layout DB generation, feature extraction, OptiTrack
correlation validation, stratified OptiTrack analysis, DOP binding, baseline
ranking, Score v2, score sensitivity analysis, ML candidate table generation,
and the Bewertung plots/report. It explicitly disables CUDA for the process.

See:

- `docs/data_assumptions.md`
- `docs/gpu_policy.md`
