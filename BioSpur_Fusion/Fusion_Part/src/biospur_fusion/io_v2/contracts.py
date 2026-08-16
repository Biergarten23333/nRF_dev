"""Governance-only semantic helpers; no estimator."""
import pathlib
HARDWARE_IDS=("BSF31CC","BSFC2CC","BSFAA61","BSF1120","BSFB165","BSFEC35","BSF44AD","BSF3C79","BSF6C53","BSF8BC4")
def identity(ids):
 values=list(ids)
 if sorted(values)!=sorted(HARDWARE_IDS) or len(set(values))!=10:raise ValueError('identity')
 return {x:{'hardware_node_id':x,'logical_role':None,'mapping_status':'UNASSIGNED'} for x in sorted(values)}
def map_ns(local,m):return round(float(m['a_ns_per_us'])*float(local)+float(m['b_ns'])) if m['first_timer_us']<=local<=m['last_timer_us'] else None
def widen(prev,value,epoch):
 if not 0<=value<=0xffffffff:raise ValueError('width')
 if prev is not None and value<prev:epoch+=1
 return (epoch<<32)|value,epoch
def seq(prev,value):
 if prev is None:return 'START'
 d=(value-prev)&0xffff;return 'DUPLICATE' if d==0 else 'FORWARD' if d<0x8000 else 'OUT_OF_ORDER'
def fixed_anchor(endpoint):
 if endpoint!='NODE_ANTENNA_TO_FIXED_ANCHOR':raise ValueError('node-to-node')
def projection(fields):
 mandatory={'validity','uncertainty_status','active_gauges','provenance'}
 return 'REFUSED_UNREPRESENTABLE' if not mandatory<=set(fields) else 'EXACT'
