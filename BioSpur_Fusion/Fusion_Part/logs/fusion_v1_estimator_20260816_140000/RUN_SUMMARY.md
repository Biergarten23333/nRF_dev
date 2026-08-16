# Run summary

The first clean articulated estimator was implemented and executed on initial-still, left-elbow, trunk, walk and final-still real slices. All five nominal solves converged numerically. Controlled +0.5 m outlier, +0.3 m sustained bias, four dropout durations, three timestamp shifts, eight leave-one-anchor cases, and wrist/ankle UWB removal were executed. Golf and boxing were not opened.

Scientific verdict: `ESTIMATOR_IMPLEMENTED_BUT_SCIENTIFICALLY_INVALID` because dynamic/validation robust range residual spreads are 0.32--0.43 m, pair health commonly collapses, two leave-one-anchor cases fail convergence, and the reduced model lacks optimized orientation states and uncertainty growth.
