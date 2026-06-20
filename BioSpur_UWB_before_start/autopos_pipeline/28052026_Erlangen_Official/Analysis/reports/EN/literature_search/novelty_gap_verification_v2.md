# Novelty Gap Verification V2

This revision expands the full-text check to 41 machine-readable papers out of 129 registry rows. The strongest access gap remaining is De Preter 2019: it is close by title and abstract, but the repository/PDF routes did not yield the actual proceedings full text locally. The added full-text item is AniTrack (Luder et al. 2025), which independently observed that self-localized anchors outperformed surveyed anchors on tag-positioning accuracy.

## Corpus-Level Phrase Check

- `delay-layout`: 0 hits
- `delay--layout`: 0 hits
- `wrong metric`: 0 hits
- `wrong-metric`: 0 hits
- `wrong calibration`: 0 hits
- `metric-correct`: 0 hits
- `physically wrong`: 0 hits
- `ranking flip`: 0 hits
- `error cancellation`: 0 hits
- `scale-delay`: 0 hits
- `common-mode`: 0 hits

No machine-readable full text contains the core novelty formulation: delay-layout coupling, scale-delay coupling, a wrong metric calibration winning, or a ranking flip after reducing tag-anchor positive bias.

## Close-Paper Interpretation

Hamer 2018, Almansa 2020, Corbalan 2023, Van Herbruggen 2023, and Schwarzbach 2026 cover UWB anchor self-localization/autocalibration. They address deployment cost, anchor reconstruction, large-scale connectivity, or uncertainty propagation, but not the case where a metric-distorted layout wins because it cancels tag-side NLOS bias.

Ledergerber 2017/2018, Shalaby 2022/2023, Shah 2021/2022, Liu 2024, and Piavanini 2022 cover delay calibration, range-bias models, and UWB error models. They support the calibration context but treat bias/delay as errors to estimate or compensate, not as a near-degenerate direction with anchor layout scale.

Prorok 2013 and Wymeersch/Shen/Win-style localization theory support the CRB/FIM and biased-range vocabulary. They do not falsify the novelty because they do not test same-environment winner reversal between metric-correct and metric-distorted calibrations.

AniTrack 2025 is the strongest new close-paper finding. It reports the same high-level paradox: ground-truth anchor positions gave worse tag accuracy than self-localized anchor positions in a DWM3000 outdoor deployment. However, the paper does not analyze Sim3 scale, delay-layout coupling, NLOS cancellation, Fisher/profile likelihood structure, or an intervention that reduces tag-anchor positive bias. It therefore strengthens rather than weakens the novelty claim: it is independent evidence that the failure mode exists, while the Erlangen paper supplies the mechanism.

## Current Novelty Status

The novelty claim remains valid after the expanded full-text pass: no checked paper explains delay-layout-NLOS coupling or diagnoses the wrong-metric-wins failure mode. AniTrack reports an unexplained instance of the paradox, which should be treated as independent supporting evidence. The lower-tail ranking flip should be presented as evidence for contribution 1, not as a separate contribution.
