# Stratified Keep-k Replay

Method: exhaustive fixed dropped-set replay. Each row records which anchors were dropped, then aggregates by upper/lower/balanced composition.

This is separate from the random MC5000 keep-k run and is meant to explain which missing-anchor patterns hurt most.

## V4-io / T4 Composition Snapshot

| kind | keep_k | category | drop_sets | metric_mm |
| --- | ---: | --- | ---: | ---: |
| static | 7 | lower_heavy | 4 | 65.2 |
| static | 7 | upper_heavy | 4 | 61.0 |
| static | 6 | balanced | 16 | 71.5 |
| static | 6 | lower_heavy | 6 | 84.5 |
| static | 6 | upper_heavy | 6 | 63.1 |
| static | 5 | lower_heavy | 24 | 92.8 |
| static | 5 | lower_heavy | 4 | 103.1 |
| static | 5 | upper_heavy | 4 | 58.9 |
| static | 5 | upper_heavy | 24 | 68.6 |
| static | 4 | balanced | 36 | 105.8 |
| static | 4 | lower_heavy | 16 | 117.6 |
| static | 4 | lower_heavy | 1 | 109.0 |
| static | 4 | upper_heavy | 1 | 50.3 |
| static | 4 | upper_heavy | 16 | 65.8 |

## Worst V4-io / T4 Drop Sets

| kind | keep_k | dropped_set | category | metric_mm |
| --- | ---: | --- | --- | ---: |
| static | 4 | ACDE | lower_heavy | 162.7 |
| static | 4 | ABDH | lower_heavy | 160.6 |
| static | 4 | ABCE | lower_heavy | 153.4 |
| static | 4 | BDEH | balanced | 148.3 |
| static | 4 | CDEH | balanced | 147.7 |
| static | 4 | ABEH | balanced | 145.7 |
| static | 4 | BCDH | lower_heavy | 134.7 |
| static | 5 | CDE | lower_heavy | 133.6 |
| static | 4 | ABCH | lower_heavy | 130.4 |
| static | 4 | ACEH | balanced | 129.9 |
| static | 4 | ABDF | lower_heavy | 129.1 |
| static | 5 | ABH | lower_heavy | 127.5 |
| static | 4 | ADEF | balanced | 127.0 |
| static | 4 | BDFH | balanced | 123.6 |
| static | 4 | ABFH | balanced | 123.5 |
| static | 5 | ACE | lower_heavy | 123.4 |
