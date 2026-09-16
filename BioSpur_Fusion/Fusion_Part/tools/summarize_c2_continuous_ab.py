#!/usr/bin/env python3
"""Summarize saved continuous A/B without changing or rerunning estimation."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np


def _trajectory_metrics(position: np.ndarray, times: np.ndarray) -> dict:
    if len(times) == 0:
        return {'samples': 0}
    displacement = np.linalg.norm(position - position[0], axis=1)
    speed = np.linalg.norm(np.diff(position, axis=0), axis=1) / np.diff(times)
    return {'samples':len(times), 'first_position_m':position[0].tolist(),
        'last_position_m':position[-1].tolist(),
        'net_displacement_m':float(displacement[-1]),
        'maximum_displacement_from_region_start_m':float(displacement.max()),
        'maximum_speed_mps':float(speed.max()) if len(speed) else None,
        'p95_speed_mps':float(np.percentile(speed,95)) if len(speed) else None}


def summarize(artifact: Path, frontend_result: Path, output: Path,
              reference: Path | None = None) -> dict:
    if output.exists():
        raise FileExistsError(output)
    regions = json.loads(frontend_result.read_text())['regions']
    with np.load(artifact, allow_pickle=False) as a:
        times = a['time_s']
        counts = np.zeros(len(times), dtype=int)
        summaries = []
        for region in regions:
            mask = (times >= region['start_ns']*1e-9) & (times < region['stop_ns']*1e-9)
            counts += mask
            uwb_mask = (a['uwb_time_s'] >= region['start_ns']*1e-9) & (a['uwb_time_s'] < region['stop_ns']*1e-9)
            delta = a['absolute_position_delta_m'][uwb_mask]
            summaries.append({'region_id':region['region_id'], 'kind':region['kind'],
                'a':_trajectory_metrics(a['roots_a'][mask],times[mask]),
                'b_published':_trajectory_metrics(a['roots_b'][mask],times[mask]),
                'b_posterior':_trajectory_metrics(a['roots_b_posterior'][mask],times[mask]),
                'uwb_updates':int(uwb_mask.sum()),
                'uwb_nodes':dict(Counter(map(str,a['uwb_node'][uwb_mask]))),
                'absolute_correction_path_length_m':float(np.linalg.norm(delta,axis=1).sum()),
                'absolute_correction_net_m':delta.sum(axis=0).tolist()})
        result = {'status':'SAVED_OUTPUT_DIAGNOSTICS_NOT_ACCURACY',
            'artifact':str(artifact.resolve()),
            'time_basis':'ABSOLUTE_COMMON_GLOBAL_SECONDS',
            'region_count':len(regions), 'region_kinds':dict(Counter(r['kind'] for r in regions)),
            'unlabelled_samples':int(np.sum(counts==0)), 'multiply_labelled_samples':int(np.sum(counts>1)),
            'unlabelled_policy':'Preserved continuous capture margins outside supplied action labels.',
            'all_regions_have_output':all(r['a']['samples'] > 0 for r in summaries),
            'a':_trajectory_metrics(a['roots_a'],times),
            'b_published':_trajectory_metrics(a['roots_b'],times),
            'b_posterior':_trajectory_metrics(a['roots_b_posterior'],times),
            'b_state_maximum_velocity_mps':float(np.linalg.norm(a['root_state_b'][:,3:6],axis=1).max()),
            'absolute_correction_path_length_m':float(np.linalg.norm(a['absolute_position_delta_m'],axis=1).sum()),
            'absolute_correction_net_m':a['absolute_position_delta_m'].sum(axis=0).tolist(),
            'absolute_correction_maximum_step_m':float(np.linalg.norm(a['absolute_position_delta_m'],axis=1).max()),
            'drift_velocity_correction_path_length_mps':float(np.linalg.norm(a['drift_velocity_delta_mps'],axis=1).sum()),
            'drift_velocity_correction_net_mps':a['drift_velocity_delta_mps'].sum(axis=0).tolist(),
            'publication_withheld_maximum_m':float(np.linalg.norm(a['roots_b_posterior']-a['roots_b'],axis=1).max()),
            'regions':summaries}
        if 'uwb_state_delta' in a.files:
            delta = a['uwb_state_delta']
            covariance = a['root_covariance_diagonal_b']
            state = a['root_state_b']
            result['inertial_feedback'] = {
                'raw_updates':len(delta),
                'nonzero_velocity_updates':int(np.sum(np.linalg.norm(delta[:,3:6],axis=1)>1e-10)),
                'nonzero_accelerometer_bias_updates':int(np.sum(np.linalg.norm(delta[:,6:9],axis=1)>1e-10)),
                'final_accelerometer_bias_mps2':state[-1,6:9].tolist(),
                'maximum_accelerometer_bias_norm_mps2':float(np.linalg.norm(state[:,6:9],axis=1).max()),
                'maximum_velocity_feedback_step_mps':float(np.linalg.norm(delta[:,3:6],axis=1).max()),
                'maximum_bias_feedback_step_mps2':float(np.linalg.norm(delta[:,6:9],axis=1).max()),
                'covariance_diagonal_minimum':float(covariance.min()),
                'covariance_diagonal_all_finite_positive':bool(np.isfinite(covariance).all() and (covariance>0).all()),
                'publication_equals_posterior':bool(np.array_equal(a['roots_b'],a['roots_b_posterior']))}
            node = a['uwb_node']
            nuisance = a['persistent_range_bias_prior_m']
            result['last_recorded_nuisance_priors_m'] = {
                str(name):nuisance[np.flatnonzero(node==name)[-1]].tolist()
                for name in np.unique(node)}
            result['nuisance_note'] = 'Last pre-update external means; not joint root/nuisance posterior or physical calibration proof.'
        if 'selected_valid_count' in a.files:
            def selection_counts(mask: np.ndarray) -> dict:
                original = a['source_valid_mask'][mask]
                ranges = a['source_range_mm'][mask]
                valid = ((original[:,None] & (1 << np.arange(8))) != 0) & (ranges > 0) & (ranges < 65535)
                return {
                    'sweeps':int(mask.sum()),
                    'original_valid_link_count_histogram':dict(Counter(map(int,valid.sum(axis=1)))),
                    'selected_link_count_histogram':dict(Counter(map(int,a['selected_valid_count'][mask]))),
                    'removed_valid_links':int(a['back_removed_count'][mask].sum()),
                    'update_reasons':dict(Counter(map(str,a['uwb_reason'][mask])))}
            result['link_selection'] = selection_counts(np.ones(len(a['uwb_node']),dtype=bool))
            result['link_selection_by_node'] = {
                str(name):selection_counts(a['uwb_node']==name) for name in np.unique(a['uwb_node'])}
            for region, summary in zip(regions,summaries):
                summary['link_selection'] = selection_counts(
                    (a['uwb_time_s'] >= region['start_ns']*1e-9) &
                    (a['uwb_time_s'] < region['stop_ns']*1e-9))
        if reference is not None:
            with np.load(reference,allow_pickle=False) as old:
                raw_ranges, link_epochs = a['raw_range_measured_m'], a['raw_range_link_epoch_s']
                source_basis = 'SOLVER_FACTORS'
                if 'source_range_mm' in a.files:
                    metadata = json.loads(artifact.with_name('RESULT.json').read_text())
                    valid = ((a['source_valid_mask'][:,None] & (1 << np.arange(8))) != 0)
                    valid &= (a['source_range_mm'] > 0) & (a['source_range_mm'] < 65535)
                    raw_ranges = np.where(valid,a['source_range_mm']/1000,np.nan)
                    link_epochs = np.zeros_like(raw_ranges)
                    for name, clock in metadata['clocks'].items():
                        mask = a['uwb_node']==name
                        link_epochs[mask] = (clock['a_ns_per_us']*1e-9 *
                            (a['source_strobe_us'][mask,None]+.5*a['source_t_round_us'][mask]) +
                            clock['b_ns']*1e-9 + float(a['origin_global_s']))
                    link_epochs = np.where(valid,link_epochs,np.nan)
                    source_basis = 'ORIGINAL_UNMASKED_SOURCE_ROWS_AND_MEASURED_T_ROUND'
                result['protected_reference_comparison'] = {
                    'path':str(reference.resolve()),
                    'raw_comparison_basis':source_basis,
                    'same_times':bool(np.array_equal(a['time_s'],old['time_s'])),
                    'same_imu_only_root':bool(np.array_equal(a['roots_a'],old['roots_a'])),
                    'same_relative_body':bool(np.array_equal(a['joints_relative'],old['joints_relative'])),
                    'same_anchors':bool(np.array_equal(a['anchors_world_m'],old['anchors_world_m'])),
                    'same_raw_ranges':bool(np.array_equal(raw_ranges,old['raw_range_measured_m'],equal_nan=True)),
                    'same_raw_link_epochs':bool(np.array_equal(link_epochs,old['raw_range_link_epoch_s'],equal_nan=True))}
    output.write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact',type=Path,required=True)
    parser.add_argument('--frontend-result',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference',type=Path)
    args = parser.parse_args()
    result = summarize(args.artifact,args.frontend_result,args.output,args.reference)
    print(json.dumps({k:v for k,v in result.items() if k!='regions'}))
