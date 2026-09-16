"""Reference-invariant metric after eliminating common root acceleration.

Independent equal-variance node errors become correlated after subtraction of
one shared node: C = I + 11'. C^(-1/2) preserves inter-limb contrasts and scales
the common residual by 1/sqrt(5). The variance scale is still engineering-owned;
this does not claim measured independent noise or model-error covariance.
"""
import math

MODEL = 'shared_reference_equal_node_variance_v1'
LEGACY = 'legacy_independent_differences'


def whiten_relative(values, *, node_axis):
    if values.shape[node_axis] != 4:
        raise ValueError('four differences against the fifth retained IMU required')
    return values + (1/math.sqrt(5)-1)*values.mean(dim=node_axis,keepdim=True)


def metric_residual(values, geometry, *, node_axis):
    model=geometry.get('acceleration_observation_model',LEGACY)
    if model==LEGACY:return values
    if model!=MODEL:raise ValueError('unknown acceleration observation model')
    return whiten_relative(values,node_axis=node_axis)
