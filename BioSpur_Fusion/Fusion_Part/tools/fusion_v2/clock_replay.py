#!/usr/bin/env python3
import argparse,json,pathlib,zipfile
import numpy as np
from access import AccessGate
from common import dump,tool_identity
def hdr(s):
 v=np.lib.format.read_magic(s);return np.lib.format.read_array_header_1_0(s) if v==(1,0) else np.lib.format.read_array_header_2_0(s)
def fld(buf,dtype,name,n):fd,off=dtype.fields[name];return np.ndarray((n,),dtype=fd,buffer=buf,offset=off,strides=(dtype.itemsize,))
def main():
 p=argparse.ArgumentParser();p.add_argument('--allowlist',required=True);p.add_argument('--ledger-log',required=True);p.add_argument('--output',required=True);a=p.parse_args();gate=AccessGate(a.allowlist,a.ledger_log);cfg=json.loads(pathlib.Path(a.allowlist).read_text());ledger,_=gate.authorize(cfg['entries'][3]['path'],'TIME_FIELDS_ONLY','clock_replay.py');side,_=gate.authorize(cfg['entries'][2]['path'],'TIME_ONLY_SIDECAR','clock_replay.py');clock,_=gate.authorize(cfg['entries'][13]['path'],'CLOCK_METADATA','clock_replay.py');models=json.loads(pathlib.Path(clock).read_text())['clock_models'];sc=np.load(side,allow_pickle=False)
 result={'tool':tool_identity(__file__),'ledger':{},'sidecar':{},'max_ledger_difference_ns':0,'max_sidecar_difference_ns':0,'measurement_fields_materialized':False}
 with zipfile.ZipFile(ledger) as z:
  for member in z.namelist():
   kind,node=member[:-4].split('_',1);m=models[node];a1=float(m['a_ns_per_us']);b=float(m['b_ns']);ep=int(m['boot_epoch']);lo=float(m['first_timer_us']);hi=float(m['last_timer_us']);sidearr=sc[f'{kind}_{node}'];pos=total=inside=ldmax=sdmax=disagree=0
   with z.open(member) as s:
    shape,fortran,dtype=hdr(s);remain=shape[0]
    while remain:
     n=min(100000,remain);buf=s.read(n*dtype.itemsize);boot=fld(buf,dtype,'boot_epoch',n).astype(np.int64);timer=fld(buf,dtype,'node_timer_us',n).astype(np.float64);actual=fld(buf,dtype,'global_time_ns',n).astype(np.int64);mask=(boot==ep)&(timer>=lo)&(timer<=hi);pred=np.rint(a1*timer+b).astype(np.int64);d=np.abs(pred[mask]-actual[mask]);ldmax=max(ldmax,int(d.max()) if d.size else 0);inside+=int(mask.sum());total+=n
     if kind=='imu':ss=sidearr[pos:pos+n];pmask=mask;pp=pred;expected=fld(buf,dtype,'raw_record_index',n);pos+=n
     else:
      local=fld(buf,dtype,'strobe_us',n).astype(np.float64)[:,None]+0.5*fld(buf,dtype,'t_round_us',n).astype(np.float64);pmask=((boot[:,None]==ep)&(local>=lo)&(local<=hi)).reshape(-1);pp=np.rint(a1*local+b).astype(np.int64).reshape(-1);ss=sidearr[pos:pos+n*8];expected=np.repeat(fld(buf,dtype,'raw_record_index',n),8);pos+=n*8
     if not np.array_equal(ss['raw_record_index'],expected):raise RuntimeError('record identity')
     disagree+=int(np.count_nonzero((ss['clock_status']==1)!=pmask));dd=np.abs(ss['common_time_ns'][pmask]-pp[pmask]);sdmax=max(sdmax,int(dd.max()) if dd.size else 0);remain-=n
   if pos!=len(sidearr):raise RuntimeError('cardinality')
   result['ledger'][f'{kind}_{node}']={'rows':total,'inside_domain':inside,'max_difference_ns':ldmax};result['sidecar'][f'{kind}_{node}']={'rows':len(sidearr),'exact_domain_status_disagreement':disagree,'max_inside_difference_ns':sdmax};result['max_ledger_difference_ns']=max(result['max_ledger_difference_ns'],ldmax);result['max_sidecar_difference_ns']=max(result['max_sidecar_difference_ns'],sdmax)
 dump(a.output,result);raise SystemExit(not(result['max_ledger_difference_ns']==0 and result['max_sidecar_difference_ns']==0))
if __name__=='__main__':main()
