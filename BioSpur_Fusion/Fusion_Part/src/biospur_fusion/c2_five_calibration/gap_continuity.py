"""Extend existing pose-correction diffusion across missing IMU samples.

This is a process prior, never a fabricated inertial measurement. Independent
per-step increments give endpoint variance proportional to elapsed steps.
The inherited engineering smoothness normalization is preserved explicitly.
"""
import torch


def gap_correction_energy(parameters, seed, valid, *, consumed_frames=0):
    ids=torch.nonzero(valid,as_tuple=False).flatten()
    left,right=ids[:-1],ids[1:]
    keep=(right-left>1)&(right>=consumed_frames)
    left,right=left[keep],right[keep]
    zero=parameters.sum()*0.
    if not len(left):return zero,zero
    steps=(right-left).to(parameters.dtype)
    pair_count=int((valid[:-1]&valid[1:]).sum())
    if pair_count==0:raise ValueError('existing continuity normalization requires valid adjacent pairs')
    change=parameters[:,:9]-seed
    pose=((change[right]-change[left])/.12).square()/steps[:,None]
    correction=((parameters[right,9:]-parameters[left,9:])/.12).square()/steps[:,None]
    return .1*pose.sum()/(pair_count*9),.1*correction.sum()/(pair_count*12)
