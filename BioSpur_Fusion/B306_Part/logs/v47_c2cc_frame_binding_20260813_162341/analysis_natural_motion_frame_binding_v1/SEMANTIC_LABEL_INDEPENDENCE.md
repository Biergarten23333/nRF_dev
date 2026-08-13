# Semantic-label independence

The estimator API receives only timestamped N and V4 paths. Metadata is carried into audit output but `_factor_arrays` never reads it. The deterministic test permutes every operator label while keeping sensor arrays unchanged and requires an identical transform and verdict. Action names select time ranges only.
