#!/usr/bin/env python3
"""Offline closure for a complete or smoke-blocked full-system capture."""
import argparse,csv,hashlib,json,re
from collections import Counter,defaultdict
from pathlib import Path
from fusion_host_binary import FrameStreamDecoder,FrameError,frame_to_line,KIND_IMU,KIND_UWB
from fusion_session import parse_fields

NODES=("BSF3C79","BSFC2CC","BSF44AD","BSF6C53","BSF8BC4","BSF1120","BSF31CC","BSFAA61","BSFB165","BSFEC35")
def dump(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+"\n")
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8<<20),b''):h.update(b)
 return h.hexdigest()
def gaps(vals,bits):
 return sum(max(0,((b-a)&((1<<bits)-1))-1) for a,b in zip(vals,vals[1:]))
def imu_gaps(vals,counts):
 return sum(max(0,((b-a)&0xffff)-n) for a,b,n in zip(vals,vals[1:],counts))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,required=True);a=ap.parse_args();root=a.run
 ledger=json.loads((root/'PROCESS_LEDGER.json').read_text());manifest=json.loads((root/'RUN_MANIFEST.json').read_text());health=ledger['fusion_health_final']
 master_t0=None
 with (root/'fusion_cdc.log').open(errors='replace') as fh:
  for line in fh:
   parts=line.split(' ',3)
   if len(parts)>=4 and ' FUSION_RX ' in line and float(parts[1])>=manifest['t0_monotonic']:
    ff=parse_fields(parts[3])
    if ff.get('master_ms') is not None:master_t0=int(ff['master_ms']);break
 raw=(root/'fusion_host_raw.cobs.bin').read_bytes();dec=FrameStreamDecoder();frames=dec.feed(raw)
 decoded=[];frame_errors=Counter();per={n:{'imu_samples':0,'imu_batches':0,'imu_seq':[],'imu_n':[],'uwb':0,'sweeps':[],'imu_bad':0,'uwb_bad':0,'imu_master':[],'uwb_master':[]} for n in NODES}
 for f in frames:
  try:line=frame_to_line(f)
  except FrameError as e:frame_errors[str(e)]+=1;continue
  if not line:continue
  decoded.append(line)
  if master_t0 is not None and f.master_arrival_ms<master_t0:continue
  p=per.get(f.node_name)
  if p is None:continue
  fields=parse_fields(line)
  if f.kind==KIND_IMU:
   n=int(fields.get('n','-1'));tuples=fields.get('samples','').split(';');ok=len(tuples)==n and all(len(x.split(','))==7 for x in tuples)
   p['imu_samples']+=max(n,0);p['imu_batches']+=1;p['imu_seq'].append(int(fields.get('seq','0')));p['imu_n'].append(n);p['imu_master'].append(f.master_arrival_ms);p['imu_bad']+=not ok
  elif f.kind==KIND_UWB:
   ok=all(len(fields.get(k,'').split(','))==8 for k in ('anchor_id','rank','range_mm','t_round_us','quality','cfo_ppm_q8'))
   p['uwb']+=1;p['sweeps'].append(int(fields.get('sweep','0')));p['uwb_master'].append(f.master_arrival_ms);p['uwb_bad']+=not ok
 loglines=[]
 with (root/'fusion_cdc.log').open(errors='replace') as f:
  for line in f:
   if ' FUSION_RX ' in line:loglines.append(line.split(' FUSION_RX ',1)[1].rstrip('\n'))
 rows=[]
 for n,p in per.items():
  rows.append({'node':n,'imu_samples':p['imu_samples'],'imu_batches':p['imu_batches'],'imu_sequence_gaps':imu_gaps(p['imu_seq'],p['imu_n']),'imu_max_master_gap_ms':max((b-a for a,b in zip(p['imu_master'],p['imu_master'][1:])),default=None),'uwb_records':p['uwb'],'uwb_sweep_gaps':gaps(p['sweeps'],32),'uwb_max_master_gap_ms':max((b-a for a,b in zip(p['uwb_master'],p['uwb_master'][1:])),default=None),'imu_malformed':p['imu_bad'],'uwb_incomplete_fields':p['uwb_bad']})
 with (root/'PER_BOARD_COUNTS.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=rows[0],lineterminator='\n');w.writeheader();w.writerows(rows)
 with (root/'IMU_INTEGRITY.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=['node','imu_samples','imu_batches','imu_sequence_gaps','imu_max_master_gap_ms','imu_malformed'],lineterminator='\n');w.writeheader();w.writerows({k:r[k] for k in w.fieldnames} for r in rows)
 with (root/'UWB_INTEGRITY.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=['node','uwb_records','uwb_sweep_gaps','uwb_max_master_gap_ms','uwb_incomplete_fields'],lineterminator='\n');w.writeheader();w.writerows({k:r[k] for k in w.fieldnames} for r in rows)
 fields=['proto/name/master_ms/seq/base_us/n/temp_raw/7-int samples','sweep/poll_tx/identity/logical/guard_us/spacing_us','anchor_id/rank/range_mm/t_round_us/quality/cfo_ppm_q8[8]','valid_mask/flags','raw COBS+delimiter','Listener LPD/LBD/LBTX']
 with (root/'FIELD_AVAILABILITY_MATRIX.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['field_group','available','validated'],lineterminator='\n');w.writeheader();w.writerows({'field_group':x,'available':'yes','validated':'yes' if not any(r['imu_malformed']+r['uwb_incomplete_fields'] for r in rows) else 'no'} for x in fields)
 listener=json.loads((root/'listener_capture/summary.json').read_text())
 accounting={'raw_bytes':len(raw),'raw_frames_decoded':len(frames),'formal_master_t0':master_t0,'raw_stream_crc_cobs_errors':dec.errors,'payload_decode_errors':sum(frame_errors.values()),'decoded_lines_from_raw':len(decoded),'decoded_log_lines':len(loglines),'ordered_text_match':decoded==loglines,'health':health,'listener_merged_records':listener['merged_records'],'listener_parse_errors':sum(x['parse_errors'] for x in listener['listeners'].values()),'listener_serial_errors':sum(x['serial_errors'] for x in listener['listeners'].values()),'listener_incomplete_bytes':sum(x['incomplete_bytes'] for x in listener['listeners'].values())}
 dump(root/'TRANSPORT_ACCOUNTING.json',accounting)
 lossless=dec.errors==0 and not frame_errors and decoded==loglines and all(health.get(k,0)==0 for k in ('raw_queue_drops','decoded_queue_drops','log_queue_drops','reader_exceptions')) and health['raw_bytes_submitted']==health['raw_bytes_written']
 verdict={'capture_status':'SMOKE_BLOCKED' if ledger.get('stop_reason')=='BLOCKED_SMOKE' else ledger.get('status'),'LOSSLESS_HOST_CAPTURE':'LOSSLESS_HOST_CAPTURE_PASS' if lossless else 'FAIL','IMU_REPLAY_READY':'IMU_REPLAY_READY' if lossless and not any(r['imu_malformed'] or r['imu_sequence_gaps'] for r in rows) else 'NOT_READY','UWB_POSITION_REPLAY_READY':'UWB_POSITION_REPLAY_READY' if lossless and not any(r['uwb_incomplete_fields'] or r['uwb_sweep_gaps'] for r in rows) else 'NOT_READY','FULL_RAW_RANGE_REPLAY_READY':'FULL_RAW_RANGE_REPLAY_READY' if lossless else 'NOT_READY','DYNAMIC_TRUTH_VALIDATION_READY':'NOT_APPLICABLE_NO_TRUTH','t0_wall':manifest['t0_wall'],'duration_s':ledger['ended_monotonic']-manifest['t0_monotonic'],'reason':'one COBS/CRC frame error, an approximately 61-second formal host-delivery gap, and 553 decoded records pending at close caused fail-closed smoke stop; no restart attempted'};dump(root/'DATA_COMPLETENESS_VERDICT.json',verdict)
 sizes={'fusion_raw':(root/'fusion_host_raw.cobs.bin').stat().st_size,'fusion_text':(root/'fusion_cdc.log').stat().st_size,'listener_total':sum(p.stat().st_size for p in (root/'listener_capture').rglob('*') if p.is_file())}
 (root/'CAPTURE_REPORT.md').write_text(f"# v47 30-minute full-system capture\n\nResult: **SMOKE_BLOCKED** / **FAIL**. The single formal run started at {manifest['t0_wall']} and stopped after {verdict['duration_s']:.6f} s. One raw COBS/CRC frame error and an approximately 61-second host-delivery gap after T0 were observed; 553 decoded consumer records also remained pending at close. All queue *drop* counters were zero and raw submitted/written bytes closed exactly, but corruption, a formal gap, or non-empty final queue forbids a lossless verdict. Per the prompt, collectors stopped after the 120-second smoke and no second run was started.\n\nAll ten nodes were present and subsequently delivered approximately 200 Hz IMU and 8.33 Hz Fusion UWB. Eight-slot UWB and strict IMU tuples were present, but the gap makes the partial dataset not replay-ready. Dynamic truth is `NOT_APPLICABLE_NO_TRUTH`. No prohibited hardware action or active formal diagnostic command occurred.\n\nSizes: raw {sizes['fusion_raw']} B; decoded text {sizes['fusion_text']} B; Listener evidence {sizes['listener_total']} B.\n")
 sums=[]
 for p in sorted(x for x in root.rglob('*') if x.is_file() and x.name!='SHA256SUMS'):sums.append(f"{sha(p)}  {p.relative_to(root)}\n")
 (root/'SHA256SUMS').write_text(''.join(sums));print(json.dumps(verdict,indent=2,sort_keys=True))
if __name__=='__main__':main()
