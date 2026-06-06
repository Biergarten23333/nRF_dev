# Pair Residual Diagnostics

Raw directional asymmetry uses staged sweep `pairs_all.csv`; layout residual bias uses solver residual table.

G-involving pairs are explicitly flagged because OptiTrack G marker labeling is suspect.

## V4-io worst pairs

| version | eval_set | pair | residual_mm | abs_residual_mm | involves_G |
| --- | --- | --- | --- | --- | --- |
| v4-io | all1000 | B-C | -141.7 | 141.7 | False |
| v4-io | all1000 | B-G | 113.7 | 113.7 | True |
| v4-io | all1000 | D-E | 99.8 | 99.8 | False |
| v4-io | all1000 | D-F | 91.4 | 91.4 | False |
| v4-io | all1000 | F-H | -59.3 | 59.3 | False |
| v4-io | all1000 | B-H | 38.4 | 38.4 | False |
| v4-io | all1000 | C-E | 31.4 | 31.4 | False |
| v4-io | all1000 | E-G | -31.3 | 31.3 | True |
| v4-io | all1000 | D-H | -28.2 | 28.2 | False |
| v4-io | all1000 | A-D | -28.1 | 28.1 | False |
| v4-io | all1000 | C-G | -27.3 | 27.3 | True |
| v4-io | all1000 | A-H | 25.6 | 25.6 | False |

## Largest raw directional asymmetries

| pair | asym_a_minus_b_mm | abs_asym_mm | involves_G |
| --- | --- | --- | --- |
| D-F | 22.0 | 22.0 | False |
| E-F | 20.0 | 20.0 | False |
| C-F | 18.0 | 18.0 | False |
| A-H | -17.0 | 17.0 | False |
| B-E | -16.0 | 16.0 | False |
| C-E | 15.0 | 15.0 | False |
| B-H | -9.0 | 9.0 | False |
| C-H | -9.0 | 9.0 | False |
| A-C | -9.0 | 9.0 | False |
| F-H | -9.0 | 9.0 | False |
| B-C | -9.0 | 9.0 | False |
| C-D | 8.5 | 8.5 | False |
