#!/usr/bin/env python3
"""Metadata-free O1D synthetic audit and conditional preregistration."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import resource
import time
from typing import Any

import numpy as np

from biospur_fusion.c2_uwb_calibration import rf_shadow_field as rf


ROOT = Path(__file__).resolve().parents[1]
NAMES = list(rf.NUISANCE_NAMES) + list(rf.MORPHOLOGY_PARAMETER_NAMES)


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _skeleton() -> dict[str, np.ndarray]:
    return {
        "pelvis_center": np.array([0.,0.,0.]), "shoulder_mid": np.array([0.,0.,.62]),
        "shoulder_left": np.array([-.22,0.,.60]), "shoulder_right": np.array([.22,0.,.60]),
        "hip_left": np.array([-.12,0.,0.]), "hip_right": np.array([.12,0.,0.]),
        "elbow_left": np.array([-.42,.04,.34]), "elbow_right": np.array([.42,.04,.34]),
        "wrist_left": np.array([-.58,.08,.08]), "wrist_right": np.array([.58,.08,.08]),
        "knee_left": np.array([-.13,.03,-.46]), "knee_right": np.array([.13,.03,-.46]),
        "ankle_left": np.array([-.14,.01,-.91]), "ankle_right": np.array([.14,.01,-.91]),
    }


def _morph(values: list[float] | None = None) -> rf.ShadowMorphology:
    return rf.ShadowMorphology(*(values or [.50,.40,.25,.16,.13,.12,.14]))


def _categorical(edges: list[list[int]]) -> np.ndarray:
    rows = []
    for node, anchor in edges:
        rows.append(np.r_[1., rf._sum_zero_contrast(node,10), rf._sum_zero_contrast(anchor,8)])
    return np.vstack(rows)


def _components(edges: list[list[int]]) -> int:
    adjacency = {index: set() for index in range(18)}
    for node, anchor in edges:
        adjacency[node].add(10+anchor); adjacency[10+anchor].add(node)
    seen = set(); count = 0
    for vertex in adjacency:
        if vertex in seen: continue
        count += 1; stack = [vertex]
        while stack:
            current = stack.pop()
            if current in seen: continue
            seen.add(current); stack.extend(adjacency[current] - seen)
    return count


def _svd(matrix: np.ndarray, names: list[str]) -> dict[str, Any]:
    rms = np.sqrt(np.mean(matrix * matrix, axis=0))
    if np.any(~np.isfinite(rms)) or np.any(rms <= 0):
        return {"pass":False,"reason":"ZERO_OR_NONFINITE_RMS","names":names}
    scaled = matrix / rms
    _, singular, right = np.linalg.svd(scaled, full_matrices=False)
    tolerance = max(scaled.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else math.inf
    return {"pass":rank == len(names) and condition <= 1e4,"names":names,
            "RMS":rms.tolist(),"singular_values":singular.tolist(),
            "smallest_right_singular_vector":right[-1].tolist(),
            "rank_tolerance":float(tolerance),"rank":rank,"condition":condition}


def _articulate(skeleton: dict[str,np.ndarray], block: int) -> None:
    changes = {
        1: (("elbow_left","wrist_left"),(0,.12,.08),("knee_right","ankle_right"),(0,-.08,.05)),
        2: (("elbow_right","wrist_right"),(0,-.14,.10),("knee_left","ankle_left"),(0,.10,-.04)),
        3: (("wrist_left","wrist_right"),(0,.10,.16),("ankle_left","ankle_right"),(0,-.06,.03)),
        4: (("elbow_left","wrist_left"),(.08,-.08,-.06),("elbow_right","wrist_right"),(-.08,.08,.06)),
    }
    if block in changes:
        group1, delta1, group2, delta2 = changes[block]
        for name in group1: skeleton[name] += np.asarray(delta1)
        for name in group2: skeleton[name] += np.asarray(delta2)


def _design(edges: list[list[int]], contract: dict[str,Any]) -> np.ndarray:
    rows=[]; morphology=_morph(); nodes=tuple(rf.EMITTER_LOCAL_SEGMENTS)
    for block in contract["observation_blocks"]:
        rng=np.random.default_rng(block["seed"])
        for node,anchor in edges:
            skeleton=_skeleton(); _articulate(skeleton,block["block"])
            for name in contract["generation"]["landmark_order"]:
                skeleton[name] += rng.normal(0,.04,3)
            tag=np.array([rng.uniform(-1.5,1.5),-2.,rng.uniform(-.8,.8)])
            target=np.array([rng.uniform(-1.5,1.5),2.,rng.uniform(-.8,.8)])
            nuisance=rf.common_nuisance_vector(node_index=node,anchor_index=anchor,
                own_facing_score=float(rng.uniform(-1,1)),predicted_path_length_m=float(rng.uniform(1,9)),
                quality=float(rng.uniform(20,100)),t_round_us=float(rng.uniform(2500,12000)))
            compiled=rf.compile_shadow_geometry(tag_origin_m=tag,anchor_position_m=target,
                landmarks=skeleton,emitter_node=nodes[node])
            def response(candidate:rf.ShadowMorphology)->float:
                feature=rf.shadow_features_from_compiled(compiled,candidate)
                return rf.nested_shadow_shift_m(feature,candidate)["B2"]
            gradient=[]
            for name in rf.MORPHOLOGY_PARAMETER_NAMES:
                value=getattr(morphology,name); step=1e-5*max(1.,abs(value))
                gradient.append((response(replace(morphology,**{name:value+step}))-
                    response(replace(morphology,**{name:value-step})))/(2*step))
            rows.append(np.r_[nuisance,gradient])
    return np.vstack(rows)


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1. / (1. + np.exp(-np.clip(value,-700,700)))


def _point_fields(points:np.ndarray,skeleton:dict[str,np.ndarray],m:rf.ShadowMorphology,
                  local:tuple[str,...])->np.ndarray:
    def segment(start:str,stop:str,scale:float)->np.ndarray:
        a=skeleton[start]; axis=skeleton[stop]-a; length=np.linalg.norm(axis); u=axis/length
        delta=points-a; t=(delta@u)/length; radial=delta-(delta@u)[:,None]*u
        return np.exp(-.5*np.sum(radial*radial,axis=1)/(scale*length)**2)*_sigmoid(12*t)*_sigmoid(12*(1-t))
    def union(fields:list[np.ndarray])->np.ndarray:
        return 1-np.prod(1-np.vstack(fields),axis=0) if fields else np.zeros(len(points))
    if "torso" in local: torso=np.zeros(len(points))
    else:
        pelvis=skeleton["pelvis_center"]; axis=skeleton["shoulder_mid"]-pelvis; length=np.linalg.norm(axis); z=axis/length
        shoulder=skeleton["shoulder_right"]-skeleton["shoulder_left"]; hip=skeleton["hip_right"]-skeleton["hip_left"]
        lateral=.5*(shoulder+hip); lateral-=z*(lateral@z); lateral/=np.linalg.norm(lateral); ap=np.cross(z,lateral); ap/=np.linalg.norm(ap)
        span=.5*(np.linalg.norm(shoulder)+np.linalg.norm(hip)); d=points-pelvis; t=(d@z)/length
        torso=np.exp(-.5*((d@lateral/(m.torso_lateral_scale*span))**2+(d@ap/(m.torso_ap_scale*span))**2))*_sigmoid(12*t)*_sigmoid(12*(1-t))
    arms=union([segment(a,b,m.arm_transverse_scale) for name,a,b in rf.ARM_SEGMENTS if name not in local])
    legs=union([segment(a,b,m.leg_transverse_scale) for name,a,b in rf.LEG_SEGMENTS if name not in local])
    ai=(1-torso)*arms*(1-.5*legs); li=(1-torso)*legs*(1-.5*arms); occ=torso+ai+li
    return np.vstack([torso,arms,legs,ai,li,occ])


def _reference(case:dict[str,Any],m:rf.ShadowMorphology,node:str,panels:int)->np.ndarray:
    x,w=np.polynomial.legendre.leggauss(64); tag=np.asarray(case["tag"]); anchor=np.asarray(case["anchor"])
    fractions=[]; weights=[]
    for p in range(panels):
        lo=p/panels; hi=(p+1)/panels
        fractions.append(.5*(lo+hi)+.5*(hi-lo)*x); weights.append(.5*(hi-lo)*w)
    fraction=np.concatenate(fractions); weight=np.concatenate(weights)
    points=tag+fraction[:,None]*(anchor-tag)
    return _point_fields(points,_skeleton(),m,rf.EMITTER_LOCAL_SEGMENTS[node])@weight


def _synthetic(output:Path,obs:dict[str,Any],grid:dict[str,Any])->int:
    started=time.monotonic(); graph={}; designs={}; failures=[]
    for name,spec in obs["graphs"].items():
        edges=spec["edges"]; cat=_categorical(edges); rank=int(np.linalg.matrix_rank(cat)); components=_components(edges)
        node_deg=[sum(n==i for n,_ in edges) for i in range(10)]; anchor_deg=[sum(a==i for _,a in edges) for i in range(8)]
        graph[name]={"components":components,"categorical_rank":rank,"node_degrees":node_deg,"anchor_degrees":anchor_deg,"unique_edges":len(set(map(tuple,edges)))}
        expected=spec["expected_components"]
        if components!=expected or rank!=spec["expected_categorical_rank"]: failures.append(f"GRAPH_{name}")
        if components!=1: continue
        matrix=_design(edges,obs)
        b0=_svd(matrix[:,:21],NAMES[:21]); b1=_svd(matrix[:,:24],NAMES[:24]); b2=_svd(matrix,NAMES); morph=_svd(matrix[:,21:],NAMES[21:])
        designs[name]={"rows":len(matrix),"B0":b0,"B1":b1,"B2":b2,"morphology":morph}
        if not all(item["pass"] for item in (b0,b1,b2,morph)): failures.append(f"SVD_{name}")
    cases=[]; scalar_names=grid["reported_scalars"]
    geometries=grid["geometry_cases"]; morphologies=grid["morphology_cases"]
    planned=[(g,m,"BSFEC35") for g in geometries for m in morphologies]
    planned += [(next(g for g in geometries if g["name"]=="center_10m"),next(m for m in morphologies if m["name"]=="nominal"),node) for node in grid["emitter_cases"]]
    for geometry,morph_spec,node in planned:
        m=_morph(morph_spec["values"]); candidate=rf.shadow_features(tag_origin_m=np.asarray(geometry["tag"]),anchor_position_m=np.asarray(geometry["anchor"]),landmarks=_skeleton(),emitter_node=node,morphology=m)
        candidate_values=np.asarray([getattr(candidate,name) for name in scalar_names])
        ref256=_reference(geometry,m,node,256); ref512=_reference(geometry,m,node,512)
        conv=np.abs(ref256-ref512); error=np.abs(candidate_values-ref512); gate=2e-8+2e-6*np.abs(ref512)
        conv_gate=.2*gate; passed=(error<=gate)&(conv<=conv_gate)
        if not np.all(passed): failures.append(f"REFERENCE_{geometry['name']}_{morph_spec['name']}_{node}")
        cases.append({"geometry":geometry["name"],"morphology":morph_spec["name"],"node":node,"candidate":candidate_values.tolist(),"reference256":ref256.tolist(),"reference512":ref512.tolist(),"candidate_abs_error":error.tolist(),"reference_convergence":conv.tolist(),"gate":gate.tolist(),"convergence_gate":conv_gate.tolist(),"scalar_pass":passed.tolist()})
    # Cold compile and repeated evaluation microbenchmark, no observation data.
    tag=np.array([0.,-5.,.3]); anchor=np.array([0.,5.,.3]); samples=[]
    for _ in range(64):
        t=time.perf_counter(); compiled=rf.compile_shadow_geometry(tag_origin_m=tag,anchor_position_m=anchor,landmarks=_skeleton(),emitter_node="BSFEC35"); samples.append(time.perf_counter()-t)
    evaluations=[]
    for _ in range(512):
        t=time.perf_counter(); rf.shadow_features_from_compiled(compiled,_morph()); evaluations.append(time.perf_counter()-t)
    compile_p95=float(np.percentile(samples,95)); eval_p95=float(np.percentile(evaluations,95))
    resource={"compile_p95_s":compile_p95,"compile_max_s":max(samples),"eval_p95_s":eval_p95,"eval_max_s":max(evaluations),"compiled_bytes":sum(v.nbytes for v in [compiled.tag_origin_m,compiled.ray_vector_m,*compiled.landmarks.values()]),"projected_slice_rows":40000,"projected_study_rows":380000,"projected_compile_slice_s":40000*compile_p95,"projected_compile_study_s":380000*compile_p95,"projected_500_eval_slice_s":40000*500*eval_p95,"projected_500_eval_study_s":380000*500*eval_p95,"maximum_rss_kib":resource_module()}
    result={"status":"PASS" if not failures else "BLOCKED_SYNTHETIC_GATE","failures":failures,"graphs":graph,"designs":designs,"reference_cases":cases,"resource":resource,"wall_seconds":time.monotonic()-started,"real_data_accessed":False}
    _write(output/"SYNTHETIC_AUDIT.json",result)
    return 0 if not failures else 2


def resource_module()->int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def _preregister(output:Path)->int:
    audit=_read(output/"SYNTHETIC_AUDIT.json")
    if audit.get("status")!="PASS": raise RuntimeError("synthetic audit not PASS")
    # Metadata-only freeze. No source-data discovery or decoding is performed.
    model={"status":"PREREGISTERED_NO_DATA","integration":rf.INTEGRATION_METHOD,"reference_gate":{"atol":2e-8,"rtol":2e-6},"morphology_names":list(rf.MORPHOLOGY_PARAMETER_NAMES),"bounds":rf.MORPHOLOGY_BOUNDS,"nuisance_names":list(rf.NUISANCE_NAMES),"models":{"B0":"common nuisance","B1":"B0+large torso","B2":"B1+non-overlapping small arm/leg increment"},"manual_anthropometry":False,"production":False}
    split={"canonical_actions":["00",*[f"{i:02d}" for i in range(2,20)]],"unavailable":["01"],"mirror_pairs":[["04","05"],["06","07"],["08","09"],["10","11"],["12","13"],["18","19"]],"seed":20260905,"H01_H02":"LOCKED_EXTERNAL_REUSE_PREVIOUSLY_INSPECTED_ZERO_EVALUATION"}
    eligibility={"target_link_exactly_omitted":True,"minimum_remaining_unique_anchors":4,"rank":3,"condition_cap":1e8,"eligible_optimizer_failure":"HARD_FAIL"}
    evaluation={"paired_rows":True,"primary":"held-out predictive log-score","bootstrap":"action and epoch clustered; 2000; seed20260906","gates":["B1-B0 lower95>0","B2-B1 lower95>0","no validation-action sign reversal","calibration and positive-tail improvement","positive-weight information rank3 condition non-regression"]}
    allowlist=["src/biospur_fusion/c2_uwb_calibration/rf_shadow_field.py","tests/test_c2_rf_shadow_field.py","tools/audit_preregister_c2_rf_shadow_field_o1d.py",str(output.relative_to(ROOT))+"/*"]
    for name,value in (("MODEL_CONTRACT.json",model),("SPLIT.json",split),("ELIGIBILITY.json",eligibility),("EVAL_CONTRACT.json",evaluation),("FILE_ALLOWLIST.json",allowlist),("PROVENANCE.json",{"status":"READY_FOR_INDEPENDENT_PREREG_AUDIT","real_data_rows":0}),("INTEGRITY.json",{"candidate_sha256":"4846f127ae4bf77eb4399eed563cd8529c775f2f8f1bea606ca80362f3c3accb","reference_sha256":"f9a473241d00652eb8cc3609bb9cc963690a1ddf81099e0663856242270d601c"})):_write(output/name,value)
    return 0


def main()->int:
    parser=argparse.ArgumentParser(); parser.add_argument("mode",choices=("synthetic","preregister")); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args(); output=args.output.resolve()
    obs=_read(output/"OBSERVATION_BLOCK_CONTRACT.json"); grid=_read(ROOT/"logs/c2_rf_shadow_field_o1c_20260905_183016/REFERENCE_CASE_GRID.json")
    return _synthetic(output,obs,grid) if args.mode=="synthetic" else _preregister(output)


if __name__=="__main__": raise SystemExit(main())
