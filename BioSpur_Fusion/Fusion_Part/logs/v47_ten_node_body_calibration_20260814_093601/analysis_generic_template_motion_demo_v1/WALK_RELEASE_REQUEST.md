# Walk release request

The calibration-only generic-template preview must be reviewed and explicitly accepted before any walk payload is opened.

Acceptance will make exactly this one-way transition:

```text
WALK_HELDOUT_STATUS:
  SEALED -> CONSUMED_FOR_VISUALIZATION
```

`final_still` will remain sealed. After preview acceptance, no template dimension, estimator parameter, gate, covariance, robust-loss threshold, rejection rule, alignment rule, camera rule or rendering rule may change.

This request does not itself authorize or open walk data.
