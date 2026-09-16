"""Pinned upstream loading, with explicit asset and weight compatibility checks."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import torch

REVISION = 'd7dd17c70abccfc63935ce9e25452c6d312df630'
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_UPSTREAM = ROOT / 'third_party/imucoco'


def verify_assets(root=DEFAULT_UPSTREAM):
    root = Path(root)
    manifest = json.loads((root / 'UPSTREAM_MANIFEST.json').read_text())
    if manifest['revision'] != REVISION:
        raise ValueError('unexpected IMUCoCo revision')
    for path, expected in manifest['files'].items():
        actual = hashlib.sha256((root / path).read_bytes()).hexdigest()
        if actual != expected['sha256']:
            raise ValueError(f'upstream asset changed: {path}')
    return manifest


def feature_class(root=DEFAULT_UPSTREAM):
    spec = importlib.util.spec_from_file_location('biospur_upstream_imucoco', Path(root) / 'models/imucoco.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.IMUCoCo


def load_features(root=DEFAULT_UPSTREAM, *, device='cpu', online=True):
    """Reuse the released model's own spatial buffers; no SMPL substitute.

The checkpoint includes its training mesh coordinates and coordinate encoders.
These suffice for feature inference. Full pose/FK still needs a body model.
"""
    root = Path(root)
    checkpoint = torch.load(root / 'saved_checkpoints/imucoco_best.pth', map_location='cpu', weights_only=True)
    model = feature_class(root)(
        coordinate_origins=checkpoint['coordinate_origins'],
        coordinate_min=checkpoint['sces.0.min_vals'],
        coordinate_max=checkpoint['sces.0.max_vals'],
        smpl_mesh_coordinates=torch.cat((checkpoint['mesh_categories'], checkpoint['mesh_positions']), dim=1),
        online_mode=online,
        joint_node_allocation_map=str(root / 'saved_checkpoints/all_error_loss_map.pth'),
    )
    # Released separately, not embedded in the encoder checkpoint.
    checkpoint['mesh_transfer_loss'] = model.mesh_transfer_loss.clone()
    if online:
        model.load_offline_state_dict_to_online_model(checkpoint)
        # The upstream converter uses strict=False. Independently check all
        # learned tensors against the released offline checkpoint.
        for name, value in model.state_dict().items():
            if 'lstm_cells' not in name and name in checkpoint:
                if not torch.equal(value, checkpoint[name]):
                    raise ValueError(f'weight conversion mismatch: {name}')
    else:
        model.load_state_dict(checkpoint, strict=True)
    model.to(device).eval()
    if online:
        model.prepare_parallel_sce_implementation()
        model.prepare_parallel_joint_node_implementation()
    return model


def set_placements(model, positions):
    positions = torch.as_tensor(positions, dtype=torch.float32, device=model.mesh_positions.device)
    if positions.ndim != 2 or positions.shape[1] != 3 or not 1 <= len(positions) <= 13:
        raise ValueError('expected D x 3 fixed body-surface coordinates')
    if not torch.isfinite(positions).all():
        raise ValueError('nonfinite placement')
    model.set_current_device_coordinates(positions)
    model.buffer_placement_codes_with_current_devices(parallel=model.online_mode)
    mapping = model.current_device_2_joint_mapping.detach().cpu().numpy()
    if (mapping < 0).any() or (mapping >= len(positions)).any():
        raise ValueError('model selected an unavailable sensor')
    return mapping
