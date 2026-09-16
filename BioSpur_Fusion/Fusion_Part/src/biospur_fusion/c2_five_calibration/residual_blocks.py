"""Elementwise square-root factors of existing engineering energies.

These preserve objective normalization, not measured statistical covariance.
Row support/ownership must be supplied separately before marginalization.
"""
import math
import torch


class ResidualBlocks(dict):
    """Optional whole-tape mean counts for evaluating overlapping windows.

    Without global counts, captures the denominators used by record_mean.
    With global counts, unknown blocks fail instead of acquiring a local
    normalization. Summed-information factors are unaffected.
    """
    def __init__(self, *, global_mean_counts=None):
        super().__init__()
        self.global_mean_counts = (None if global_mean_counts is None else
                                   dict(global_mean_counts))
        self.mean_counts = {}

    def mean_count(self, name, local_count):
        count = local_count
        if self.global_mean_counts is not None:
            if name not in self.global_mean_counts:
                raise ValueError(f'missing whole-tape normalization: {name}')
            count = self.global_mean_counts[name]
            if isinstance(count, bool) or not isinstance(count, int) or count < local_count:
                raise ValueError('whole-tape count must cover the local residuals')
        self.mean_counts[name] = count
        return count


def signed_huber_residual(value):
    """Squared result equals 2*smooth_l1(value, 0), with derivative 1 at 0.

    Clamp the inactive square-root branch too: torch.where evaluates both
    branches, and sqrt(0)'s infinite derivative otherwise poisons backward.
    This is an energy-equivalent residual, not an exact Hessian factor.
    """
    tail = value.sign() * (2*value.abs()-1).clamp_min(1.).sqrt()
    return torch.where(value.abs() <= 1., value, tail)


def record_weighted(blocks, name, value, information):
    """Export a sum with fixed, per-element information (no renormalizing)."""
    if blocks is None:
        return
    if name in blocks:
        raise ValueError(f'duplicate residual block: {name}')
    info = torch.as_tensor(information, dtype=value.dtype, device=value.device)
    if info.requires_grad or not torch.isfinite(info).all() or torch.any(info < 0):
        raise ValueError('fixed finite nonnegative information required')
    if info.shape not in (torch.Size([]), value.shape):
        raise ValueError('information must be scalar or match residual shape')
    blocks[name] = value * info.sqrt()


def record_mean(blocks, name, value, weight=1.):
    """Record sqrt(weight / count) * each residual, never sqrt(group loss)."""
    if blocks is None:
        return
    if name in blocks:
        raise ValueError(f'duplicate residual block: {name}')
    if not math.isfinite(weight) or weight < 0 or value.numel() == 0:
        raise ValueError('finite nonnegative weight and nonempty residual required')
    count = blocks.mean_count(name, value.numel()) if isinstance(blocks, ResidualBlocks) else value.numel()
    blocks[name] = value * math.sqrt(weight / count)
