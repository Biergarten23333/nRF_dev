"""Read a frozen previous five-node result for downstream animation only."""
import json
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_five_calibration.geometry import DISPLAY, joints_from_global
from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL
from biospur_fusion.c2_sparse_nodes.inputs import sha
from c2_five_geometry_review import freeze_five_output_coordinates, five_fk_to_output


class PreviousOutput:
    """Never run old source against new parameters or relabel neural output.

    Both runs originate on the same continuous 20 Hz time grid. Enforce exact
    sample correspondence; a frame-index approximation must fail visibly.
    """
    def __init__(self, source, *, with_h):
        self.source = Path(source).resolve()
        self.bindings = {}
        self.geometry = json.loads(self.read('GEOMETRY.json'))
        self.calibration = self.archive('C2_VALIDATION')
        self.read('FRONTEND.json')
        # Historical output predates the new candidate-bound replay schema.
        # Verify its original file hash, then use its own saved initial-action
        # timestamps to recover pelvis samples. Never invent new producer
        # metadata or run the current calibration loader on an old result.
        prior = self.archive('C2_PRIOR')
        times = self.calibration['00_initial_still/time_s']
        indexes = np.searchsorted(prior['time_s'], times)
        if (np.any(indexes >= len(prior['time_s']))
                or not np.allclose(prior['time_s'][indexes], times, atol=1e-7, rtol=0)):
            raise ValueError('previous initial-pelvis samples do not match its saved action times')
        self.frame = freeze_five_output_coordinates(prior['observed'][indexes, 0], WORLD_TO_SMPL)
        self.holdout = self.archive('H_REPLAY') if with_h else None

    def read(self, name):
        self.bindings[name] = sha(self.source/name)
        return (self.source/name).read_text()

    def archive(self, stage):
        record = json.loads(self.read(stage+'.json'))
        name = stage+'.npz'
        self.bindings[name] = sha(self.source/name)
        if record['output_sha256'] != self.bindings[name]:
            raise ValueError('previous output changed: '+name)
        if stage == 'H_REPLAY':
            for frozen_name, expected in record['frozen_inputs'].items():
                self.bindings[frozen_name] = sha(self.source/frozen_name)
                if self.bindings[frozen_name] != expected:
                    raise ValueError('previous H calibration changed: '+frozen_name)
        with np.load(self.source/name) as archive:
            return {key: archive[key] for key in archive.files}

    def on_grid(self, action, times, window):
        if action.startswith('H'):
            archive = self.holdout
            selected = (archive['time_s'] >= window['lo']) & (archive['time_s'] <= window['hi'])
            old_times, rotation, valid = [archive[key][selected] for key in ('time_s', 'rotation', 'valid')]
        else:
            old_times, rotation, valid = [self.calibration[action+'/'+key] for key in ('time_s', 'rotation', 'valid')]
        if old_times.shape != times.shape or not np.allclose(old_times, times, atol=1e-7, rtol=0):
            raise ValueError('old/new sample times differ; no frame-index substitution: '+action)
        points = joints_from_global(torch.tensor(rotation), self.geometry).numpy()[:, DISPLAY]
        return five_fk_to_output(points, self.frame, source_space='SMPL_FK'), valid

    def provenance(self):
        if any(sha(self.source/name) != expected for name, expected in self.bindings.items()):
            raise ValueError('previous frozen output changed during review')
        return dict(source_run=str(self.source), files_sha256=self.bindings,
            source='frozen physical output, not a recomputation or learned initialization',
            identical_sample_times_verified=True, time_shift_fitted=False,
            output_coordinate_convention=self.frame,
            used_in_current_calibration_or_inference=False)
