#!/usr/bin/env python3
"""Deterministic host-only synthetic replay. Never imports serial/BLE modules."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np
from .solver import proper_rotation, solve_assignment

def run(seed=47010):
    rng=np.random.default_rng(seed); slots=[f"slot{i}" for i in range(10)]; nodes=["BSF31CC"]+[f"N{i}" for i in range(9)]
    prototypes={s:rng.normal(size=12) for s in slots}; permutation=rng.permutation(slots[1:])
    truth={nodes[0]:slots[0], **dict(zip(nodes[1:],permutation))}
    features={n:prototypes[s].copy() for n,s in truth.items()}
    result=solve_assignment(features,prototypes,central_node="BSF31CC",central_slot="slot0")
    rotations={}
    for n in nodes:
        q,_=np.linalg.qr(rng.normal(size=(3,3)))
        if np.linalg.det(q)<0:q[:,0]*=-1
        rotations[n]=proper_rotation(np.eye(3),q).round(12).tolist()
    payload={"schema":"biospur-body-calibration-dry-run-v1","seed":seed,"truth":truth,"result":result,
             "extrinsics":rotations,"mvp":{"accelerometer_matrix":"IDENTITY","shared_accelerometer_bias":0,
             "session_gyro_bias":"ESTIMATE_FROM_INITIAL_STILL","device_specific_production_calibration":False},
             "validation_used_for_fit":False}
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":")); payload["result_sha256"]=hashlib.sha256(canonical.encode()).hexdigest()
    return payload

def main():
    p=argparse.ArgumentParser();p.add_argument("--out",type=Path);a=p.parse_args();result=run()
    text=json.dumps(result,indent=2,sort_keys=True)+"\n"
    if a.out:a.out.write_text(text)
    else:print(text,end="")
if __name__=="__main__":main()
