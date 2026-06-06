# Stratified Keep-k Replay

Method: exhaustive fixed dropped-set replay. Each row records which anchors were dropped, then aggregates by upper/lower/balanced composition.

This is separate from the random MC5000 keep-k run and is meant to explain which missing-anchor patterns hurt most.

## V4-io / T4 Composition Snapshot

| kind | keep_k | category | drop_sets | metric_mm |
| --- | ---: | --- | ---: | ---: |
| roto | 7 | lower_heavy | 4 | 18.3 |
| roto | 7 | upper_heavy | 4 | 16.0 |
| roto | 6 | balanced | 16 | 22.3 |
| roto | 6 | lower_heavy | 6 | 25.2 |
| roto | 6 | upper_heavy | 6 | 18.1 |
| roto | 5 | lower_heavy | 24 | 30.0 |
| roto | 5 | lower_heavy | 4 | 32.2 |
| roto | 5 | upper_heavy | 4 | 20.6 |
| roto | 5 | upper_heavy | 24 | 24.3 |
| roto | 4 | balanced | 36 | 45.3 |
| roto | 4 | lower_heavy | 16 | 50.2 |
| roto | 4 | lower_heavy | 1 | 38.5 |
| roto | 4 | upper_heavy | 1 | 16.3 |
| roto | 4 | upper_heavy | 16 | 34.5 |

## Worst V4-io / T4 Drop Sets

| kind | keep_k | dropped_set | category | metric_mm |
| --- | ---: | --- | --- | ---: |
| roto | 4 | ACEG | balanced | 257.7 |
| roto | 4 | BCEH | balanced | 134.8 |
| roto | 4 | CDEF | balanced | 96.8 |
| roto | 4 | ADFG | balanced | 91.4 |
| roto | 4 | ABGH | balanced | 89.5 |
| roto | 4 | ACDF | lower_heavy | 79.1 |
| roto | 4 | BCGH | balanced | 74.7 |
| roto | 4 | ABCH | lower_heavy | 71.5 |
| roto | 4 | ABCG | lower_heavy | 69.3 |
| roto | 4 | ABDG | lower_heavy | 67.4 |
| roto | 4 | ABEG | balanced | 66.9 |
| roto | 4 | BCEG | balanced | 64.5 |
| roto | 4 | ACGH | balanced | 59.5 |
| roto | 4 | BDFH | balanced | 59.4 |
| roto | 4 | BCDE | lower_heavy | 59.0 |
| roto | 4 | CDEG | balanced | 59.0 |
