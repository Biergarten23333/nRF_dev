# Phase 1 / Phase 2 / Phase 3 Comparison

## What Changed

- Phase 1: wall-only baseline with one default heavy reflective wall model.
- Phase 2: Phase 1 plus photo-inspired random metal/equipment boxes near the layout boundary.
- Phase 3: wall material sensitivity: drywall, gypsum, aerated concrete, sand-lime brick, and reinforced concrete C25/C30/C40.

## 4-Wall Key Distances

| distance | Phase1 default | Phase2 wall+metal | drywall | sand-lime | RC C30/37 | RC C40/50 |
|---:|---:|---:|---:|---:|---:|---:|
| 0cm | 0.764 | 0.892 | 0.195 | 0.622 | 0.889 | 0.998 |
| 40cm | 0.287 | 0.405 | 0.095 | 0.221 | 0.363 | 0.426 |
| 100cm | 0.088 | 0.123 | 0.066 | 0.080 | 0.099 | 0.109 |

## Reading

- Wall distance is still the strongest control knob.
- Material matters strongly when the layout is close to the wall; the difference shrinks as distance approaches 100cm.
- Metal clutter in Phase 2 can make even no-wall/low-wall cases worse because it adds local reflectors near the boundary.
- Reinforced concrete C30/37 and C40/50 are worse than sand-lime brick in this model, but the difference between C25/C30/C40 is intentionally smaller than the difference between drywall and reinforced concrete.

## Figure

- `figures/comparison/phase123_4wall_pos_p95_comparison.png`
