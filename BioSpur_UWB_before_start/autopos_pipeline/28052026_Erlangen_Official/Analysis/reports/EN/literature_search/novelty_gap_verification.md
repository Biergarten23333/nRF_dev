# Novelty Gap Verification

Registry size: 120 papers. Search source: OpenAlex work metadata and abstracts, plus DOI/OpenAlex verification URLs. The dangerous-query terms were included in the registry through searches on scale error, delay geometry coupling, wrong calibration, systematic bias cancellation, and related calibration-identifiability terms.

## Question 1: Does any Cluster A or B paper analyze delay-layout coupling or show that wrong calibration wins?

No registry paper in Cluster A or B clearly reports the specific phenomenon that a metrically wrong UWB anchor calibration can systematically outperform a metric-correct one because scale distortion cancels structured NLOS bias. Several papers estimate antenna delay, ranging offset, or anchor geometry, and some perform joint calibration, but the registry did not expose a paper that tests the wrong-geometry-wins paradox or common-mode delay absorption as a causal mechanism.

## Question 2: Does any paper use Fisher information or profile likelihood for UWB self-cal identifiability?

Fisher information, CRLB, observability, and gauge-freedom tools appear in the broader localization, SLAM, and sensor-network literature, and some UWB placement/accuracy papers use GDOP or CRLB-style arguments. The registry did not identify a UWB anchor self-calibration paper that combines Fisher/profile-likelihood analysis with the delay-layout weak direction found in the Erlangen campaign.

## Question 3: Does any paper use NLOS reduction as a causal intervention to diagnose calibration properties?

No. The NLOS literature uses CIR, ML, first-path features, robust filters, and error correction to improve ranging or localization. The registry did not reveal a paper that intentionally reduces the positive NLOS tail and then uses the resulting V4/V5-style geometry-ranking reversal as evidence about calibration physics.

## Question 4: Does any paper provide winner's curse correction or nested spatial CV for UWB accuracy?

Some evaluation and benchmark papers use repeated measurements, test trajectories, or benchmark splits. The registry did not identify a UWB calibration paper that explicitly applies winner's-curse correction or nested spatial cross-validation to distinguish in-campaign tuning from externally valid accuracy.

## Question 5: Is there a paper dangerously close to our work?

The closest papers are those on joint antenna-delay/anchor self-localization, visual-inertial SLAM aided UWB anchor-pose and sensor-error calibration, robust simultaneous UWB-anchor calibration, and node calibration with simultaneous ranging. They are close because they estimate anchor positions and delay/bias terms. They differ because they do not report that a scale-distorted geometry can win on positioning accuracy, do not use lower-tail NLOS reduction as a causal intervention, and do not separate metric anchor correctness from same-environment tag accuracy with the same falsification stack.

## Potentially dangerous hits requiring explicit comparison

- No registry entry was keyword-flagged as directly showing wrong-calibration-wins or explicit scale-delay/common-mode coupling.
