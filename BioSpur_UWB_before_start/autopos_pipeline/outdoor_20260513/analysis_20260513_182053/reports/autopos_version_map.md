# AutoPos Version Map

This file separates the names used in the early paper draft, later summary, and
current 2026-05-13 analysis scripts. The important point is that "V1/V2/V3/V4"
can refer to different evaluation contexts, so reports should state both the
version name and the actual algorithmic components used.

## Current Analysis-Script Version Definitions

Based on:

- `run_full_evaluation_same_pipeline_20260513.py`
- `run_analysis.py`

| Version / label | Pair fusion input | Anchor layout solver | Explicit anchor delay estimation? | Robust loss / outlier handling? | Tag positioning model used in evaluation | Notes |
|---|---|---|---|---|---|---|
| MDS + NLS | V1/simple pair distances | Classical MDS initialization + NLS | No | Mostly no; NLS refinement | Sigma-weighted Huber tag solve, delay = 0 | Algorithm baseline, not an AutoPos version. |
| Ridolfi GD | V1/simple pair distances | MDS/trilateration + gradient/NLS style refinement | No | Mostly no | Sigma-weighted Huber tag solve, delay = 0 | Baseline inspired by geometric self-localization. |
| SDP + NLS | V1/simple pair distances | SDP relaxation + NLS | No | Mostly no | Sigma-weighted Huber tag solve, delay = 0 | Initialization baseline; can be worse if rank gap is poor. |
| AutoPos V1 | Simple average pair fusion (`fused["v1"]`) | Weighted/NLS-style geometry solve | No | No explicit delay; no robust delay solve | Sigma-weighted Huber tag solve, delay = 0 | In current code, V1 is still no explicit delay estimation. Differences vs V3/V4 may be small because evaluation tag solver is already stronger. |
| AutoPos V2 | IVW-like / improved pair fusion (`fused["v2"]`) | Iterative geometry solve | No | No explicit delay | Sigma-weighted Huber tag solve, delay = 0 | Improves fusion/weighting, not delay modeling. |
| V3-lite | MAD+MVUE robust pair fusion (`fused["v3"]`) | No-delay geometry solve using robustly fused distances | No | Robust fusion, but no anchor delay variables | Sigma-weighted Huber tag solve, delay = 0 | Good practical no-delay baseline after robust pair fusion. |
| V3-full | MAD+MVUE robust pair fusion (`fused["v3"]`) | Joint/alternating position + delay solve in the paper draft; current same-pipeline script calls `solve_v3_full` | Yes | Tukey / robust delay-aware optimization | Sigma-weighted Huber tag solve using estimated anchor delay | This is the paper-draft "delay-aware AutoPos" core contribution. |
| V4-interonly / V4-io | MAD+MVUE robust pair fusion (`fused["v3"]`) | Huber joint solve over anchor positions + bounded per-anchor delays | Yes | Huber loss; delay prior/bounds | Sigma-weighted Huber tag solve using estimated anchor delay | Current V4 is inter-anchor-only delay solve. It is not the original future "RotArm Z-injection V4" from the early roadmap. |
| V5 / FIM | Uses V4 solution | Analytic uncertainty/FIM computed from V4 variables | Uses V4 delays | Not a new positioning solver here | N/A | Diagnostic uncertainty layer, not a separate deployed solver in current scripts. |

## Early `main.pdf` Meaning

In the first paper draft, the key ablation was:

| Early label | Meaning | Delay estimation? | Reported effect |
|---|---|---:|---|
| V1-no115 | True no-delay baseline without floating reference tag | No | `132.0 mm` 3D std, `119.8 mm` Z std |
| V3-full-no115 | Delay-aware robust AutoPos without floating reference tag | Yes | `49.6 mm` 3D std, `41.3 mm` Z std |

Interpretation: in the early/poorer regime, explicit delay-aware robust AutoPos
rescued the system from a bad no-delay geometry. This is the large `2.7x`
improvement story.

## V3 Name Collision

The label `V3` is especially overloaded. It should not be used by itself in a
report.

| Name to use | What it actually means | Delay estimation? | Why it is different |
|---|---|---:|---|
| Early archived V3-lite | Historical April experiment chain under `AutoPos_archive/autopos_V3`; mostly improved fusion plus iterative layout | No | This was a practical stepping-stone version, not the later delay-aware V3. |
| V3-lite robust-fusion no-delay | MAD/MVUE-style robust pair fusion, then no-delay anchor layout | No | Better pair statistics than V1/V2, but still assumes measured inter-anchor distance equals geometric distance. |
| Early V3-full delay-aware | Early practical `solve_anchor_layout_v3_full.py` style solver: SDP/MDS seed, Tukey IRLS, alternating position and per-anchor additive bias/delay estimation | Yes | This is the real delay-estimation version behind the early `V1-no115` vs `V3-full-no115` improvement story. |
| Current same-pipeline V3-lite | The 2026-05-13 no-delay robust-fusion baseline evaluated with the modern sigma-weighted Huber tag solver | No | The tag-evaluation layer is stronger than in early reports, so results can look closer to V4. |
| Current same-pipeline V3-full | The 2026-05-13 delay-aware V3-full label inside the current analysis scripts | Yes | It shares the name `V3-full`, but the surrounding evaluator/data handling and sometimes implementation path differ from the early full solver. |
| Current V4-interonly / V4-io | Huber joint solve over anchor positions plus bounded per-anchor delays | Yes | This is the current delay-compensated production comparison point; it is not the same solver as early V3-full even though both estimate delays. |

Short rule: if the result is meant to show the value of antenna-delay
estimation, compare **Original no-delay V1 baseline** against
**Early V3-full delay-aware baseline**. If the result is meant to compare
today's field performance, compare **Current V3-lite robust-fusion no-delay**,
**Current same-pipeline V3-full**, and **Current V4-interonly delay-compensated**
under the same tag-positioning evaluator.

The distinction is not only `lite` vs `full`. There are at least three separate
axes:

1. `lite` vs `full`: no delay estimation vs delay estimation.
2. `early full` vs `current full`: both may estimate delays, but the solver
   implementation and evaluation wrapper are not guaranteed to be identical.
3. `layout solver` vs `tag evaluator`: later reports can make different layouts
   look similar because they share a stronger sigma-weighted Huber tag solver.

Therefore, a result table should include both the public label and the actual
implementation path, e.g. `Early V3-full via solve_anchor_layout_v3_full.py` or
`Current V4-io via 2026-05-13 run_analysis.py`, instead of only saying `V3`.

## Later Summary / Current 2026-05-13 Meaning

Later evaluations often compare solver labels under a stronger shared evaluation
pipeline:

- sigma-weighted tag positioning;
- Huber tag solve;
- improved robust pair fusion for V3/V4;
- better inter-anchor data in the 2026-05-13 session.

Therefore the later "V1-V4 are similar" statement should be read as:

> Within the improved evaluation pipeline and cleaner dataset, the representative
> repeatability floor is no longer dominated by the solver label alone.

It should **not** be read as:

> Delay estimation was never useful.

## Recommended Naming in Reports

Avoid saying only "V1" or "V4" without defining the pipeline. Use explicit names:

| Recommended name | Use when referring to |
|---|---|
| Original no-delay V1 baseline | The early paper-draft V1-no115 result (`132 mm`) |
| Early V3-full delay-aware baseline | The early paper-draft robust delay-estimation result (`~50 mm`) |
| V3-lite robust-fusion no-delay baseline | Current no-delay but robustly fused baseline |
| Current same-pipeline V3-full | Current delay-aware V3-full label under the same 2026-05-13 evaluator |
| V4-interonly delay-compensated layout | Current 2026-05-13 Huber delay-aware inter-anchor solve |
| Sigma-weighted traditional tag solver | The later tag-positioning evaluation layer that can make several layouts look similar |

## Recommended Progression Line

For a paper/report progression figure, do **not** use every version that happened
to exist. Use one clean algorithmic lineage, otherwise the figure will suggest
that V1->V4 has no meaningful improvement.

Recommended main line:

| Progression label | Use implementation / result family | Delay-aware? | Why keep it |
|---|---|---:|---|
| V1 | Original/early V1-no115 | No | True simple baseline: simple bidirectional fusion + no-delay geometry. |
| V2 | Original/early V2-no115 | No | Intermediate no-delay improvement: better weighting/fusion than V1. |
| V3-lite | Early V3-lite no115 | No | Robust fusion / iterative no-delay layout. This is the last no-delay baseline. |
| V3-full | Early V3-full-no115 | Yes | Canonical delay-aware jump; best for showing antenna-delay estimation benefit. |
| V4 | Current V4-io / interonly delay-compensated layout | Yes | Current production-style Huber bounded-delay solver. |
| V5 | V4 + FIM / uncertainty layer | Uses V4 | Optional diagnostic extension, not a core accuracy solver. |

Versions to **exclude** from the main progression:

| Excluded label | Reason |
|---|---|
| Current V3-lite | It is too close to the current evaluation wrapper and does not add a clear algorithmic story beyond early V3-lite. |
| Current same-pipeline V3-full | It has naming overlap with early V3-full but a different surrounding pipeline; including both makes the progression confusing. |
| Early V3-full-with115 | It adds floating reference information, so it is not directly comparable to the no115 baseline chain. |

For the multiple `V3-full` candidates, keep **Early V3-full-no115** as the
canonical paper progression version. It is the cleanest point where the only big
conceptual change after V3-lite is explicit per-anchor delay/bias estimation.

## Short Takeaway

The version conflict is mostly a naming/pipeline issue:

- The early report compared a true no-delay baseline against a delay-aware solver,
  so the difference was large.
- The later analysis compares variants inside a much stronger shared pipeline and
  on cleaner data, so V1/V3/V4 may appear similar.
- Current code still has no-delay versions (`AutoPos V1`, `V2`, `V3-lite`) and
  delay-estimating versions (`V3-full`, `V4-interonly`).
