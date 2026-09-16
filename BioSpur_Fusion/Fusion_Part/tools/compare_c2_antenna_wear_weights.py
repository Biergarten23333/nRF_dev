#!/usr/bin/env python3
"""Saved-output OFF/ON motion and antenna-weight comparison, without truth claims."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def distribution(values):
    values=np.asarray(values).ravel()
    values=values[np.isfinite(values)]
    if not len(values):
        return {'count':0}
    return {'count':len(values),'minimum':float(values.min()),'maximum':float(values.max()),
        'mean':float(values.mean()),'p50':float(np.percentile(values,50)),
        'p95':float(np.percentile(values,95))}


def motion(position,velocity,times):
    if not len(times):
        return {'frames':0}
    steps=np.linalg.norm(np.diff(position,axis=0),axis=1)
    return {'frames':len(times),'net_displacement_m':float(np.linalg.norm(position[-1]-position[0])),
        'maximum_displacement_from_start_m':float(np.linalg.norm(position-position[0],axis=1).max()),
        'position_correction_and_motion_path_m':float(steps.sum()),
        'frame_step_m':distribution(steps),
        'posterior_finite_difference_speed_mps':distribution(steps/np.diff(times)),
        'inertial_state_speed_mps':distribution(np.linalg.norm(velocity,axis=1))}


def compare(off_path,on_path,frontend_result,normals_path,output):
    if output.exists():
        raise FileExistsError(output)
    keys=('time_s','roots_a','roots_b','root_state_b','joints_relative','anchors_world_m',
        'raw_range_measured_m','raw_range_link_epoch_s','uwb_node','uwb_time_s')
    with np.load(off_path,allow_pickle=False) as z:
        off={k:z[k] for k in keys}
    with np.load(on_path,allow_pickle=False) as z:
        on={k:z[k] for k in keys}
        weights=z['antenna_information_weight']
        cosine=z['antenna_facing_cosine']
        evidence=z['antenna_evidence_time_s']
    same={k:bool(np.array_equal(off[k],on[k],equal_nan=True))
        for k in keys if k not in ('roots_b','root_state_b','uwb_node')}
    same['uwb_node']=bool(np.array_equal(off['uwb_node'],on['uwb_node']))
    same['initial_root']=bool(np.array_equal(off['roots_b'][0],on['roots_b'][0]))
    if not all(same.values()):
        raise ValueError(f'comparison input invariants changed: {same}')
    times=on['time_s']
    regions=[]
    for region in json.loads(frontend_result.read_text())['regions']:
        mask=(times>=region['start_ns']*1e-9)&(times<region['stop_ns']*1e-9)
        regions.append({'region_id':region['region_id'],'kind':region['kind'],
            'off':motion(off['roots_b'][mask],off['root_state_b'][mask,3:6],times[mask]),
            'on':motion(on['roots_b'][mask],on['root_state_b'][mask,3:6],times[mask])})
    node_stats={}
    valid_links=np.isfinite(on['raw_range_measured_m'])
    for node in np.unique(on['uwb_node']):
        mask=on['uwb_node']==node
        w,c=weights[mask],cosine[mask]
        valid=valid_links[mask]
        node_stats[str(node)]={'valid_links':distribution(w[valid]),
            'back_cosine_negative':distribution(w[(c<0)&valid]),
            'front_cosine_nonnegative':distribution(w[(c>=0)&valid]),
            'anchor_mean_information':np.nanmean(np.where(valid,w,np.nan),axis=0).tolist()}
    earliest=np.nanmin(on['raw_range_link_epoch_s'],axis=1)
    applied=np.isfinite(evidence)
    with np.load(normals_path,allow_pickle=False) as z:
        index=np.searchsorted(z['time_s'],earliest,side='left')-1
        node_map={str(node):i for i,node in enumerate(z['node_names'])}
        n=np.asarray([node_map[str(node)] for node in on['uwb_node']])
        valid=index>=0
        normal_age=earliest[valid]-z['normal_source_time_s'][index[valid],n[valid]]
    result={'status':'COMPLETE_OFF_ON_MECHANISM_COMPARISON_NOT_ACCURACY',
        'off_source':str(off_path.resolve()),'on_source':str(on_path.resolve()),
        'invariants':same,'region_count':len(regions),
        'all_regions_have_output':all(r['on']['frames']>0 for r in regions),
        'off':motion(off['roots_b'],off['root_state_b'][:,3:6],times),
        'on':motion(on['roots_b'],on['root_state_b'][:,3:6],times),
        'prospective_eight_link_weight_distribution':distribution(weights),
        'valid_link_weight_distribution':distribution(weights[valid_links]),
        'back_weight_distribution':distribution(weights[(cosine<0)&valid_links]),
        'front_weight_distribution':distribution(weights[(cosine>=0)&valid_links]),
        'weight_mapping_exact':bool(np.array_equal(weights[applied],.5+.25*cosine[applied])),
        'applied_updates':int(applied.sum()),'neutral_updates':int((~applied).sum()),
        'strict_pre_link_all':bool(np.all(evidence[applied]<earliest[applied])),
        'evidence_pre_link_margin_s':distribution(earliest[applied]-evidence[applied]),
        'normal_source_age_at_first_link_s':distribution(normal_age),
        'weights_by_node':node_stats,'regions':regions,
        'scope':'ANTENNA_FACING_WEARING_BACK_ONLY; NO_TORSO_RAY; NO_OTHER_LIMB',
        'limitation':'Complete existing information policy changes overall information as well as direction; motion statistics are not error against truth.'}
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('weights_by_node','regions')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('off','on','frontend-result','normals','output'):
        parser.add_argument('--'+field,type=Path,required=True)
    args=parser.parse_args()
    compare(args.off,args.on,args.frontend_result,args.normals,args.output)
