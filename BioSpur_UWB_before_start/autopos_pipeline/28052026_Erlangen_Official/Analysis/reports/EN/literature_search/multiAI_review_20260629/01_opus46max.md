# Multi-AI review — reviewer: OPUS 4.6 MAX (4 PDFs at once)
# captured 2026-06-29 — RAW, not yet arbitrated

## VERDICT BLOCK
| PDF | (A) Novelty | (B) Writing | (C) Formatting |
|---|---|---|---|
| A-long | Strong | Needs-pass | Minor |
| A-short | Strong | Needs-pass (~46 cm number) | Minor |
| B-long | Strong | Needs-pass (verify note, v4-io undefined) | Minor |
| B-short | Strong | Needs-pass (dense) | Clean |
| (D) Cross-doc | Real conflicts: headline number provenance; A<->B headline-framing; Piavanini citation | | |

## Key cross-doc (D) findings
- D.2.1 (top issue): A's headline ~68 mm 3D absolute has no clear provenance in B's Erlangen data.
  B reports v4-io median 72.7, uncorrected common-mode 109.5, corrected held-out common-mode 68.5.
  ~68 matches B's 68.5 (corrected common-mode) but A never mentions common-mode reparam / tag-delay correction.
- D.2.2: A says B's headline = identifiability analysis; B says its headline = falsification protocol. Mismatch.
- D.2.3: Piavanini 2022 cited as two different venues (A: Sensors 22(23) 9363; B: MetroInd4.0&IoT).
- D.2.4: A-short "~46 cm from pure UWB" — if literal 46 cm it's a 10x typo vs ~59 mm; should be ~4-6 cm. VERIFY.
- D.4: B-short drops B-long's "globally-softest Fisher modes = within-layer wiggle" caveat.

## Must-fix (ranked)
1. Reconcile A's ~68 mm 3D with B's Erlangen results (state solver variant + correction).
2. Align what A says B's headline is vs what B says its headline is.
3. A-short: verify "~46 cm" rendering; fix if not "~4-6 cm".
4. B-long: remove "[verify exact venue/pages against PDF before submission]" from Batstone ref.
5. B-long: define "v4-io" on first use.
6. Resolve Piavanini 2022 citation discrepancy (Sensors vs MetroInd4.0&IoT).
7. B-long/short §1: weaken "is not a reliable proxy" -> "is not necessarily" (one room, 24 positions).
8. A-long: reduce "honest" count from 6 to <=1.

## Other notable per-doc
- A-long: "the benchmark any later ... must be measured against" too aggressive; "This is top-tier" subjective.
- A-long unverified arXiv: AniTrack 2506.00216, Delama 2506.15518, Nguyen 2510.05992.
- B-long: MR-ULINS arXiv:2408.05719 cited by ID only, unverified.
- B-long: "frame-conditioned" undefined; check "most prior work satisfies at most one" vs Corbalan (1+2).
- Contribution split judged CLEAN; only friction = framing mismatch + number provenance.

## Single most important (Opus)
A's headline 3D ~68 mm has no transparent provenance in B's data; appears to rely on a correction step A doesn't disclose. A reviewer reading both will catch it.
