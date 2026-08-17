from __future__ import annotations
import csv,gzip,hashlib,math
from collections import defaultdict
import numpy as np

def file_sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()

def stream_d1_motion_features(path,expected_sha256):
    if file_sha256(path)!=expected_sha256:raise ValueError("D1 hash mismatch")
    sums=defaultdict(float); counts=defaultdict(int); nodes=set(); actions=set(); rows=0; bytes_streamed=0
    with gzip.open(path,"rt",newline="") as f:
        reader=csv.DictReader(f)
        required={"node","measurement","value_3","value_4","value_5","split_class","selector_name"}
        if not required<=set(reader.fieldnames or ()):raise ValueError("D1 schema")
        for r in reader:
            rows+=1; bytes_streamed+=sum(len(v) for v in r.values() if v is not None)
            if r["split_class"]!="D1" or r["measurement"]!="imu6_raw":raise ValueError("scope violation")
            node=r["node"]; action=r["selector_name"]; g=[float(r[f"value_{i}"]) for i in (3,4,5)]
            mag2=sum(x*x for x in g); sums[(action,node)]+=mag2; counts[(action,node)]+=1;nodes.add(node);actions.add(action)
    feature={a:{n:math.sqrt(sums[(a,n)]/counts[(a,n)]) if counts[(a,n)] else 0.0 for n in sorted(nodes)} for a in sorted(actions)}
    return {"rows":rows,"nodes":sorted(nodes),"actions":sorted(actions),"gyro_rms_raw":feature,"counts":{a:{n:counts[(a,n)] for n in sorted(nodes)} for a in sorted(actions)},
            "access":{"bytes_streamed":bytes_streamed,"headers_parsed":1,"routing_fields_decoded":rows*3,"imu_fields_decoded":rows*3,"uwb_fields_decoded":0,"arrays_materialized":0,"values_analyzed":rows,"initializer_consumption":0,"factor_estimator_consumption":rows}}

ACTION_ROLE_WEIGHTS={
 "arms":{"upper_arm_left":1,"upper_arm_right":1,"forearm_left":.7,"forearm_right":.7,"torso":.15},
 "left_elbow":{"forearm_left":1,"upper_arm_left":.45},"right_elbow_attempt_2":{"forearm_right":1,"upper_arm_right":.45},
 "left_knee":{"thigh_left":1,"shank_left":.55,"pelvis":.15},"right_knee":{"thigh_right":1,"shank_right":.55,"pelvis":.15},
 "left_heel":{"shank_left":1,"thigh_left":.35},"right_heel":{"shank_right":1,"thigh_right":.35},
 "squats":{"pelvis":.8,"thigh_left":.7,"thigh_right":.7,"shank_left":.4,"shank_right":.4,"torso":.25},
 "trunk":{"torso":1,"pelvis":.35}
}

def score_blocks(summary,roles):
    nodes=summary["nodes"]; blocks=[]; names=[]
    for action,weights in ACTION_ROLE_WEIGHTS.items():
        if action not in summary["gyro_rms_raw"]:continue
        x=np.array([summary["gyro_rms_raw"][action][n] for n in nodes],float); sd=x.std(); z=(x-x.mean())/(sd if sd>0 else 1)
        b=np.zeros((len(nodes),len(roles)))
        for j,role in enumerate(roles):b[:,j]=z*weights.get(role,-.08)
        blocks.append(b);names.append(action)
    if len(blocks)<2:raise ValueError("insufficient action blocks")
    return names,np.stack(blocks)

