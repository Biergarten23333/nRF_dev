# Tests

`python3 -m pytest fusion_v1/tests -q`: **16 passed**. Tests cover accepted infrastructure plus FK invariance, SO(3) interpolation, asynchronous root interpolation, orientation/range/joint/axis residuals, robust health, absence of old body imports, and absence of independent node XYZ state. Real deterministic replay is represented by immutable slice caches and serialized converged outputs; byte-identical replay of optimizer trajectories is not yet asserted.
