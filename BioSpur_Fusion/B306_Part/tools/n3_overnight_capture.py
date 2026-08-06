#!/usr/bin/env python3
"""N3 command-free Fusion/listener capture to fleet depletion."""
from __future__ import annotations
import json, shutil, signal, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from d1_blind_disturbance import SLOTS
from fusion_session import parse_fields, parse_reply, resolve_fusion_port
from listener_array_run import wait_listener_preflight

ROOT=Path(__file__).resolve().parents[2]; COLLECTOR=ROOT/'B306_Part/host/listener_array_collector.py'
def wall():return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')
def dump(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n',encoding='utf-8')
def main():
 root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
 duration_s=float(sys.argv[2]) if len(sys.argv)>2 else 12*3600
 ld=root/'listener_array';clog=(root/'listener.stdout.log').open('x',encoding='utf-8',buffering=1)
 cdc=(root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1)
 proc=subprocess.Popen([sys.executable,str(COLLECTOR),'--out-dir',str(ld),'--duration',str(duration_s+600),
  '--baud','460800','--require-kind','LSTAT'],cwd=ROOT,stdout=clog,stderr=subprocess.STDOUT,text=True)
 out={'status':'STARTING','started_wall':wall(),'events':[],'hour_ticks':[],'terminal':None,
      'commands_sent':[],'configuration_source':'phase_p3 frozen baseline'}
 ch=None;stop=False
 def sig(_s,_f):
  nonlocal stop;stop=True
 signal.signal(signal.SIGINT,sig);signal.signal(signal.SIGTERM,sig)
 try:
  out['listener_preflight']=wait_listener_preflight(ld,proc,30)
  ch=ThreadedLineChannel(resolve_fusion_port(None),cdc,'FUSION',decoded_queue_records=2097152,
   backlog_red_records=262144,raw_backlog_red_bytes=262144,stall_red_s=2)
  ch.transport_mode='binary';ch.text_pending.clear();out['decode_guard']=decode_guard(ch,15)
  opened=time.monotonic();out['window_open']={'monotonic':opened,'wall':wall()};out['status']='RUNNING';dump(root/'state.json',out)
  print(f'=== N3 OVERNIGHT WINDOW OPEN — RUN TO FLEET DEPLETION === mono={opened:.6f} wall={wall()}',flush=True)
  last_u={n:opened for n in SLOTS};last_i={n:opened for n in SLOTS};counts=defaultdict(lambda:defaultdict(int))
  silent=set();pending=[];poll_queue=[];next_stall_read={}
  next_health=opened+2;next_hour=opened+3600;next_poll=opened+300;next_poll_send=opened;hard=opened+duration_s
  while not stop and time.monotonic()<hard:
   now=time.monotonic();line=ch.read(min(now+.5,next_health,next_hour,hard));now=time.monotonic()
   if line:
    f=parse_fields(line);n=f.get('name')
    if n in SLOTS:
     if line.startswith('FUSION_UWB '):last_u[n]=now;counts[n]['uwb']+=1;silent.discard(n);next_stall_read.pop(n,None)
     elif line.startswith('FUSION_IMU '):last_i[n]=now;counts[n]['imu_samples']+=int(f.get('n','0'),0);silent.discard(n);next_stall_read.pop(n,None)
     elif line.startswith('FUSION_QUEUE '):counts[n]['queue']+=1
    reply=parse_reply(line)
    if reply:
     for item in list(pending):
      if item['node']==n and reply.text.startswith(item['prefix']):
       item['reply']=reply.text;item['received_monotonic']=now;out['events'].append(item);pending.remove(item);break
    if line.startswith(('FUSION_CONNECTED ','FUSION_DISCONNECTED ')) or 'TAG_RESET_DETECTED ' in line:
     out['events'].append({'monotonic':now,'wall':wall(),'line':line});dump(root/'state.json',out)
    if line.startswith(('FUSION_STALL_READ_START ','FUSION_STALL_READ ')):
     out['events'].append({'type':'stall_escape_read','monotonic':now,'wall':wall(),'line':line});dump(root/'state.json',out)
   if now>=next_poll:
    for n in SLOTS:
     poll_queue.extend([(n,f'QUEUE PUB HIST={p}',f'QUEUE PUB HIST p={p} ') for p in range(4)])
     poll_queue.extend([(n,'COUNTERS','CTR1 '),(n,'STALL STATUS','STALL '),(n,'STACKS','STACKS ')])
    next_poll+=300
   if poll_queue and now>=next_poll_send:
    n,cmd,prefix=poll_queue.pop(0);ch.send(f'{n} {cmd}')
    pending.append({'type':'bounded_poll','node':n,'command':cmd,'prefix':prefix,'sent_monotonic':now,'deadline':now+8})
    out['commands_sent'].append({'node':n,'command':cmd,'monotonic':now});next_poll_send=now+0.2
   for item in list(pending):
    if now>=item['deadline']:
     item['type']='bounded_poll_timeout';out['events'].append(item);pending.remove(item)
   if now>=next_health:
    h=ch.health_snapshot();free=shutil.disk_usage(root).free
    for n in SLOTS:
     if now-last_u[n]>2 and now-last_i[n]>2 and n not in silent:
      silent.add(n);event={'type':'DATA_PLANE_SILENT','node':n,'monotonic':now,'wall':wall(),'uwb_s':now-last_u[n],'imu_s':now-last_i[n]};out['events'].append(event)
      print(f'DATA_PLANE_SILENT node={n} threshold_s=2.0 uwb_s={event["uwb_s"]:.3f} imu_s={event["imu_s"]:.3f}',flush=True)
      ch.send(f'{n} STALL READ');next_stall_read[n]=now+5
      out['commands_sent'].append({'node':n,'command':'STALL READ','reason':'DATA_PLANE_SILENT','monotonic':now})
    for n in list(silent):
     if now>=next_stall_read.get(n,float('inf')):
      ch.send(f'{n} STALL READ');next_stall_read[n]=now+5
      out['commands_sent'].append({'node':n,'command':'STALL READ','reason':'silent_retry','monotonic':now})
    alive=[n for n in SLOTS if now-max(last_u[n],last_i[n])<=30]
    if not alive:out['terminal']='fleet_depletion';break
    if free<5*1024**3:out['terminal']=f'disk_near_full free={free}';break
    if proc.poll() is not None:out['terminal']=f'listener_down rc={proc.returncode}';break
    if not ch._reader.is_alive() or h['reader_exceptions'] or h['red_markers']:
     out['terminal']=f'fusion_master_down health={h}';break
    out['last_health']={'monotonic':now,'wall':wall(),'alive':alive,'free_bytes':free,'host':h};dump(root/'state.json',out)
    next_health+=2
   if now>=next_hour:
    hour=int((next_hour-opened)//3600);alive=[n for n in SLOTS if next_hour-max(last_u[n],last_i[n])<=30]
    row={'hour':hour,'monotonic':next_hour,'wall':wall(),'alive':alive,'counts':{n:dict(counts[n]) for n in SLOTS}}
    out['hour_ticks'].append(row);dump(root/'state.json',out)
    print(f'N3 T+{hour:02d}:00 — {len(alive)}/10 active — free={shutil.disk_usage(root).free}',flush=True)
    counts=defaultdict(lambda:defaultdict(int));next_hour+=3600
  if stop:out['terminal']='operator_stop'
  elif out['terminal'] is None:out['terminal']='duration_complete'
  out['status']='COMPLETE' if out['terminal'] in ('fleet_depletion','duration_complete') else 'STOPPED'
 except Exception as e:
  out['status']='FAILED';out['terminal']=f'{type(e).__name__}: {e}'
 finally:
  if ch:out['host_health']=ch.health_snapshot();ch.close()
  if proc.poll() is None:proc.send_signal(signal.SIGINT)
  try:out['listener_rc']=proc.wait(timeout=30)
  except subprocess.TimeoutExpired:proc.terminate();out['listener_rc']=proc.wait(timeout=10)
  sp=ld/'summary.json'
  if sp.exists():out['listener_summary']=json.loads(sp.read_text())
  out['window_close']={'monotonic':time.monotonic(),'wall':wall()};dump(root/'state.json',out)
  cdc.close();clog.close();print(f'=== N3 OVERNIGHT WINDOW CLOSED — {out["terminal"]} === wall={wall()}',flush=True)
 return 0 if out['status']=='COMPLETE' else 2
if __name__=='__main__':raise SystemExit(main())
