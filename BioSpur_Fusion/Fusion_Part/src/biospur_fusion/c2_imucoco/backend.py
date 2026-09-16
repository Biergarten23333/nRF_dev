"""Upstream pose network and stateful five-node execution, in a worker process.

Call from an isolated CLI process: upstream imports `models`, `utils` and
`articulate` as top-level packages and loads SMPL relative to its working dir.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import numpy as np
import torch

from .upstream import DEFAULT_UPSTREAM, load_features, set_placements
from .chunked import ChunkedFeatures
from .contact_contract import contact_head_contract

# Published wrist locations and above-ankle locations from utils/imu_config.py.
VERTICES = [3021, 1962, 5431, 1120, 4606]


def load_pose(smpl_path, work_dir, *, root=DEFAULT_UPSTREAM, device='cpu'):
    smpl_path, work_dir, root = Path(smpl_path).resolve(), Path(work_dir).resolve(), Path(root).resolve()
    if not smpl_path.is_file():
        raise FileNotFoundError('official SMPL male .pkl is required: ' + str(smpl_path))
    link = work_dir / 'smpl/SMPL_MALE.pkl'
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and link.resolve() != smpl_path:
        raise ValueError('work directory already uses a different SMPL asset')
    if not link.exists():
        link.symlink_to(smpl_path)
    sys.path.insert(0, str(root))
    os.chdir(work_dir)
    from models.dtp import Poser
    from utils.imu_config import body_model
    poser = Poser(joint_feature_dim=128, n_hidden=300, n_glb=40, num_layer=3,
                  n_total_devices=24, load_tran_module=True)
    state = torch.load(root / 'saved_checkpoints/poser_dtp_best.pth', map_location='cpu', weights_only=True)
    poser.load_state_dict(state, strict=True)
    return poser.to(device).eval(), body_model


class PoseStream:
    def __init__(self, poser, *, root=DEFAULT_UPSTREAM, device='cpu'):
        self.device = device
        self.poser = poser
        self.encoder = load_features(root, device=device)
        self.positions = self.encoder.mesh_positions[VERTICES].detach().clone()
        self.mapping = set_placements(self.encoder, self.positions)
        self.reset()

    def reset(self):
        self.h_mfe = self.h_jnm = None
        initial = torch.eye(3, device=self.device).expand(1, 24, 3, 3)
        r6d = initial[..., :2].transpose(-1, -2).flatten(-2)
        self.h_pose = self.poser.init_hidden_states(torch.zeros(1, 24, 3, device=self.device), r6d)
        self.translation = None
        self.poser.saved_previous_joint_pos = None

    @torch.inference_mode()
    def step(self, features):
        x = torch.as_tensor(features, dtype=torch.float32, device=self.device)[None, None]
        if x.shape != (1, 1, 5, 9) or not torch.isfinite(x).all():
            raise ValueError('exactly five finite IMU feature vectors are required')
        f, self.h_mfe, self.h_jnm = self.encoder.inference_time_forward_mesh_online(x, self.h_mfe, self.h_jnm)
        pose, global_pose, self.translation, self.h_pose = self.poser.forward_online(
            f, self.h_pose, current_tran=self.translation, compute_tran='transpose')
        if not torch.isfinite(global_pose).all():
            raise ValueError('nonfinite upstream prediction')
        return pose[0, 0].cpu().numpy(), global_pose[0, 0].cpu().numpy(), self.translation[0].cpu().numpy()

    def run(self, features, *, wall_limit_s=600., progress=None):
        start = time.monotonic()
        local, global_pose, translation = [], [], []
        for i, row in enumerate(features):
            if time.monotonic()-start > wall_limit_s:
                raise TimeoutError('IMUCoCo stage wall-time budget reached')
            a, b, c = self.step(row)
            local.append(a); global_pose.append(b); translation.append(c)
            if progress and i % 600 == 0:
                progress(dict(frame=i, total=len(features), wall_s=time.monotonic()-start))
        return dict(local_rotation=np.stack(local), global_rotation=np.stack(global_pose),
                    translation_m=np.stack(translation)), dict(frames=len(features),
                    wall_s=time.monotonic()-start, sensor_count=5, sensor_vertices=VERTICES,
                    joint_to_device_mapping=self.mapping.tolist())


class ChunkedPoseStream:
    """Published causal pose network with bounded, state-preserving batches.

Global translation is deliberately not evaluated in this root-centred pose
experiment. The upstream contact/velocity outputs remain available in the NPZ.
"""
    def __init__(self, poser, *, root=DEFAULT_UPSTREAM, device='cpu', initial_global_rotation=None, sensor_vertices=None):
        self.poser, self.device = poser, device
        self.encoder = ChunkedFeatures(root=root, device=device)
        self.vertices = list(VERTICES if sensor_vertices is None else sensor_vertices)
        if len(self.vertices)!=5 or len(set(self.vertices))!=5 or any(i<0 or i>=6890 for i in self.vertices):
            raise ValueError('five distinct SMPL sensor vertices are required')
        self.positions = self.encoder.model.mesh_positions[self.vertices].detach().clone()
        self.mapping = self.encoder.set_placements(self.positions)
        initial = torch.eye(3, device=device).expand(1, 24, 3, 3)
        if initial_global_rotation is not None:
            initial = torch.as_tensor(initial_global_rotation, dtype=torch.float32, device=device).reshape(1,24,3,3)
            if not torch.isfinite(initial).all() or not torch.allclose(initial@initial.transpose(-1,-2), torch.eye(3,device=device), atol=1e-5):
                raise ValueError('calibrated initial state must contain proper rotations')
            if not torch.allclose(torch.linalg.det(initial),torch.ones(1,24,device=device),atol=1e-5):
                raise ValueError('calibrated initial state cannot contain a reflection')
        self.h_pose = poser.init_hidden_states(torch.zeros(1, 24, 3, device=device),
            initial[..., :2].transpose(-1, -2).flatten(-2))

    @torch.inference_mode()
    def forward(self, features):
        x = torch.as_tensor(features, dtype=torch.float32, device=self.device)[None]
        if x.shape[2:] != (5, 9) or not torch.isfinite(x).all():
            raise ValueError('exactly five finite input streams required')
        feat = self.encoder.forward(x)
        local, global_pose, velocity, contact, self.h_pose = self.poser.forward_online(feat, self.h_pose)
        return dict(local_rotation=local[0].cpu().numpy(),
                    global_rotation=global_pose[0].cpu().numpy(),
                    predicted_root_velocity_scaled=velocity[0].cpu().numpy(),
                    contact_logits=contact[0].cpu().numpy())

    def run(self, features, *, chunk_size=120, wall_limit_s=1200., progress=None,
            input_valid=None):
        """Keep the output grid while withholding invalid rows from every RNN.

        Invalid outputs hold the last prediction and remain invalid in the
        caller's mask. They are neither observations nor gap predictions.
        Holding hidden state does not model elapsed time through a gap.
        A leading gap has no previous prediction: reject before any update.
        """
        if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
            raise ValueError('chunk_size must be a positive integer')
        if len(features) == 0:
            raise ValueError('empty feature sequence')
        supplied = input_valid is not None
        valid = np.ones(len(features), dtype=bool) if not supplied else np.asarray(input_valid)
        if valid.shape != (len(features),) or valid.dtype != np.bool_:
            raise ValueError('input_valid must be one boolean per feature row')
        if not valid[0]:
            raise ValueError('leading invalid input has no previous prediction')
        started = time.monotonic()
        output = {}
        start = 0
        last = None
        while start < len(features):
            if time.monotonic()-started > wall_limit_s:
                raise TimeoutError('IMUCoCo stage wall-time budget reached')
            stop = start+1
            while stop < len(features) and stop-start < chunk_size and valid[stop] == valid[start]:
                stop += 1
            if valid[start]:
                batch = self.forward(features[start:stop])
                last = {key: value[-1:].copy() for key, value in batch.items()}
            else:
                batch = {key: np.repeat(value, stop-start, axis=0) for key, value in last.items()}
            for key, value in batch.items():
                if not np.isfinite(value).all():
                    raise ValueError('nonfinite upstream output: ' + key)
                output.setdefault(key, []).append(value)
            if progress and start % 1200 == 0:
                progress(dict(frame=start, total=len(features), wall_s=time.monotonic()-started))
            start = stop
        return {k: np.concatenate(v) for k, v in output.items()}, dict(
            frames=len(features), wall_s=time.monotonic()-started, sensor_count=5,
            sensor_vertices=self.vertices, joint_to_device_mapping=self.mapping.tolist(),
            recurrence_reset_between_chunks=False, root_translation_evaluated=False,
            validity_mask_supplied=supplied, recurrent_update_frames=int(valid.sum()),
            withheld_invalid_frames=int((~valid).sum()),
            gap_state_policy='hold all recurrent states without update or reset',
            invalid_output_policy='hold last valid prediction; caller validity remains false',
            elapsed_time_during_gap_modeled=False,
            input_orientation_representation='caller-supplied global rotations in first-two-column 6D format',
            placement_applies_orientation_conversion=False,
            contact_head_contract=contact_head_contract())
