# V3 vs V4 Distribution Figures

- fig01: boxplot
- fig02: ECDF
- fig03: paired delta
- fig04: paired scatter

## Added Clearer Distribution Plots

- `fig01_boxplot_static_roto_v3_v4_annotated.png`: annotated boxplot with legend explaining box, median, mean diamond, tail circles, and per-capture dots.
- `fig05_distribution_cloud_violin_jitter.png`: violin/distribution-cloud plot. The wide part means many captures land there; dots are individual captures.

Reading the delta plots: `V4 - V3 > 0` means V3 is better. Static is mostly positive, so V3 is better for static. Roto is closer to balanced, and the long negative bars mean V4 helps a few bad roto cases / tail cases.
