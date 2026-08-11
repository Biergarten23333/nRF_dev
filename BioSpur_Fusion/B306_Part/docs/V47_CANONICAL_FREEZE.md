# Canonical B306 v47 freeze

Status: **FROZEN production Fusion foundation**, 2026-08-11.

| Identity | Canonical value |
|---|---|
| Version | `v47` |
| Marker | `b306-imu-relay-v47` |
| MCUboot | `0.1.47` |
| FWID | `f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed` |
| Active image SHA-256 | `90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98` |

These values match `releases/v47/finalized_manifest.json` and the immutable
identity-registry entry. The immutable set is `firmware/`, the v46 SDK patch
used by the finalized build, `releases/v47/`, and the v47 registry entry. This
freeze does not rewrite their history or authorize regeneration.

All ten boards passed exact-image rollout and confirmation. Nine production
boards then passed hard-power persistence acceptance; BSF6C53 retained its
separately accepted adapter-powered state. The continuous formal run delivered
6.382149 hours at full ten-node Fusion cadence: 57.439344 battery board-hours,
6.382149 adapter-hours and 63.821493 healthy board-hours total, without an
observed full-air wedge or B306 reset.

The scientific conclusion remains `V47_PREVENTION_CONSISTENT_NOT_PROVEN`.
Canonical v47 remains the Fusion algorithm and human-capture baseline. An
investigation named exactly v48 may begin only after nominal-power real Fusion
workload evidence establishes one of the triggers in `v48_trigger_policy.json`.
Power depletion, intermittent air traffic, LEDs-off evidence, or terminal
off-air behavior alone is not such a trigger. No v48 behavior is pre-approved.

Version names are integers only: `v47`, `v48`, `v49`, …; aliases, suffixes,
roles such as `prod`/`val`, and hyphenated subversions are forbidden.
