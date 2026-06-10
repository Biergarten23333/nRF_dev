# Filtered Deployment Static Results

These results are deployment-output metrics. They do not replace the official unfiltered calibration/measurement validation.

OptiTrack truth was used only after solving/filtering, for final evaluation under the anchor-locked transform.

## V4-io / all8 Ranking

| rank | solver | family | median 3D | p95 3D | RMS 3D | repeat D3 std median |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | T4+F5 | external_position_filter | 65.3 | 178.1 | 110.5 | 18.6 |
| 2 | T4+F4 | external_position_filter | 66.1 | 177.4 | 110.3 | 21.3 |
| 3 | T4+F1 | external_position_filter | 66.1 | 177.4 | 110.3 | 30.1 |
| 4 | T4+F2 | external_position_filter | 66.1 | 177.4 | 110.3 | 30.1 |
| 5 | T4+F3 | external_position_filter | 67.1 | 176.8 | 110.3 | 35.5 |
| 6 | T4+F0 | external_position_filter | 72.7 | 171.5 | 109.8 | 67.1 |

## Full Summary

| version | solver | eval | median 3D | p95 3D | RMS 3D | h.med | v.med | repeat D3 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v4-io | T4+F5 | all8 | 65.3 | 178.1 | 110.5 | 37.7 | 55.1 | 18.6 |
| v4-io | T4+F4 | all8 | 66.1 | 177.4 | 110.3 | 37.7 | 55.2 | 21.3 |
| v4-io | T4+F1 | all8 | 66.1 | 177.4 | 110.3 | 37.7 | 55.2 | 30.1 |
| v4-io | T4+F2 | all8 | 66.1 | 177.4 | 110.3 | 37.7 | 55.2 | 30.1 |
| v4-io | T4+F3 | all8 | 67.1 | 176.8 | 110.3 | 37.8 | 55.4 | 35.5 |
| v4-io | T4+F0 | all8 | 72.7 | 171.5 | 109.8 | 37.4 | 61.9 | 67.1 |
