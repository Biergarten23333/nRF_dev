# Task G3 - Vicon Phase-Center Alternative

Verdict: **not explainable by small phase-center offsets**.

Best model: C sigma= mean held-out median=54.291 mm, max offset=128.529 mm.

Offsets above 50 mm are flagged physically suspicious; the decision threshold for a clean RF phase-center explanation is stricter (<20 mm and matching V4/V5 references within 5 mm).

| model | sigma_offset | mean_test_median | std_test_median | mean_test_rmse | max_offset_mm | any_offset_gt50 |
| --- | --- | --- | --- | --- | --- | --- |
| C |  | 54.291 | 3.758 | 65.433 | 128.529 | True |
| E | 40.000 | 59.160 | 12.569 | 77.636 | 357.600 | True |
| E | 20.000 | 59.637 | 12.760 | 77.628 | 340.505 | True |
| E | 2.000 | 59.675 | 8.781 | 73.558 | 129.319 | True |
| D |  | 61.534 | 14.368 | 70.704 | 198.291 | True |
| E | 10.000 | 61.554 | 13.544 | 77.740 | 287.680 | True |
| E | 5.000 | 63.188 | 12.996 | 77.355 | 201.174 | True |
| B |  | 66.065 | 23.932 | 75.916 | 37.323 | False |
| G |  | 68.886 | 16.980 | 77.471 | 0.000 | False |
| A |  | 70.825 | 23.303 | 80.274 | 0.000 | False |
| F |  | 73.328 | 24.822 | 83.532 | 0.000 | False |
