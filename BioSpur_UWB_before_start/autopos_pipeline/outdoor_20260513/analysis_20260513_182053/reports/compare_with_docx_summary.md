# Comparison Against Previous AutoPos Summary

Important interpretation: all positioning values below are repeatability / stability
metrics, not absolute accuracy against OptiTrack or total-station ground truth.

## Why `main.pdf` and the later summary appear to disagree

The first paper draft (`main.pdf`, May 2) and the later summary docx use different
ablation questions:

| Source | Main question | Reported result | Meaning |
|---|---|---:|---|
| `main.pdf` | Does joint antenna-delay estimation rescue a poor/no-delay calibration under quality-aware / variable-anchor tag positioning? | V1-no115 `132 mm` 3D std vs V3-full-no115 `49.6 mm` 3D std; Z `119.8 -> 41.3 mm` | Large improvement. This is the "algorithm robustness rescues bad calibration / bad D-H-like conditions" story. |
| Later docx summary | Once the robust pipeline and σ-weighted tag solver are used, do V1/V2/V3/V4 initialization/fusion variants change the final representative ID02 repeatability? | All solvers around `40-41 mm`; V1->V4 improvement `<2%` | Small difference. This is the "remaining floor is no longer calibration solver limited" story. |

These two statements are not contradictory. They describe two regimes. In the
earlier/poorer condition, no-delay calibration can distort the layout and dynamic
anchor selection exposes the error, so V3-full is decisive. In the later/cleaner
or stronger evaluation pipeline, the final repeatability floor is already dominated
by tag-anchor ranging noise, antenna orientation, geometry, and spatial multipath;
therefore V1-V4 variants collapse to a similar `~40 mm` representative floor.

| Topic | Previous summary docx | 2026-05-13 outdoor dataset | Interpretation |
|---|---:|---:|---|
| Earlier paper-draft core ablation | `main.pdf`: V1-no115 `132 mm`, V3-full-no115 `49.6 mm`; Z `119.8 -> 41.3 mm` | New comparable static ID02: V3 `39.29 mm`, V4 `39.16 mm`; broad static median V3 `47.01 mm` | The original "V3 rescues bad/no-delay calibration" story is valid, but the new good-data session shows the residual floor after rescue. |
| Dataset scale | 500-set inter-anchor sweep; 27 static captures; 4 roto captures; 2 deployments | 1000-set inter-anchor sweep + 10 prewarm; 23/24 static captures; 17 roto captures; W01-W05 wand/free-move data | New dataset is broader, especially dynamic/tilt coverage. |
| Inter-anchor solver RMS | V3-lite / MDS+NLS inter RMS ~42.1 mm; V4-io ~48.0 mm | V1 41.27 mm; V3-lite 41.08 mm; V3-full/V4-io 29.29 mm | Sweep/layout consistency improved in the new data; V4 inter-anchor fit is much better. |
| Comparable static point | ID02 center-mid: V3-lite 41.3 mm; V4-io 40.8 mm | ID02: V3-lite 39.29 mm; V4-interonly 39.16 mm | On the closest same-style static test, new data is slightly better than the old 41 mm floor. |
| Static overall distribution | Mainly reported one representative ID02 value (~41 mm) | Static V3-lite across 23 captures: best 30.19, p25 41.67, median 47.01, p75 57.92, worst 77.33 mm | New dataset shows the broader spatial/orientation distribution; typical static repeatability is ~47 mm, best cases reach ~30 mm. |
| Static V3 vs V4 | V4 only improves ~0.6 mm on ID02 | Static median: V3 47.01 mm, V4 47.72 mm; V4 worst 108.61 mm | V4 delay compensation is not globally better for static positioning; V3 remains safer as the baseline. |
| Center-mid orientation group | Not fully separated by orientation in the old summary | ID13-ID16 V3-lite: 47.68, 43.41, 41.40, 44.19 mm | Center-mid remains around the old 41-47 mm repeatability range; best orientation is ~41.4 mm. |
| Dynamic roto error | 4 roto captures; 3D std 111.7 mm; 3D RMS 186.2 mm; delta-R 119.8 mm | 17 roto captures; V3-lite median circle-fit 3D std 54.64 mm; V4 median 56.50 mm | New roto repeatability is substantially better in median/std terms, but note metric is circle-fit std here, not old 3D RMS. |
| Roto tilt dependence | Not enough tilt coverage | Median 3D std by tilt: planar 98.97, small 102.07, mid 76.18, high 60.17, vertical 58.70 mm | New data reveals a strong tilt/geometry effect; higher tilt gives better 3D geometric constraint. |
| Roto V3 vs V4 | Not emphasized beyond V4-io solver table | Roto median: V3 54.64 mm, V4 56.50 mm; V4 mean slightly lower due to tail improvement | V4 helps some bad-tail dynamic cases, but is not a clean global win. |
| Wand / spatial quality map | Not available in old summary | W05 free-move: 5400 solved points; V3 usable <=60 mm: 85.37%; V4 usable <=60 mm: 83.63% | New experiment adds spatial quality/usable-area analysis, not just point repeatability. |
| Activity region with anchor-near exclusion | Not available | With residual <=70 mm and nearest-anchor exclusion: 700 mm gives V3 12.44 m², V4 12.14 m²; 1000 mm gives V3 10.66 m², V4 10.13 m² | This is a new practical field-use result: recommended usable area can be estimated session-by-session. |
| Per-anchor residual diagnosis | Not available in old summary | V3 residuals show F/G/H stronger negative bias; V4 tightens F/G but H remains weak; H has fewer observations and median quality 84 | New data supports spatial/NLOS-like residual diagnosis; H is not only a delay problem. |

Short conclusion:

The new dataset does not invalidate the previous 41 mm result. On the directly
comparable ID02 static test, the new result is slightly better (~39 mm). The
important difference is that the new experiment expands the evaluation from a
single representative point to spatial/orientation distributions, roto tilt
dependence, and W05-derived usable-area estimation. Therefore, the headline
should be: AutoPos still achieves ~40 mm repeatability in favorable static
conditions, while the broader field dataset shows typical static repeatability
around 47 mm and exposes spatially structured residuals that can be used to
estimate the usable activity region.

Additional robustness interpretation:

The most important cross-experiment observation is that the final tag-position
repeatability remains in the same `~40-50 mm` band across very different
inter-anchor quality regimes. In the first draft's poor/no-delay regime, V1 can
degrade to `132 mm`, but the robust delay-aware pipeline recovers to `~50 mm`.
In the 2026-05-13 session, D/H-related sweep quality is much healthier, but the
representative static result remains `~39-47 mm`. This supports the claim that
AutoPos robustness is working: localized bad inter-anchor links can be absorbed
or downweighted, and the remaining error floor is more likely dominated by
runtime tag-anchor ranging noise, antenna orientation, geometry, and spatial
multipath than by inter-anchor calibration alone.
