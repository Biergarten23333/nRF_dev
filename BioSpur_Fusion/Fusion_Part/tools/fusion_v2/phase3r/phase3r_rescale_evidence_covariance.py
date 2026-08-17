#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,math
from pathlib import Path

SCALE=.15;SQRT=math.sqrt(SCALE);TILT=math.radians(15);JOINT=math.radians(20)
def dump(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def migrate_frame(row):
 row['segment_tilt_sigma_rad']={k:v*SQRT for k,v in row['segment_tilt_sigma_rad'].items()}
 row['joint_relative_sigma_rad']={k:v*SQRT for k,v in row['joint_relative_sigma_rad'].items()}
 row['segment_quality']={k:('USABLE_BODY_RELATIVE_TILT' if v<=TILT else 'DEGRADED_TILT_UNCERTAINTY') for k,v in row['segment_tilt_sigma_rad'].items()}
 row['joint_quality']={k:('USABLE_RELATIVE_ROTATION' if v<=JOINT else 'DEGRADED_RELATIVE_HEADING') for k,v in row['joint_relative_sigma_rad'].items()}
 row['whole_body_available']=all(v.startswith('USABLE') for v in (*row['segment_quality'].values(),*row['joint_quality'].values()))
 row['degraded_reasons']=[] if row['whole_body_available'] else sorted({v for v in (*row['segment_quality'].values(),*row['joint_quality'].values()) if v.startswith('DEGRADED')})
 row['posterior_covariance_scale']=SCALE;return row

def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();master=json.loads((a.evidence/'REAL_ACTION_MASTER_SUMMARY.json').read_text())
 for summary in master['actions']:
  action=summary['action_id'];path=a.evidence/'actions'/action/'production_pose.jsonl';tmp=path.with_suffix('.jsonl.tmp');available=[];segment={};joint={}
  with path.open() as src,tmp.open('w') as dst:
   for line in src:
    row=migrate_frame(json.loads(line));dst.write(json.dumps(row,separators=(',',':'),sort_keys=True)+'\n');available.append(row['whole_body_available'])
    for k,v in row['segment_quality'].items():segment.setdefault(k,[]).append(v.startswith('USABLE'))
    for k,v in row['joint_quality'].items():joint.setdefault(k,[]).append(v.startswith('USABLE'))
  tmp.replace(path);summary['whole_body_availability']=sum(available)/len(available);summary['segment_availability']={k:sum(v)/len(v) for k,v in segment.items()};summary['joint_availability']={k:sum(v)/len(v) for k,v in joint.items()};summary['cross_state_covariance_norm']*=SCALE
  for factor in summary['factor_activation'].values():factor['state_delta_sq']*=SCALE*SCALE
  summary['posterior_covariance_scale']=SCALE;summary['covariance_only_evidence_migration']='EXACT_STATE_INVARIANT_P_NOT_FED_BACK'
  dump(a.evidence/'actions'/action/'SUMMARY.json',summary)
 dump(a.evidence/'REAL_ACTION_MASTER_SUMMARY.json',master)
 manifest={'schema':'biospur-phase3r-evidence-manifest-v2','covariance_only_migration':{'scale':SCALE,'state_invariant':True},'calibration_sha256':sha(a.evidence/'REAL_SENSOR_TO_SEGMENT_AND_QMT_CALIBRATION.json'),'master_summary_sha256':sha(a.evidence/'REAL_ACTION_MASTER_SUMMARY.json'),'action_summary_sha256':{x['action_id']:sha(a.evidence/'actions'/x['action_id']/'SUMMARY.json') for x in master['actions']},'production_pose_sha256':{x['action_id']:sha(a.evidence/'actions'/x['action_id']/'production_pose.jsonl') for x in master['actions']}}
 dump(a.evidence/'EVIDENCE_MANIFEST.json',manifest);print(json.dumps({'actions':len(master['actions']),'scale':SCALE,'manifest':str(a.evidence/'EVIDENCE_MANIFEST.json')}))
if __name__=='__main__':main()
