"""Covariance-conditioned relative-chain excitation; no absolute-motion minimum."""
from __future__ import annotations

import math
from typing import Mapping,Any
import numpy as np
from scipy.spatial.transform import Rotation


def relative_excitation(parent_rotation:np.ndarray,child_rotation:np.ndarray,parent_covariance_rad2:np.ndarray,child_covariance_rad2:np.ndarray,contract:Mapping[str,Any])->dict:
    parent=np.asarray(parent_rotation,float);child=np.asarray(child_rotation,float);pc=np.asarray(parent_covariance_rad2,float);cc=np.asarray(child_covariance_rad2,float);finite=np.isfinite(parent).all((1,2))&np.isfinite(child).all((1,2))&np.isfinite(pc).all((1,2))&np.isfinite(cc).all((1,2));parent=parent[finite];child=child[finite];pc=pc[finite];cc=cc[finite]
    minimum=int(contract["decision"]["minimum_valid_samples"])
    if len(parent)<minimum:return {"pass":False,"status":"FAIL_INSUFFICIENT_VALID_SAMPLES","valid_samples":int(len(parent))}
    relative=np.einsum("nji,njk->nik",parent,child);reference=Rotation.from_matrix(relative).mean().as_matrix();rotvec=Rotation.from_matrix(np.einsum("ji,njk->nik",reference,relative)).as_rotvec();_,_,vh=np.linalg.svd(rotvec-np.median(rotvec,axis=0),full_matrices=False);axis=vh[0];along=rotvec@axis;off_axis=rotvec-along[:,None]*axis;excursion=float(np.percentile(np.abs(along),95));incremental_pc=np.abs(pc-pc[0]);incremental_cc=np.abs(cc-cc[0]);incremental_variance=np.trace(incremental_pc+incremental_cc,axis1=1,axis2=2)/3.;floor=math.radians(float(contract["decision"]["minimum_incremental_attitude_sigma_deg"]));measurement_sigma=max(floor,float(np.sqrt(np.median(incremental_variance))));off_norm=np.linalg.norm(off_axis,axis=1);model_mismatch=1.4826*float(np.median(np.abs(off_norm-np.median(off_norm))));combined=float(np.hypot(measurement_sigma,model_mismatch));ratio=excursion/max(combined,1e-12);threshold=float(contract["decision"]["minimum_relative_excursion_to_uncertainty_ratio"]);parent_step=Rotation.from_matrix(np.einsum("nji,njk->nik",parent[:-1],parent[1:])).magnitude();child_step=Rotation.from_matrix(np.einsum("nji,njk->nik",child[:-1],child[1:])).magnitude()
    return {"pass":bool(ratio>=threshold),"status":"PASS_RELATIVE_EXCITATION" if ratio>=threshold else "FAIL_NO_RELATIVE_JOINT_EXCITATION","valid_samples":int(len(parent)),"relative_excursion_p95_rad":excursion,"measurement_sigma_rad":measurement_sigma,"robust_model_mismatch_rad":model_mismatch,"combined_standard_uncertainty_rad":combined,"excursion_to_uncertainty_ratio":ratio,"minimum_ratio":threshold,"proximal_step_rms_rad":float(np.sqrt(np.mean(np.square(parent_step)))) if len(parent_step) else 0.,"distal_step_rms_rad":float(np.sqrt(np.mean(np.square(child_step)))) if len(child_step) else 0.,"uses_absolute_segment_motion_minimum":False}
