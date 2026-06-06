# Extended 4-Wall Convergence Study

## Setup

- Re-ran Phase 1, Phase 2, and Phase 3 for 4-wall only.
- Distance range: 0-300 cm in 5 cm steps.
- Clear LOS baseline is estimated from the 0-wall Phase 1 noise floor.

- Clear LOS p95 baseline: `0.062 m`

## Convergence Distances

| series | p95 @300cm | <= LOS+10% | <= LOS+25% | <= LOS+50% | <=0.10m | <=0.15m | <=0.25m |
|---|---:|---:|---:|---:|---:|---:|---:|
| Phase1 default wall | 0.063 | 135 | 115 | 100 | 95 | 70 | 50 |
| Phase2 wall+metal median | 0.063 | 175 | 145 | 125 | 120 | 90 | 65 |
| Phase3 light_drywall | 0.063 | 85 | 60 | 45 | 40 | 15 | 0 |
| Phase3 sand_lime_brick | 0.062 | 130 | 105 | 90 | 85 | 60 | 35 |
| Phase3 reinforced_concrete_C30_37 | 0.063 | 145 | 125 | 105 | 100 | 80 | 55 |
| Phase3 reinforced_concrete_C40_50 | 0.063 | 155 | 130 | 115 | 110 | 85 | 65 |

## Reading

- If the criterion is strict LOS+25%, most heavy wall/metal cases need much more than 1m.
- Drywall converges quickly; reinforced concrete and wall+metal converge slowly.
- The previous 0-100cm plot was not enough to see full convergence for heavy wall and metal clutter.

## Figures

- `figures/extended_4wall_0to300cm/extended_4wall_convergence_linear.png`
- `figures/extended_4wall_0to300cm/extended_4wall_convergence_log.png`
- `figures/extended_4wall_0to300cm/extended_4wall_ratio_to_los.png`
