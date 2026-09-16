"""Causal batching of the released IMUCoCo LSTMs, preserving their weights.

The upstream offline API discards hidden states. This adapter exposes those
states so consecutive chunks/actions remain one sequence. Equivalence to the
author's online implementation is tested with the actual released weights.
"""
from __future__ import annotations

import torch

from .upstream import load_features, set_placements, DEFAULT_UPSTREAM


class ChunkedFeatures:
    def __init__(self, *, root=DEFAULT_UPSTREAM, device='cpu'):
        self.model = load_features(root, device=device, online=False)
        self.reset()

    def reset(self):
        self.mfe_state = [None] * 24
        self.jnm_state = [[None] * self.model.n_jnm_layers for _ in range(24)]

    def set_placements(self, positions):
        self.reset()
        return set_placements(self.model, positions)

    @torch.inference_mode()
    def forward(self, x):
        if x.ndim != 4 or x.shape[0] != 1 or x.shape[-1] != 9:
            raise ValueError('expected one B x T x D x 9 feature sequence')
        output = []
        m = self.model
        for j in range(24):
            sensor = int(m.current_device_2_joint_mapping[j])
            y = m.mfes[j].linear(x[:, :, sensor])
            y, self.mfe_state[j] = m.mfes[j].rnn(y, self.mfe_state[j])
            for layer, rnn in enumerate(m.jnms[j].rnn_layers):
                gamma, beta = m.placement_codes_buffered[j, layer]
                y, self.jnm_state[j][layer] = rnn(gamma*y+beta, self.jnm_state[j][layer])
            output.append(y)
        result = torch.stack(output, dim=2)
        result[:, :, m.nodes_over_error_thres] = 0
        return result
