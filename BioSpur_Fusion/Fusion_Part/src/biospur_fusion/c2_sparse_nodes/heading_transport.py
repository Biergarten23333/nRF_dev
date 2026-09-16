"""Continuous replay of a frozen calibration-only relative-yaw curve."""
import numpy as np
from scipy.interpolate import PchipInterpolator


def temporal_heading(time_s, node, calibration):
    curve=calibration['temporal_heading_curves'].get(node)
    if curve is None:
        value=np.full(len(time_s),calibration['frozen_heading_correction_rad'].get(node,0.))
        return value+session_increment(time_s,node,calibration)
    times=np.asarray(curve['time_s'],dtype=float)
    angles=np.asarray(curve['correction_rad'],dtype=float)
    if (times.ndim!=1 or len(times)<2 or times.shape!=angles.shape
            or not np.isfinite(times).all() or not np.isfinite(angles).all()
            or np.any(np.diff(times)<=0)):
        raise ValueError('temporal heading requires ordered finite calibration knots')
    # No periodic resets or continued extrapolation of a calibration slope.
    # H uses the frozen last correction; its future drift is not observed here.
    query=np.asarray(time_s,dtype=float)
    if not np.isfinite(query).all():raise ValueError('nonfinite replay time')
    value=PchipInterpolator(times,angles)(np.clip(query,times[0],times[-1]))
    if 'joint_increment_rad' in curve:
        delta=np.asarray(curve['joint_increment_rad'],dtype=float)
        if delta.shape!=times.shape or not np.isfinite(delta).all():
            raise ValueError('joint heading increments must match calibration knots')
        value=value+np.interp(query,times,delta)
    return value+session_increment(time_s,node,calibration)


def session_increment(time_s,node,calibration):
    """A separate full-session increment must not change the original anchors."""
    curve=calibration.get('session_heading_increments',{}).get(node)
    if curve is None:return np.zeros(len(time_s))
    times=np.asarray(curve['time_s'],dtype=float)
    angles=np.asarray(curve['correction_rad'],dtype=float)
    query=np.asarray(time_s,dtype=float)
    if (times.ndim!=1 or len(times)<2 or times.shape!=angles.shape
            or not np.isfinite(times).all() or not np.isfinite(angles).all()
            or not np.isfinite(query).all() or np.any(np.diff(times)<=0)):
        raise ValueError('invalid full-session heading increment')
    return np.interp(query,times,angles)
