# No-Tag Multipath Intake

Generated: `2026-06-07T20:54:40.449043+00:00`

## Policy

- No-tag multipath captures can be used for environment and residual-risk analysis.
- They must not be used as real localization-error labels.
- They remain `train_allowed=false` until a ground-truth tag trajectory is available.

## Current Intake

- Multipath-like captures: `4`
- No-tag multipath captures: `1`

| Capture | Env | Condition | Layouts | Tag | GT | Use | Notes |
|---|---|---|---:|---|---|---|---|
| `Garage_Test` | `garage` | `multipath_possible` | 5 | `true` | `false` | `ranking_and_proxy_analysis` | `Garage proxy capture; usable for ranking and multipath-risk context` |
| `Garage_Test_nah` | `garage` | `multipath_possible` | 0 | `false` | `false` | `multipath_risk_analysis` | `No post tag capture; keep as no-tag multipath intake` |
| `Garage_test_2` | `garage` | `multipath_possible` | 10 | `true` | `false` | `ranking_and_proxy_analysis` | `Garage proxy capture; not a real error label` |
| `Garage_test_nah_2` | `garage` | `multipath_possible` | 5 | `true` | `false` | `ranking_and_proxy_analysis` | `Near garage proxy capture; not a real error label` |

## Required Metadata For Future Basement/NLOS Captures

- `environment_type`: `basement`, `garage`, `indoor_lab`, or `outdoor`.
- `condition`: `multipath`, `nlos`, `multipath_possible`, or `los`.
- `has_tag_capture`: set `false` when static/roto/wand/tag replay is missing.
- `has_ground_truth`: set `false` unless OptiTrack or equivalent true trajectory exists.
- `notes`: describe reflectors, wall material, obstacles, and anchor visibility.
