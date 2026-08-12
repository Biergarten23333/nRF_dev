#!/usr/bin/env python3
"""Deterministic offline trajectory plots for the v47 dual-node overnight run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "biospur-v47-dual-trajectory-plots-v1"
import matplotlib.pyplot as plt
from matplotlib import colors
from matplotlib.cm import ScalarMappable
import numpy as np

import analyze_v47_dual_rotation_overnight as overnight


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "B306_Part/logs/v47_c2cc_3c79_9rpm_overnight_20260812_013304"
SOURCE_ANALYSIS = RUN / "analysis_dual_rotation_overnight_v1"
REPAIR_ANALYSIS = RUN / "analysis_q1_covariance_repair_v1"
DEFAULT_OUT = RUN / "analysis_dual_rotation_trajectory_plots_v1"
RAW = RUN / "attempt2_continuous/fusion_host_raw.cobs.bin"
EXPECTED_RAW_SHA = "e9cad96e432f27e61a3a88105cf68e725ee398ba5743490a413f24a4ca7802ec"
NOMINAL_S = 7.283928561 * 3600.0
NODES = ("BSF3C79", "BSFC2CC")
NODE_COLORS = {"BSF3C79": "#0072B2", "BSFC2CC": "#D55E00"}
FIGURES = (
    "BSF3C79_OVERNIGHT_TRAJECTORY.svg", "BSF3C79_OVERNIGHT_TRAJECTORY.png",
    "BSFC2CC_OVERNIGHT_TRAJECTORY.svg", "BSFC2CC_OVERNIGHT_TRAJECTORY.png",
    "DUAL_NODE_OVERNIGHT_TRAJECTORIES.svg", "DUAL_NODE_OVERNIGHT_TRAJECTORIES.png",
)
CORE = FIGURES + ("PLOT_METHOD.md", "PLOT_METRICS.csv", "PLOT_MANIFEST.json")


def sha(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(8<<20),b""):digest.update(block)
    return digest.hexdigest()


def clean(value):
    if isinstance(value,np.generic):value=value.item()
    if isinstance(value,np.ndarray):return [clean(x) for x in value.tolist()]
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(x) for x in value]
    if isinstance(value,float):return None if not math.isfinite(value) else float(f"{value:.12g}")
    return value


def write_json(path:Path,value)->None:
    path.write_text(json.dumps(clean(value),indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")


def write_csv(path:Path,rows:list[dict])->None:
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore",lineterminator="\n")
        writer.writeheader()
        for row in rows:writer.writerow({k:"" if clean(row.get(k)) is None else clean(row.get(k)) for k in fields})


def deterministic_basis(normal:np.ndarray)->tuple[np.ndarray,np.ndarray,np.ndarray]:
    n=np.asarray(normal,float);n/=np.linalg.norm(n)
    if n[2]<0:n=-n
    reference=np.array([1.,0.,0.]);u=reference-n*float(reference@n)
    if np.linalg.norm(u)<.2:
        reference=np.array([0.,1.,0.]);u=reference-n*float(reference@n)
    u/=np.linalg.norm(u);v=np.cross(n,u);v/=np.linalg.norm(v)
    return u,v,n


def robust_orbit_fit(time_s:np.ndarray,xyz_mm:np.ndarray)->dict:
    """Frozen fit, extended with deterministic basis and full-data residuals."""
    valid=np.isfinite(xyz_mm).all(axis=1);t=np.asarray(time_s[valid],float);xyz=np.asarray(xyz_mm[valid],float)
    if len(xyz)<100:return {"status":"INSUFFICIENT","valid_points":len(xyz)}
    center0=np.median(xyz,axis=0);dist=np.linalg.norm(xyz-center0,axis=1);keep=dist<=np.quantile(dist,.99)
    fit_xyz=xyz[keep];fit_t=t[keep];mean=np.mean(fit_xyz,axis=0);cov=np.cov((fit_xyz-mean).T)
    eig,vec=np.linalg.eigh(cov);plane_basis=vec[:,[2,1]];fit_uv=(fit_xyz-mean)@plane_basis
    A=np.c_[2*fit_uv[:,0],2*fit_uv[:,1],np.ones(len(fit_uv))];b=np.sum(fit_uv*fit_uv,axis=1)
    solution=np.linalg.lstsq(A,b,rcond=None)[0];circle_center=solution[:2]
    radius=math.sqrt(max(float(solution[2]+circle_center@circle_center),0));center=mean+plane_basis@circle_center
    u,v,n=deterministic_basis(vec[:,0]);all_delta=xyz-center;all_uv=np.c_[all_delta@u,all_delta@v]
    radial=np.linalg.norm(all_uv,axis=1)-radius;plane=all_delta@n
    fit_angle=np.unwrap(np.arctan2(fit_uv[:,1]-circle_center[1],fit_uv[:,0]-circle_center[0]))
    omega=abs(float(np.polyfit(fit_t-fit_t[0],fit_angle,1)[0]))
    return {"status":"OK","valid_points":len(xyz),"fit_retained_points":len(fit_xyz),
        "center_mm":center,"radius_mm":radius,"plane_normal":n,"u":u,"v":v,
        "radial_residual_rms_mm":float(np.sqrt(np.mean(radial*radial))),
        "radial_residual_p95_mm":float(np.quantile(np.abs(radial),.95)),
        "out_of_plane_rms_mm":float(np.sqrt(np.mean(plane*plane))),
        "out_of_plane_p95_mm":float(np.quantile(np.abs(plane),.95)),
        "angular_rate_rad_s":omega,"apparent_rpm":omega*60/(2*math.pi)}


def project(points:np.ndarray,origin:np.ndarray,u:np.ndarray,v:np.ndarray)->np.ndarray:
    delta=np.asarray(points)-origin
    return np.c_[delta@u,delta@v]


def window_metrics(position:dict,full_fit:dict)->list[dict]:
    rows=[]
    for node in NODES:
        data=position[node];times=data["time_s"];xyz=data["xyz_mm"]
        windows=[("FULL",0.,NOMINAL_S)]+[(f"HOUR_{h}",h*3600.,min((h+1)*3600.,NOMINAL_S)) for h in range(math.ceil(NOMINAL_S/3600))]
        reference_center=None
        for label,start,end in windows:
            mask=(times>=start)&(times<end);fit=robust_orbit_fit(times[mask],xyz[mask])
            if fit["status"]!="OK":continue
            if label!="FULL" and reference_center is None:reference_center=np.asarray(fit["center_mm"])
            drift=0. if label!="FULL" and np.array_equal(np.asarray(fit["center_mm"]),reference_center) else (
                float(np.linalg.norm(np.asarray(fit["center_mm"])-reference_center)) if reference_center is not None else None)
            rows.append({"node":node,"window":label,"elapsed_start_s":start,"elapsed_end_s":end,
                "window_duration_s":end-start,"valid_point_count":fit["valid_points"],"fit_retained_point_count":fit["fit_retained_points"],
                "apparent_radius_m":fit["radius_mm"]*.001,"center_V4_x_m":fit["center_mm"][0]*.001,
                "center_V4_y_m":fit["center_mm"][1]*.001,"center_V4_z_m":fit["center_mm"][2]*.001,
                "center_drift_from_first_valid_window_m":None if drift is None else drift*.001,
                "plane_normal_V4_x":fit["plane_normal"][0],"plane_normal_V4_y":fit["plane_normal"][1],
                "plane_normal_V4_z":fit["plane_normal"][2],"out_of_plane_rms_m":fit["out_of_plane_rms_mm"]*.001,
                "out_of_plane_p95_m":fit["out_of_plane_p95_mm"]*.001,
                "radial_residual_rms_m":fit["radial_residual_rms_mm"]*.001,
                "radial_residual_p95_m":fit["radial_residual_p95_mm"]*.001,"T4_apparent_RPM":fit["apparent_rpm"],
                "coordinate_contract":"RELATIVE_GEOMETRY_ONLY","fit_interpretation":"SELF_CONSISTENCY_ONLY"})
    return rows


def shared_plane(full_fit:dict,position:dict)->dict:
    scatter=np.zeros((3,3));count=0
    for node in NODES:
        xyz=position[node]["xyz_mm"];xyz=xyz[np.isfinite(xyz).all(axis=1)]
        delta=xyz-np.asarray(full_fit[node]["center_mm"]);scatter+=delta.T@delta;count+=len(delta)
    eig,vec=np.linalg.eigh(scatter/max(count-1,1));u,v,n=deterministic_basis(vec[:,0])
    origin=.5*(np.asarray(full_fit[NODES[0]]["center_mm"])+np.asarray(full_fit[NODES[1]]["center_mm"]))
    fits={}
    for node in NODES:
        xyz=position[node]["xyz_mm"];valid=np.isfinite(xyz).all(axis=1);uv=project(xyz[valid],origin,u,v)
        center0=np.median(uv,axis=0);keep=np.linalg.norm(uv-center0,axis=1)<=np.quantile(np.linalg.norm(uv-center0,axis=1),.99)
        q=uv[keep];A=np.c_[2*q[:,0],2*q[:,1],np.ones(len(q))];b=np.sum(q*q,axis=1);s=np.linalg.lstsq(A,b,rcond=None)[0]
        fits[node]={"center_uv_mm":s[:2],"radius_mm":math.sqrt(max(float(s[2]+s[:2]@s[:2]),0))}
    return {"origin_mm":origin,"u":u,"v":v,"normal":n,"eigenvalues_mm2":eig,"per_node":fits,
        "method":"combined within-node centered scatter PCA; deterministic +V4-Z normal; projected V4-X defines +u"}


def equal_cube_limits(position:dict)->tuple[np.ndarray,float]:
    all_points=[]
    for node in NODES:
        xyz=position[node]["xyz_mm"];all_points.append(xyz[np.isfinite(xyz).all(axis=1)][::20]*.001)
    points=np.vstack(all_points);lo=np.quantile(points,.002,axis=0);hi=np.quantile(points,.998,axis=0)
    center=.5*(lo+hi);half=.55*float(np.max(hi-lo))
    return center,half


def set_3d_equal(ax,center,half):
    ax.set_xlim(center[0]-half,center[0]+half);ax.set_ylim(center[1]-half,center[1]+half);ax.set_zlim(center[2]-half,center[2]+half)
    ax.set_box_aspect((1,1,1));ax.set_xlabel("relative V4 X (m)");ax.set_ylabel("relative V4 Y (m)");ax.set_zlabel("relative V4 Z (m)")


def draw_plane(ax,fit,color,extent_m):
    c=np.asarray(fit["center_mm"])*.001;u=np.asarray(fit["u"]);v=np.asarray(fit["v"])
    grid=np.linspace(-extent_m,extent_m,2);a,b=np.meshgrid(grid,grid);surface=c+a[...,None]*u+b[...,None]*v
    ax.plot_surface(surface[:,:,0],surface[:,:,1],surface[:,:,2],color=color,alpha=.09,shade=False,linewidth=0)


def time_rows(metrics,node):
    return [r for r in metrics if r["node"]==node and r["window"].startswith("HOUR_")]


def save_figure(fig,out:Path,stem:str):
    fig.savefig(out/f"{stem}.svg",format="svg",metadata={"Date":None},bbox_inches="tight")
    svg=out/f"{stem}.svg";svg.write_text("\n".join(x.rstrip() for x in svg.read_text().splitlines())+"\n",encoding="utf-8")
    fig.savefig(out/f"{stem}.png",format="png",dpi=300,metadata={"Software":"BioSpur deterministic trajectory plot v1"},bbox_inches="tight")
    plt.close(fig)


def individual_figure(out,node,position,fit,metrics,cube,projection_half,gyro):
    fig=plt.figure(figsize=(17,10),layout="constrained");gs=fig.add_gridspec(4,3,width_ratios=(1.12,1.12,1),hspace=.08)
    ax3=fig.add_subplot(gs[:,0],projection="3d");ax2=fig.add_subplot(gs[:,1]);axes=[fig.add_subplot(gs[i,2]) for i in range(4)]
    data=position[node];valid=np.isfinite(data["xyz_mm"]).all(axis=1);xyz=data["xyz_mm"][valid]*.001;t=data["time_s"][valid]/3600
    stride=max(1,math.ceil(len(xyz)/12000));norm=colors.Normalize(0,NOMINAL_S/3600);cmap="viridis"
    ax3.scatter(xyz[::stride,0],xyz[::stride,1],xyz[::stride,2],c=t[::stride],cmap=cmap,norm=norm,s=.7,alpha=.16,rasterized=False)
    center=np.asarray(fit["center_mm"])*.001;ax3.scatter(*center,s=50,marker="x",color="black",label="robust fitted centre")
    draw_plane(ax3,fit,NODE_COLORS[node],fit["radius_mm"]*.001*1.15);set_3d_equal(ax3,*cube);ax3.view_init(elev=24,azim=-58)
    ax3.set_title("A  Canonical T4 XYZ trajectory\nrelative V4 frame; SELF-CONSISTENCY ONLY",loc="left");ax3.legend(loc="upper left",fontsize=8)
    sm=ScalarMappable(norm=norm,cmap=cmap);fig.colorbar(sm,ax=ax3,shrink=.55,pad=.08,label="elapsed rotation time (h)")
    uv=project(data["xyz_mm"][valid],fit["center_mm"],fit["u"],fit["v"])*.001
    ax2.scatter(uv[::stride,0],uv[::stride,1],c=t[::stride],cmap=cmap,norm=norm,s=.7,alpha=.15)
    theta=np.linspace(0,2*np.pi,721);radius=fit["radius_mm"]*.001;ax2.plot(radius*np.cos(theta),radius*np.sin(theta),"k--",lw=1.5,label="robust fitted circle")
    ax2.scatter(uv[0,0],uv[0,1],marker="*",s=90,color="#CC79A7",edgecolor="black",linewidth=.5,label="first valid point")
    ax2.set(xlim=(-projection_half,projection_half),ylim=(-projection_half,projection_half),xlabel="orbit-plane u (m)",ylabel="orbit-plane v (m)")
    ax2.set_aspect("equal",adjustable="box");ax2.grid(alpha=.18);ax2.set_title("B  Projection onto node best-fit plane",loc="left");ax2.legend(loc="upper right",fontsize=8)
    ax2.text(.02,.02,f"apparent radius = {radius:.4f} m\nradial RMS / P95 = {fit['radial_residual_rms_mm']:.1f} / {fit['radial_residual_p95_mm']:.1f} mm\nSELF-CONSISTENCY ONLY",
             transform=ax2.transAxes,fontsize=8,va="bottom",bbox={"facecolor":"white","alpha":.82,"edgecolor":"none"})
    rows=time_rows(metrics,node);hour=np.array([(r["elapsed_start_s"]+r["elapsed_end_s"])/7200 for r in rows])
    values=([r["apparent_radius_m"] for r in rows],[r["center_drift_from_first_valid_window_m"] for r in rows],
            [r["out_of_plane_rms_m"] for r in rows],[r["T4_apparent_RPM"] for r in rows])
    labels=("apparent radius (m)","centre displacement (m)","out-of-plane RMS (m)","T4 apparent RPM")
    for ax,y,label in zip(axes,values,labels):ax.plot(hour,y,marker="o",color=NODE_COLORS[node],lw=1.5);ax.set_ylabel(label);ax.grid(alpha=.2);ax.set_xlim(0,NOMINAL_S/3600)
    axes[0].set_title("C  Long-term orbit stability (fixed 1 h windows)",loc="left");axes[-1].set_xlabel("elapsed rotation time (h)")
    fig.suptitle(f"{node} overnight UWB trajectory — 7.283928561 h nominal interval",fontsize=15)
    save_figure(fig,out,f"{node}_OVERNIGHT_TRAJECTORY")


def combined_figure(out,position,full_fit,metrics,shared,cube,gyro):
    fig=plt.figure(figsize=(17,10),layout="constrained");gs=fig.add_gridspec(4,3,width_ratios=(1.12,1.12,1),hspace=.08)
    ax3=fig.add_subplot(gs[:,0],projection="3d");ax2=fig.add_subplot(gs[:,1]);axes=[fig.add_subplot(gs[i,2]) for i in range(4)]
    for node in NODES:
        data=position[node];valid=np.isfinite(data["xyz_mm"]).all(axis=1);xyz=data["xyz_mm"][valid]*.001;stride=max(1,math.ceil(len(xyz)/9000))
        ax3.scatter(xyz[::stride,0],xyz[::stride,1],xyz[::stride,2],s=.8,alpha=.12,color=NODE_COLORS[node],label=node)
        center=np.asarray(full_fit[node]["center_mm"])*.001;ax3.scatter(*center,s=48,marker="x",color=NODE_COLORS[node]);draw_plane(ax3,full_fit[node],NODE_COLORS[node],full_fit[node]["radius_mm"]*.001*1.08)
    set_3d_equal(ax3,*cube);ax3.view_init(elev=24,azim=-58);ax3.set_title("A  Both trajectories in common relative V4 XYZ\nno per-node translation",loc="left");ax3.legend()
    shared_origin=np.asarray(shared["origin_mm"]);u=np.asarray(shared["u"]);v=np.asarray(shared["v"]);theta=np.linspace(0,2*np.pi,721)
    all_uv=[]
    for node in NODES:
        data=position[node];valid=np.isfinite(data["xyz_mm"]).all(axis=1);uv=project(data["xyz_mm"][valid],shared_origin,u,v)*.001;all_uv.append(uv);stride=max(1,math.ceil(len(uv)/9000))
        ax2.scatter(uv[::stride,0],uv[::stride,1],s=.8,alpha=.12,color=NODE_COLORS[node],label=node)
        sf=shared["per_node"][node];c=np.asarray(sf["center_uv_mm"])*.001;r=float(sf["radius_mm"])*.001
        ax2.plot(c[0]+r*np.cos(theta),c[1]+r*np.sin(theta),ls="--",lw=1.5,color=NODE_COLORS[node])
        ax2.scatter(c[0],c[1],marker="x",s=45,color=NODE_COLORS[node])
    uvall=np.vstack(all_uv);lo=np.quantile(uvall,.002,axis=0);hi=np.quantile(uvall,.998,axis=0);mid=.5*(lo+hi);half=.56*max(hi-lo)
    ax2.set(xlim=(mid[0]-half,mid[0]+half),ylim=(mid[1]-half,mid[1]+half),xlabel="shared-plane u (m)",ylabel="shared-plane v (m)")
    ax2.set_aspect("equal",adjustable="box");ax2.grid(alpha=.18);ax2.legend();ax2.set_title("B  Deterministic shared-plane comparison\nrelative positions and phase preserved",loc="left")
    ratio=full_fit["BSF3C79"]["radius_mm"]/full_fit["BSFC2CC"]["radius_mm"]
    ax2.text(.02,.02,f"BSF3C79 apparent r = {full_fit['BSF3C79']['radius_mm']:.1f} mm\nBSFC2CC apparent r = {full_fit['BSFC2CC']['radius_mm']:.1f} mm\nradius ratio = {ratio:.3f}\nrelative phase ≈ 163.0° (SELF-CONSISTENCY ONLY)",
             transform=ax2.transAxes,fontsize=8,va="bottom",bbox={"facecolor":"white","alpha":.84,"edgecolor":"none"})
    for node in NODES:
        rows=time_rows(metrics,node);hour=np.array([(r["elapsed_start_s"]+r["elapsed_end_s"])/7200 for r in rows]);color=NODE_COLORS[node]
        axes[0].plot(hour,[r["T4_apparent_RPM"] for r in rows],marker="o",color=color,label=node)
        axes[1].plot(hour,[r["apparent_radius_m"] for r in rows],marker="o",color=color)
        axes[2].plot(hour,[r["center_drift_from_first_valid_window_m"] for r in rows],marker="o",color=color)
        grows=[r for r in gyro if r["node"]==node];axes[3].plot([r["hour_mid"] for r in grows],[r["gyro_apparent_RPM"] for r in grows],marker="o",color=color)
    for ax,label in zip(axes,("T4 apparent RPM","apparent radius (m)","centre displacement (m)","gyro apparent RPM")):
        ax.set_ylabel(label);ax.grid(alpha=.2);ax.set_xlim(0,NOMINAL_S/3600)
    axes[0].legend(fontsize=8,ncol=2);axes[0].set_title("C  Same-window time comparison",loc="left");axes[-1].set_xlabel("elapsed rotation time (h)")
    fig.suptitle("Dual-node overnight UWB trajectories — relative V4 geometry, SELF-CONSISTENCY ONLY",fontsize=15)
    save_figure(fig,out,"DUAL_NODE_OVERNIGHT_TRAJECTORIES")


def gyro_rows()->list[dict]:
    rows=[]
    with (SOURCE_ANALYSIS/"TWO_NODE_ANGULAR_COMPARISON.csv").open() as handle:
        for row in csv.DictReader(handle):
            h=int(row["hour"])
            if h>=math.ceil(NOMINAL_S/3600):continue
            for node in NODES:
                value=row.get(f"{node}_gyro_median_dps","")
                if value!="":rows.append({"node":node,"hour":h,"hour_mid":min(h*3600+1800,NOMINAL_S)/3600,
                    "gyro_median_dps":float(value),"gyro_apparent_RPM":float(value)/6})
    return rows


def derive(out:Path)->None:
    if out.exists():shutil.rmtree(out)
    out.mkdir(parents=True)
    raw_before=sha(RAW)
    if raw_before!=EXPECTED_RAW_SHA:raise RuntimeError(f"raw hash mismatch: {raw_before}")
    imu,uwb,audit=overnight.decode_capture()
    if audit["raw_sha256"]!=EXPECTED_RAW_SHA:raise RuntimeError("decoded raw mismatch")
    position=overnight.solve_positions(uwb,int(audit["first_formal_master_ms"]),NOMINAL_S)
    full_fit={node:robust_orbit_fit(position[node]["time_s"],position[node]["xyz_mm"]) for node in NODES}
    frozen=json.loads((SOURCE_ANALYSIS/"UWB_ORBIT_SELF_CONSISTENCY.json").read_text())["per_node"]
    comparison={}
    for node in NODES:
        comparison[node]={"radius_difference_mm":full_fit[node]["radius_mm"]-float(frozen[node]["radius_mm"]),
            "rpm_difference":full_fit[node]["apparent_rpm"]-float(frozen[node]["apparent_rpm"])}
        if abs(comparison[node]["radius_difference_mm"])>1e-6 or abs(comparison[node]["rpm_difference"])>1e-8:
            raise RuntimeError(f"frozen orbit mismatch {node}: {comparison[node]}")
    metrics=window_metrics(position,full_fit);write_csv(out/"PLOT_METRICS.csv",metrics)
    shared=shared_plane(full_fit,position);gyro=gyro_rows();cube=equal_cube_limits(position)
    projection_half=.001*1.12*max(full_fit[n]["radius_mm"]+full_fit[n]["radial_residual_p95_mm"] for n in NODES)
    for node in NODES:individual_figure(out,node,position,full_fit[node],metrics,cube,projection_half,gyro)
    combined_figure(out,position,full_fit,metrics,shared,cube,gyro)
    source_paths={"raw":RAW,"layout":overnight.LAYOUT,"geometry_manifest":overnight.GEOMETRY,
        "frozen_orbit_metrics":SOURCE_ANALYSIS/"UWB_ORBIT_SELF_CONSISTENCY.json",
        "frozen_angular_comparison":SOURCE_ANALYSIS/"TWO_NODE_ANGULAR_COMPARISON.csv",
        "covariance_repair_integrity":REPAIR_ANALYSIS/"NUMERICAL_INTEGRITY.json"}
    manifest={"schema":"biospur-v47-dual-rotation-trajectory-plots-v1","coordinate_contract":"RELATIVE_GEOMETRY_ONLY",
        "interpretation":"SELF_CONSISTENCY_ONLY_NO_ABSOLUTE_ACCURACY_OR_PHYSICAL_VERTICAL_CLAIM",
        "nominal_interval_s":NOMINAL_S,"nominal_interval_h":NOMINAL_S/3600,
        "nodes":list(NODES),"source_paths":{k:str(v.relative_to(ROOT)) for k,v in source_paths.items()},
        "source_sha256":{k:sha(v) for k,v in source_paths.items()},"raw_hash_before":raw_before,"raw_hash_after":sha(RAW),
        "source_accounting":{node:{"T4_solutions":position[node]["solution_count"],"full_valid_points":full_fit[node]["valid_points"],
            "fit_retained_points":full_fit[node]["fit_retained_points"],"last_valid_elapsed_s":float(position[node]["time_s"][np.isfinite(position[node]["xyz_mm"]).all(axis=1)][-1])} for node in NODES},
        "frozen_reproduction":comparison,"fit":{"method":"frozen PCA plane plus algebraic circle after deterministic 99th-percentile distance trim",
            "metrics_input":"all finite canonical T4 points in nominal interval","window_s":3600,
            "residuals":"computed on all finite points; fitting trim does not hide scatter"},
        "render":{"individual_max_points":12000,"combined_max_points_per_node":9000,"decimation":"valid rows [::ceil(N/max_points)] display only",
            "random_sampling":False,"time_color_range_h":[0,NOMINAL_S/3600],"png_dpi":300,"svg_editable":True,
            "common_3d_cube_center_m":cube[0],"common_3d_cube_half_extent_m":cube[1],"individual_projection_half_extent_m":projection_half},
        "shared_plane":shared,"apparent_radius_ratio":full_fit["BSF3C79"]["radius_mm"]/full_fit["BSFC2CC"]["radius_mm"],
        "relative_phase_deg_from_frozen_analysis":163.029619229,"larger_apparent_radius_node":"BSF3C79",
        "mounting_assignment":"NOT_CONFIRMED_MISSING_OPERATOR_TOKEN","algorithm_rerun":False,"S2R_used":False,"hardware_access":False,
        "plot_driver_sha256":sha(Path(__file__).resolve())}
    write_json(out/"PLOT_MANIFEST.json",manifest)
    (out/"PLOT_METHOD.md").write_text(f"""# Overnight dual-node trajectory plot method

These figures are an offline visualization of canonical T4 positions in the frozen relative V4-io frame. V4 Z is not asserted to be physical vertical. Fits, radii, centres, planes, RPM and phase are `SELF-CONSISTENCY ONLY`; no absolute accuracy or ground-truth claim is made. BSF3C79 has the larger apparent radius, but the missing mounting token prevents calling it the confirmed long-arm node.

Only `[0, {NOMINAL_S:.7f}) s` (`7.283928561 h`) is admitted. The battery-depletion/reconnect tail is excluded. T4 uses the canonical frozen solver, geometry and delay parameters. S2R and Fusion are not run.

The frozen orbit fit is reproduced: finite positions are trimmed only for fitting at the deterministic 99th percentile of distance from the componentwise median; PCA supplies the plane and an algebraic least-squares circle supplies centre/radius. Residual metrics are then evaluated on every finite nominal point, including visible scatter. Fixed one-hour causal windows have no smoothing and the last window is partial. Centre displacement is relative to the first valid window.

The shared comparison plane comes from the combined within-node-centred scatter matrix, preventing centre separation from dominating its normal. Its normal is oriented toward positive relative V4 Z; projected V4 X defines positive u. Both trajectories are projected about one common origin, so relative position and phase are preserved.

All metrics and fits use complete valid nominal data. Rendering alone uses fixed-stride decimation: at most 12,000 points in each individual plot and 9,000 per node in the combined plot. There is no random sampling or trajectory smoothing. Individual 3D panels share the same equal-sided cube, individual plane panels share the same limits, and all 2D orbit panels use equal aspect. PNG is 300 DPI; SVG remains editable.
""",encoding="utf-8")
    if sha(RAW)!=raw_before:raise RuntimeError("raw evidence changed")
    sums=[f"{sha(out/name)}  {name}" for name in CORE]
    (out/"SHA256SUMS").write_text("\n".join(sums)+"\n",encoding="utf-8")


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=DEFAULT_OUT);args=parser.parse_args();derive(args.output)


if __name__=="__main__":main()
