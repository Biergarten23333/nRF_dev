# Range correction ledger

Verdict: `DELAY_CONVENTION_PASS`.

| Stage | Quantity | Owner | Applied here |
|---|---|---|---|
| DW1000 timestamps | TX/RX antenna timestamp calibration (`16436`) | DWM1001C firmware via `dwt_settxantennadelay` / `dwt_setrxantennadelay` | Once, in radio timestamps |
| SS-TWR | Clock-offset-corrected ToF from `rtd_init` and `rtd_resp` | `ss_twr_init.c` | Once before `raw_distance_mm` |
| B306 transport | `range_mm` from the range tracker | Fusion-link serializer | Pass-through; no V4 bias |
| Inter-anchor residual bias | `d_anchor_mm` | V4-io layout | Estimated once from SW100 |
| Tag observation model | `norm(tag-anchor) + d_anchor + tag_delay` | UWB_TAG_T4/UWB_TAG_U5 | Applies V4 anchor delay once |
| Common tag delay | `tag_delay_mm` | Layout/tag calibration | 0.0 mm; no unsupported fit |

Source audit follows the value from the SS-TWR ToF calculation into
`uwb_range_tracker_record_success`, the BSL `range_mm` field, and then the
frozen Tag solver prediction. Firmware does not apply a V4-io anchor residual
bias. T4/U5 do not reapply the DW1000 timestamp register value. There is thus
no duplicated correction in this replay.

