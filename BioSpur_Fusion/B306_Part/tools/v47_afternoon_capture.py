#!/usr/bin/env python3
"""Command-free v47 Fusion/listener wedge and depletion capture."""
from __future__ import annotations

import argparse, hashlib, json, os, shutil, signal, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from listener_array_run import wait_listener_preflight
from v47_guard_evidence import GuardSampler, SCHEMA as GUARD_SCHEMA

ROOT=Path(__file__).resolve().parents[2]
COLLECTOR=ROOT/'B306_Part/host/listener_array_collector.py'
NODES=('BSF3C79','BSFC2CC','BSF44AD','BSF6C53','BSF8BC4','BSF1120','BSF31CC','BSFAA61','BSFB165','BSFEC35')
BATTERY=tuple(n for n in NODES if n!='BSF6C53')
POWER_NOTE=('POWER_ON_CONFIRMED: nine battery boards fully charged and not connected to chargers; '
            'BSF6C53 continuously powered by the fixed adapter.')
POLL_RECEIVERS=frozenset(('LAE','LBF','LDH','LLOW','LMID'))

def wall(): return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')
def atomic(path:Path,obj):
    tmp=path.with_name(path.name+'.tmp'); tmp.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n'); os.replace(tmp,path)
def sha(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def listener_coverage(listener_dir:Path, node_map:dict, since_ns:int):
    wanted={int(v['tag_short_address'],16):n for n,v in node_map.items()}; rows={n:defaultdict(int) for n in node_map}
    first={};last={};errors=defaultdict(int)
    for p in sorted((listener_dir/'listeners').glob('*.jsonl')):
        snr=p.stem
        try:
            for line in p.open(errors='replace'):
                try:r=json.loads(line)
                except Exception: errors[snr]+=1;continue
                if int(r.get('arrival_monotonic_ns',0))<since_ns:continue
                if not r.get('parsed_ok'):
                    errors[snr]+=1;continue
                if r.get('kind')!='LPD':continue
                src=r.get('fields',{}).get('src'); n=wanted.get(src)
                if n:
                    rows[n][snr]+=1;t=int(r['arrival_monotonic_ns']);first[n]=min(first.get(n,t),t);last[n]=max(last.get(n,t),t)
        except OSError: errors[snr]+=1
    return {n:{'listeners':dict(rows[n]),'poll_count':sum(rows[n].values()),
               'first_arrival_monotonic_ns':first.get(n),'last_arrival_monotonic_ns':last.get(n),
               'sufficient':sum(rows[n].values())>0} for n in node_map},dict(errors)

def deduplicated_listener_rates(listener_dir:Path,node_map:dict,start_ns:int,end_ns:int):
    """Source rates: cluster same src/seq observations across five receivers."""
    wanted={int(v['tag_short_address'],16):n for n,v in node_map.items()}
    seen={n:[] for n in node_map}; visibility={n:defaultdict(int) for n in node_map}; errors=0
    for p in sorted((listener_dir/'listeners').glob('*.jsonl')):
        try:
            with p.open(errors='replace') as fh:
                for line in fh:
                    try:r=json.loads(line)
                    except Exception:errors+=1;continue
                    if r.get('listener_key') not in POLL_RECEIVERS or r.get('kind')!='LPD' or not r.get('parsed_ok'):continue
                    t=int(r.get('arrival_monotonic_ns',0))
                    if not start_ns<=t<end_ns:continue
                    f=r.get('fields',{});n=wanted.get(f.get('src'))
                    if n:
                        seen[n].append((t,int(f.get('poll_seq',-1))));visibility[n][r['listener_key']]+=1
        except OSError:errors+=1
    duration=(end_ns-start_ns)/1e9;out={}
    for n,rows in seen.items():
        transmissions=[]
        for t,seq in sorted(rows):
            if not transmissions or seq!=transmissions[-1][1] or t-transmissions[-1][0]>50_000_000:
                transmissions.append((t,seq))
        out[n]={'source_count':len(transmissions),'source_hz':len(transmissions)/duration,
                'per_receiver':dict(visibility[n])}
    return out,errors

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out-dir',required=True,type=Path);ap.add_argument('--hours',type=float,default=8.0)
    ap.add_argument('--duration-seconds',type=float,default=None)
    ap.add_argument('--smoke-seconds',type=float,default=600.0)
    ap.add_argument('--disable-diagnostics',action='store_true')
    ap.add_argument('--diagnostic-ten-minute',action='store_true',help='collect all ten minutes; rate/reset findings do not stop early')
    ap.add_argument('--bsf6c53-uwb-exempt',action='store_true',help='record but do not gate BSF6C53 UWB cadence')
    ap.add_argument('--minimum-uninterruptible-hours',type=float,default=0.0,help='board/event findings cannot auto-stop before this elapsed time')
    ap.add_argument('--exact-duration',action='store_true',help='do not extend capture for late stall candidates')
    ap.add_argument('--precheck',required=True,type=Path);a=ap.parse_args();root=a.out_dir;root.mkdir(parents=True,exist_ok=True)
    if (root/'PROCESS_LEDGER.json').exists():raise SystemExit('refusing existing capture state')
    stop=False
    def sig(_s,_f):
        nonlocal stop;stop=True
    signal.signal(signal.SIGINT,sig);signal.signal(signal.SIGTERM,sig)
    precheck=json.loads(a.precheck.read_text());atomic(root/'PRECHECK.json',precheck)
    if precheck.get('status')!='INVENTORY_PASS':raise SystemExit('precheck did not pass')
    state={'schema':'biospur-v47-afternoon-capture-v1','status':'STARTING','supervisor_pid':os.getpid(),'started_wall':wall(),
           'stop_reason':None,'events':[],'smoke_verdict':None,'precheck':str(a.precheck)};atomic(root/'PROCESS_LEDGER.json',state)
    target_seconds=a.duration_seconds if a.duration_seconds is not None else a.hours*3600
    if a.duration_seconds is None and not a.diagnostic_ten_minute and not 8.0 <= a.hours <= 12.0:raise SystemExit('overnight duration must be 8..12 hours')
    if target_seconds<=0 or not 0<a.smoke_seconds<=target_seconds:raise SystemExit('invalid duration/smoke duration')
    if not 0.0 <= a.minimum_uninterruptible_hours <= a.hours:raise SystemExit('invalid minimum-uninterruptible-hours')
    powers={'battery_nodes':BATTERY,'adapter_nodes':['BSF6C53'],'verbatim_note':POWER_NOTE};atomic(root/'POWER_COHORTS.json',powers)
    free=shutil.disk_usage(root).free
    required=40*1024**3
    atomic(root/'STORAGE_GATE.json',{'available_bytes':free,'required_bytes':required,'status':'PASS' if free>=required else 'BLOCKED'})
    if free<required:raise SystemExit('storage gate blocked')
    listener_dir=root/'listener_capture'; listener_log=(root/'listener_collector.stdout.log').open('x',buffering=1)
    listener_duration=target_seconds+60
    lp=subprocess.Popen([sys.executable,str(COLLECTOR),'--out-dir',str(listener_dir),'--duration',str(listener_duration),
        '--baud','460800','--require-kind','LSTAT','--require-kind','LPD'],cwd=ROOT,stdout=listener_log,stderr=subprocess.STDOUT,text=True)
    state['listener_pid']=lp.pid;atomic(root/'PROCESS_LEDGER.json',state)
    ch=None; fusion_log=(root/'fusion_cdc.log').open('x',buffering=1);fusion_raw=(root/'fusion_host_raw.cobs.bin').open('xb',buffering=0)
    try:
        state['listener_preflight']=wait_listener_preflight(listener_dir,lp,30)
        ch=ThreadedLineChannel(resolve_fusion_port(None),fusion_log,'FUSION',decoded_queue_records=2097152,
            backlog_red_records=262144,raw_backlog_red_bytes=262144,stall_red_s=2,raw_file=fusion_raw)
        ch.transport_mode='binary';ch.text_pending.clear();state['fusion_decode_guard']=decode_guard(ch,15)
        mapping={};counts={n:defaultdict(int) for n in NODES};last={n:{'uwb':None,'imu':None,'telemetry':None} for n in NODES}
        mapping_deadline=time.monotonic()+30
        while len(mapping)<10 and time.monotonic()<mapping_deadline:
            line=ch.read(mapping_deadline)
            if not line:continue
            f=parse_fields(line);n=f.get('name')
            if n in counts and line.startswith('FUSION_UWB '):
                logical=int(f['logical'],0);mapping[n]={'logical_tag_id':logical,'tag_short_address':f'0x{0xB100+logical:04X}'}
        if set(mapping)!=set(NODES):raise RuntimeError(f'node/tag mapping incomplete: {sorted(mapping)}')
        atomic(root/'node_tag_map.json',mapping)
        # Prove Listener coverage before the formal clock starts.  Keep draining
        # decoded Fusion records so this readiness wait cannot create a backlog.
        coverage_since_ns=time.monotonic_ns();coverage_deadline=time.monotonic()+60;coverage={};errs={};next_coverage=0.0
        while time.monotonic()<coverage_deadline:
            now=time.monotonic();ch.read(min(coverage_deadline,now+.05))
            if now>=next_coverage:
                coverage,errs=listener_coverage(listener_dir,mapping,coverage_since_ns);next_coverage=now+1
                if all(v['sufficient'] for v in coverage.values()):break
        atomic(root/'LISTENER_INVENTORY.json',{'coverage':coverage,'parse_errors':errs,'inventory':json.loads((listener_dir/'inventory.json').read_text())})
        if not coverage or not all(v['sufficient'] for v in coverage.values()):raise RuntimeError(f'listener coverage insufficient before T0: {coverage}')
        pre_t0_boundary=ch.discard_pending('formal_t0')
        formal_health_baseline=ch.health_snapshot()
        t0=time.monotonic();t0_ns=time.monotonic_ns();t0_wall=wall();hard=t0+target_seconds
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        relevant=[Path(__file__),ROOT/'B306_Part/tools/async_line_channel.py',ROOT/'B306_Part/tools/fusion_host_binary.py',COLLECTOR]
        dirty_digest=hashlib.sha256(''.join(f'{p.relative_to(ROOT)}:{sha(p)}\n' for p in relevant).encode()).hexdigest()
        manifest={'schema':'biospur-v47-afternoon-manifest-v1','t0_wall':t0_wall,'t0_monotonic':t0,'t0_monotonic_ns':t0_ns,
          'planned_hours':target_seconds/3600,'target_duration_s':target_seconds,'smoke_duration_s':a.smoke_seconds,'minimum_checkpoint_hours':8,'hard_cap_hours':12,'master':'dk-fusion-imu-relay-v36','nodes':NODES,'power_note':POWER_NOTE,
          'bsf6c53_uwb_cadence_exempt':a.bsf6c53_uwb_exempt,
          'minimum_uninterruptible_hours':a.minimum_uninterruptible_hours,
          'exact_duration':a.exact_duration,'pre_t0_boundary':pre_t0_boundary,
          'formal_health_baseline':formal_health_baseline,
          'canonical_marker':'b306-imu-relay-v47','fwid':'f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed',
          'active_image_sha256':'90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98',
          'git_commit':git_commit,'host_relevant_paths_digest':dirty_digest,
          'listener_roles':{'poll_receivers':['LAE','LBF','LDH','LLOW','LMID'],'LCG':'SLAVED beacon subscriber','LHIGH':'MAIN beacon transmitter'},
          'power_groups':{'adapter':['BSF6C53'],'battery':list(BATTERY)},
          'bsf6c53_exemption_scope':'Listener absolute reception rate only',
          'schemas':{'binary':'biospur-host-frame-v1 COBS+CRC16/CCITT-FALSE','decoded_text':'full-uwb-eight-slot-v1','raw_tee':'exact serial bytes with COBS delimiters'},
          'authorization':{'allowed':['read-only PING','STATUS','inventory','host collection'],'forbidden':['OTA','upload','pending','PREPARE/COMMIT','reboot','power cycle','charging','configuration','SWD','J-Link','RTT','firmware flash']},
          'reset_intents_verified_from_source':{'1':'BSF_RESET_INTENT_RECOVERY_GUARD','5':'BSF_RESET_INTENT_STALL_RECOVERY'},
          'commands_sent':[],'no_configuration_mutation':True,'script_sha256':sha(Path(__file__)),'listener_script_sha256':sha(COLLECTOR)}
        atomic(root/'RUN_MANIFEST.json',manifest);state.update({'status':'CAPTURE_RUNNING_SMOKE_ACTIVE','t0_wall':t0_wall,'t0_monotonic':t0});atomic(root/'PROCESS_LEDGER.json',state)
        guard=GuardSampler(NODES,root/'guard_evidence.jsonl')
        if not a.disable_diagnostics:guard.start('t0_baseline',t0)
        manifest['guard_evidence']={'schema':GUARD_SCHEMA,'path':'guard_evidence.jsonl',
          'periodic_polling':False,'disabled_for_run':a.disable_diagnostics,'policy':['t0_baseline','host_anomaly','best_effort_final'],
          'command_allowlist':['V45 GUARD'],'append_only':True}
        atomic(root/'RUN_MANIFEST.json',manifest)
        print(f'CAPTURE_RUNNING_SMOKE_ACTIVE T0_wall={t0_wall} T0_monotonic={t0:.6f}',flush=True)
        peer={n:{'connected':True,'subscribed':True} for n in NODES};last_node_ms={};minute_base={n:dict(counts[n]) for n in NODES}
        integrity={n:{'uwb_full':0,'uwb_bad':0,'imu_ok':0,'imu_bad':0} for n in NODES}
        smoke_minutes=[];next_minute=t0+60
        next_health=time.monotonic()+2;next_checkpoint=time.monotonic()+30;candidates=[];smoke_counts=None;extension=0.0
        while not stop and time.monotonic()<hard+extension:
            now=time.monotonic();line=ch.read(min(now+.5,next_health,hard+extension));now=time.monotonic()
            if line:
                guard.on_line(line)
                f=parse_fields(line);n=f.get('name')
                if n in counts:
                    if line.startswith('FUSION_UWB '):
                        counts[n]['uwb']+=1;last[n]['uwb']=now
                        full=all(len(f.get(k,'').split(','))==8 for k in ('anchor_id','rank','range_mm','t_round_us','quality','cfo_ppm_q8')) and all(k in f for k in ('sweep','poll_tx','identity','logical','guard_us','spacing_us','valid_mask','flags'))
                        integrity[n]['uwb_full' if full else 'uwb_bad']+=1
                    elif line.startswith('FUSION_IMU '):
                        count=int(f.get('n','0'),0);counts[n]['imu']+=count;last[n]['imu']=now
                        tuples=f.get('samples','').split(';');ok=len(tuples)==count and all(len(x.split(','))==7 for x in tuples)
                        integrity[n]['imu_ok' if ok else 'imu_bad']+=1
                    elif line.startswith('FUSION_TELEMETRY '):counts[n]['telemetry']+=1;last[n]['telemetry']=now
                    elif line.startswith('FUSION_QOS '):counts[n]['qos']+=1
                    elif line.startswith('FUSION_CONNECTED '):peer[n]['connected']=True
                    elif line.startswith('FUSION_DISCONNECTED '):peer[n]['connected']=False
                    elif line.startswith('FUSION_DATA_SUBSCRIBED '):peer[n]['subscribed']=True
                    elif line.startswith('FUSION_DATA_UNSUBSCRIBED '):peer[n]['subscribed']=False
                    if line.startswith('FUSION_TELEMETRY '):
                        ms=int(f.get('node_ms','-1'),0);prev=last_node_ms.get(n)
                        if prev is not None and ms<prev:state['events'].append({'type':'UPTIME_RESET','node':n,'before':prev,'after':ms,'monotonic':now})
                        last_node_ms[n]=ms
                if line.startswith(('FUSION_CONNECTED ','FUSION_DISCONNECTED ')) or 'TAG_RESET_DETECTED ' in line or 'RECOVERY' in line:
                    state['events'].append({'monotonic':now,'wall':wall(),'line':line})
            if now>=next_health:
                for n in NODES:
                    u=last[n]['uwb'];i=last[n]['imu']
                    if u and i and now-max(u,i)>=2 and not any(x.get('node')==n and x.get('open') for x in candidates):
                        lower=max(u,i);x={'node':n,'open':True,'provisional_monotonic':now,'onset_lower':lower,'onset_upper':lower+.120,'wall':wall()};candidates.append(x);state['events'].append({'type':'JOINT_STALL_PROVISIONAL',**x})
                        if not a.disable_diagnostics:guard.start('host_anomaly',now)
                    for x in candidates:
                        if x['node']==n and x.get('open') and u and i and max(u,i)>x['provisional_monotonic']:
                            x['open']=False;x['recovered_monotonic']=max(u,i);x['duration_s']=max(u,i)-x['onset_lower']
                if lp.poll() is not None:state['stop_reason']='IRRECOVERABLE_EVIDENCE_FAILURE';break
                h=ch.health_snapshot()
                if not ch._reader.is_alive() or h['reader_exceptions'] or h['red_markers']:
                    state['stop_reason']='IRRECOVERABLE_EVIDENCE_FAILURE';break
                if shutil.disk_usage(root).free<8*1024**3:state['stop_reason']='IRRECOVERABLE_EVIDENCE_FAILURE';break
                next_health=now+2
            guard.tick(ch.send,now)
            if state['smoke_verdict'] is None and now>=next_minute:
                idx=len(smoke_minutes)+1;rates={};fail=[]
                for n in NODES:
                    du=counts[n]['uwb']-minute_base[n].get('uwb',0);di=counts[n]['imu']-minute_base[n].get('imu',0)
                    rates[n]={'uwb_hz':du/60,'imu_hz':di/60,'peer':dict(peer[n])}
                    uwb_bad=not 8.0<=du/60<=8.6
                    if (uwb_bad and not (n=='BSF6C53' and a.bsf6c53_uwb_exempt)) or not 195<=di/60<=205:fail.append(f'{n}:fusion_rate')
                    if not all(peer[n].values()):fail.append(f'{n}:peer_state')
                    minute_base[n]=dict(counts[n])
                air,air_errors=deduplicated_listener_rates(listener_dir,mapping,t0_ns+(idx-1)*60_000_000_000,t0_ns+idx*60_000_000_000)
                for n,row in air.items():
                    if not 8.0<=row['source_hz']<=8.6:
                        row['classification']='BSF6C53_METAL_PLATE_EXEMPT' if n=='BSF6C53' and a.bsf6c53_uwb_exempt else ('RF_OR_RECEIVER_VISIBILITY' if 8.0<=rates[n]['uwb_hz']<=8.6 else 'SOURCE_CADENCE_LOW')
                        if row['classification']=='SOURCE_CADENCE_LOW':fail.append(f'{n}:listener_rate')
                if air_errors:fail.append('listener_parse_or_read_error')
                hs=ch.health_snapshot()
                cumulative=('raw_queue_drops','decoded_queue_drops','log_queue_drops','frame_crc_decode_errors','payload_decode_errors','red_markers','reader_exceptions')
                if any(hs.get(k,0)-formal_health_baseline.get(k,0) for k in cumulative):fail.append('host_loss_or_decode_error')
                for n in NODES:
                    if integrity[n]['uwb_full']==0 or integrity[n]['uwb_bad'] or integrity[n]['imu_ok']==0 or integrity[n]['imu_bad']:fail.append(f'{n}:field_integrity')
                if any(x.get('open') or x.get('duration_s',0)>=2 for x in candidates):fail.append('joint_silence')
                if any(x.get('type')=='UPTIME_RESET' or
                       ('RECOVERY' in x.get('line','') and 'TAG_RESET_' not in x.get('line',''))
                       for x in state['events']):fail.append('reset_or_recovery')
                row={'minute':idx,'window_end_monotonic':now,'fusion':rates,'listener':air,'failures':sorted(set(fail)),'pass':not fail}
                smoke_minutes.append(row);atomic(root/'SMOKE_MINUTE_STATUS.json',{'minutes':smoke_minutes})
                if fail and not a.diagnostic_ten_minute and now>=t0+a.minimum_uninterruptible_hours*3600:
                    state['smoke_verdict']='BLOCKED_SMOKE';state['stop_reason']='BLOCKED_SMOKE'
                    atomic(root/'SMOKE_RESULT.json',{'verdict':'BLOCKED_SMOKE','minutes':smoke_minutes});break
                next_minute=t0+(idx+1)*60
            if state['smoke_verdict'] is None and now>=t0+a.smoke_seconds:
                smoke_counts={n:dict(counts[n]) for n in NODES};infra_ok=lp.poll() is None and ch._reader.is_alive() and all(v['sufficient'] for v in coverage.values())
                if a.diagnostic_ten_minute:
                    state['smoke_verdict']='DIAGNOSTIC_COMPLETE' if infra_ok and len(smoke_minutes)==10 else 'DIAGNOSTIC_INFRASTRUCTURE_FAILURE'
                elif a.minimum_uninterruptible_hours>0 and infra_ok:
                    state['smoke_verdict']='SMOKE_RECORDED_MINIMUM_CAPTURE_CONTINUES'
                else:
                    state['smoke_verdict']='SMOKE_PASS' if infra_ok and len(smoke_minutes)==10 and all(x['pass'] for x in smoke_minutes) else 'BLOCKED_SMOKE'
                atomic(root/'SMOKE_RESULT.json',{'verdict':state['smoke_verdict'],'evaluated_wall':wall(),'counts':smoke_counts,'minutes':smoke_minutes,'events':state['events'],'collectors_alive':infra_ok})
                print(state['smoke_verdict'],flush=True)
                if a.diagnostic_ten_minute:
                    state['stop_reason']='DIAGNOSTIC_TEN_MINUTES_COMPLETE' if state['smoke_verdict']=='DIAGNOSTIC_COMPLETE' else 'IRRECOVERABLE_EVIDENCE_FAILURE';break
                if state['smoke_verdict']=='SMOKE_RECORDED_MINIMUM_CAPTURE_CONTINUES':
                    state['status']='CAPTURE_RUNNING_MINIMUM_UNINTERRUPTIBLE';continue
                if state['smoke_verdict']!='SMOKE_PASS':state['stop_reason']='BLOCKED_SMOKE';break
                state['status']='CAPTURE_RUNNING_OVERNIGHT'
            if now>=next_checkpoint:
                state['last_health']={'wall':wall(),'monotonic':now,'free_bytes':shutil.disk_usage(root).free,'fusion':ch.health_snapshot(),'listener_alive':lp.poll() is None,'counts':{n:dict(counts[n]) for n in NODES}}
                atomic(root/'JOINT_STALL_CANDIDATES.json',candidates);atomic(root/'PROCESS_LEDGER.json',state)
                hp=root/'HOST_HEALTH_MINUTE.json';history=json.loads(hp.read_text()) if hp.exists() else []
                history.append(state['last_health']);atomic(hp,history);next_checkpoint=now+60
            if now>=hard and extension==0:
                if a.exact_duration:break
                late=[x for x in candidates if x['onset_lower']>=hard-600]
                extension=min(600,max((x['onset_lower']+600-hard for x in late),default=0));state['tail_extension_s']=extension
                if extension:continue
                break
        if stop:state['stop_reason']='OPERATOR_STOP'
        elif state['stop_reason'] is None:state['stop_reason']='PLANNED_DURATION_COMPLETE'
        state['status']='CAPTURE_COMPLETE';state['ended_wall']=wall();state['ended_monotonic']=time.monotonic();state['counts']={n:dict(counts[n]) for n in NODES};atomic(root/'JOINT_STALL_CANDIDATES.json',candidates)
        # Raw collectors remain alive while this bounded, best-effort final
        # snapshot runs. A timeout is evidence, never a capture failure.
        if not a.disable_diagnostics and not guard.active:guard.start('best_effort_final')
        final_deadline=time.monotonic()+15
        while guard.active and time.monotonic()<final_deadline:
            now=time.monotonic();guard.tick(ch.send,now);line=ch.read(min(final_deadline,now+.1))
            if line:guard.on_line(line)
    except Exception as e:
        state['status']='BLOCKED_EVIDENCE_FAILURE';state['stop_reason']='IRRECOVERABLE_EVIDENCE_FAILURE';state['error']=f'{type(e).__name__}: {e}'
    finally:
        if ch:
            state['decoded_close_drain']=ch.quiesce_reader_and_drain('normal_close_drain')
            ch.close();state['fusion_health_final']=ch.health_snapshot()
        if lp.poll() is None:lp.send_signal(signal.SIGINT)
        try:state['listener_rc']=lp.wait(timeout=30)
        except subprocess.TimeoutExpired:lp.terminate();state['listener_rc']=lp.wait(timeout=10)
        fusion_raw.close();fusion_log.close();listener_log.close();state['finalized_wall']=wall();atomic(root/'PROCESS_LEDGER.json',state)
    return 0 if state['status']=='CAPTURE_COMPLETE' else 2
if __name__=='__main__':raise SystemExit(main())
