"""One causal correction bandwidth budget shared by all raw UWB tags."""
import math


class GlobalCorrectionBudget:
    def __init__(self, time_constant_s, maximum_gap_s=.12):
        if not all(math.isfinite(x) and x > 0 for x in (time_constant_s, maximum_gap_s)):
            raise ValueError('correction budget parameters must be finite positive')
        self.time_constant_s = float(time_constant_s)
        self.maximum_gap_s = float(maximum_gap_s)
        self.last_epoch_s = None

    def consume(self, epoch_s):
        """Call for every raw attempt, including rejected/fewer-link sweeps.

        Lost or rejected observations never accumulate a future catch-up gain.
        The first event establishes the clock without inventing an interval.
        """
        if not math.isfinite(epoch_s):
            raise ValueError('nonfinite correction epoch')
        dt = 0. if self.last_epoch_s is None else epoch_s-self.last_epoch_s
        if dt < 0:
            raise ValueError('correction epochs must be nondecreasing')
        self.last_epoch_s = float(epoch_s)
        if dt == 0 or dt > self.maximum_gap_s:
            return 0., dt
        return -math.expm1(-dt/self.time_constant_s), dt
