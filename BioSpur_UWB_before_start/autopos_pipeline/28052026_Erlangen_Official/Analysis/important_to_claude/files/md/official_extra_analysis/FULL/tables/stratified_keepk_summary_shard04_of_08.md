# Stratified Keep-k Replay

Method: exhaustive fixed dropped-set replay. Each row records which anchors were dropped, then aggregates by upper/lower/balanced composition.

This is separate from the random MC5000 keep-k run and is meant to explain which missing-anchor patterns hurt most.

## V4-io / T4 Composition Snapshot

| kind | keep_k | category | drop_sets | dropped_lower_med | dropped_upper_med | metric_mm |
| --- | ---: | --- | ---: | ---: | ---: | ---: |

## V4-io / T4 Count-Split Detail

| kind | keep_k | category | dropped_lower | dropped_upper | drop_sets | metric_mm |
| --- | ---: | --- | ---: | ---: | ---: | ---: |

## Worst V4-io / T4 Drop Sets

| kind | keep_k | dropped_set | category | metric_mm |
| --- | ---: | --- | --- | ---: |
