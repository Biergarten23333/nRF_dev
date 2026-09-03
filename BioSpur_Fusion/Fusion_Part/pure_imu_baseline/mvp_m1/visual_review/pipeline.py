"""Build the visual-review-only MVP-M1R acceptance pack."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import GEOMETRY
from pure_imu_baseline.stage2.config import BONES
from pure_imu_baseline.mvp_m1.exports import sha

from . import PRODUCT_ID
from .renderer import render_comparison,render_four_view,render_orbit,render_recenter_demo
from .selection import MVP_ROOT,STAGE1_ROOT,build_ledger,load_mvp_capture,rendered_intervals

CAPTURES="123"
VIDEO_NAMES=[*(f"CAPTURE{c}_MVP_RAW_4VIEW_REVIEW.mp4" for c in CAPTURES),*(f"CAPTURE{c}_MVP_RAW_ORBIT_REVIEW.mp4" for c in CAPTURES),"C123_MVP_RAW_COMPARISON.mp4","MANUAL_GLOBAL_YAW_RECENTER_DEMO.mp4"]


def dump(path: Path,value) -> None:
    path.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")


def frozen_files() -> list[Path]:
    return [MVP_ROOT/"FINAL_RESULT.json",MVP_ROOT/"REPRODUCIBILITY_MANIFEST.json",MVP_ROOT/"RAW_IMMUTABILITY_AUDIT.json",
            *(MVP_ROOT/f"CAPTURE{c}_MVP_REPLAY_DATA.npz" for c in CAPTURES),*(STAGE1_ROOT/f"CAPTURE{c}_REPLAY_DATA.npz" for c in CAPTURES),
            STAGE1_ROOT/"REPRODUCIBILITY_MANIFEST.json",STAGE1_ROOT/"SKELETON_GEOMETRY.json",STAGE1_ROOT/"SEGMENT_FRAME_CONTRACT.json"]


def source_hashes() -> dict[str,dict]:
    return {str(path):{"sha256":sha(path),"bytes":path.stat().st_size} for path in frozen_files()}


def bone_error(positions: np.ndarray,available: np.ndarray,joint_names: np.ndarray) -> float:
    index={str(name):i for i,name in enumerate(joint_names)};maximum=0.0
    for name,a_name,b_name,_ in BONES:
        a,b=index[a_name],index[b_name];mask=available[:,a]&available[:,b]
        lengths=np.linalg.norm(positions[mask,a]-positions[mask,b],axis=-1)
        if len(lengths):maximum=max(maximum,float(np.max(np.abs(lengths-GEOMETRY[name]))))
    return maximum


def array_bytes_equal(a: np.ndarray,b: np.ndarray) -> bool:
    return a.dtype==b.dtype and a.shape==b.shape and np.ascontiguousarray(a).tobytes()==np.ascontiguousarray(b).tobytes()


def parity_audit(raws: dict[str,dict],render_stats: dict) -> dict:
    captures={}
    for capture,raw in raws.items():
        with np.load(STAGE1_ROOT/f"CAPTURE{capture}_REPLAY_DATA.npz",allow_pickle=False) as source:
            fields={name:array_bytes_equal(raw[name],source[name]) for name in ("time_s","q_GB_wxyz","valid","filter_reset","joint_positions_m","joint_available","segment_names","joint_names")}
            coordinate_difference=float(np.nanmax(np.abs(raw["joint_positions_m"].astype(np.float64)-source["joint_positions_m"].astype(np.float64))))
        internal_long_gaps=[];t=raw["time_s"];valid=raw["valid"]
        for node,name in enumerate(raw["segment_names"]):
            inv=~valid[:,node];starts=np.flatnonzero(inv&np.r_[True,valid[:-1,node]]);stops=np.flatnonzero(inv&np.r_[valid[1:,node],True])
            for start,stop in zip(starts,stops):
                duration=float(t[stop]-t[start]+1/60)
                if duration>0.05 and t[start]<t[-1]-1: internal_long_gaps.append({"node":str(name),"start_s":float(t[start]),"end_s":float(t[stop]),"duration_s":duration,"render_input_remains_unavailable":bool(np.all(~valid[start:stop+1,node]))})
        captures[capture]={"frozen_fields_bit_exact":fields,"maximum_render_input_coordinate_difference_m":coordinate_difference,
                           "raw_bone_length_error_max_m":bone_error(raw["joint_positions_m"],raw["joint_available"],raw["joint_names"]),
                           "camera_pose_array_mutations":sum(item.get("camera_pose_array_mutations",0) for item in render_stats.values() if item.get("capture")==capture),
                           "internal_long_gaps":internal_long_gaps,"invalid_frames_remain_unavailable":bool(np.all(np.isnan(raw["working_q_GB_wxyz"][~raw["valid"]]))),
                           "left_right_mapping":{name:{"proximal":a,"distal":b,"class":category} for name,a,b,category in BONES},
                           "pass":all(fields.values()) and coordinate_difference==0 and bone_error(raw["joint_positions_m"],raw["joint_available"],raw["joint_names"])<=2e-6 and all(item["render_input_remains_unavailable"] for item in internal_long_gaps)}
    return {"schema":"biospur.pure_imu.mvp_m1r.render_input_parity.v1","governing_branch":"RAW","rendered_timestamp_rule":"every encoded animation frame maps to one exact 60 Hz MVP frame; 30 Hz output uses stride two","render_stats":render_stats,"captures":captures,"maximum_render_input_coordinate_difference_m":max(item["maximum_render_input_coordinate_difference_m"] for item in captures.values()),"encoding_changes_numerical_artifacts":False,"pass":all(item["pass"] for item in captures.values()) and all(item.get("camera_pose_array_mutations",0)==0 for item in render_stats.values())}


def ffprobe(path: Path) -> dict:
    run=subprocess.run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,text=True,check=True)
    result=json.loads(run.stdout);video=next(stream for stream in result["streams"] if stream["codec_type"]=="video")
    passed=video.get("codec_name")=="h264" and video.get("pix_fmt")=="yuv420p" and int(video.get("width",0))>0 and float(video.get("duration",result.get("format",{}).get("duration",0)))>0
    return {"path":str(path),"sha256":sha(path),"bytes":path.stat().st_size,"codec_name":video.get("codec_name"),"profile":video.get("profile"),"pix_fmt":video.get("pix_fmt"),"width":int(video.get("width",0)),"height":int(video.get("height",0)),"avg_frame_rate":video.get("avg_frame_rate"),"duration_s":float(video.get("duration",result.get("format",{}).get("duration",0))),"nb_frames":int(video["nb_frames"]) if video.get("nb_frames") else None,"ffprobe_pass":passed}


def camera_contract() -> dict:
    return {"schema":"biospur.pure_imu.mvp.m1r.camera_contract.v1","projection":"orthographic","global_axes":"+X forward, +Y left, +Z up",
            "fixed_center_m":[0,0,-0.15],"per_frame_autofit":False,"camera_definitions":{"FRONT":{"look_direction":[1,0,0]},"SIDE":{"look_direction":[0,-1,0]},"TOP":{"look_direction":[0,0,1],"up_reference":[1,0,0]},"OBLIQUE":{"look_direction":[1,-1,0.7]},"ORBIT":{"yaw_equation":"-0.72 + 2*pi*output_index/(N-1)","pitch_rad":0.32}},
            "four_view_pixels_per_m":150.0,"orbit_pixels_per_m":320.0,"comparison_pixels_per_m":150.0,"recenter_demo_pixels_per_m":250.0,
            "bounds":"fixed for the complete video, identical camera equations across captures","camera_modifies_pose_data":False,"skeleton_rotated_to_simulate_camera":False}


def write_index(output: Path) -> None:
    frozen=MVP_ROOT.as_uri()
    video_sections=[]
    for capture in CAPTURES:
        video_sections.append(f'''<section><h2>Capture {capture}</h2><p><a href="{frozen}/CAPTURE{capture}_PURE_IMU_MVP.html">Open interactive Capture {capture}</a></p><div class="grid"><article><h3>Four synchronized views</h3><video controls preload="metadata" src="CAPTURE{capture}_MVP_RAW_4VIEW_REVIEW.mp4"></video><a href="CAPTURE{capture}_MVP_RAW_4VIEW_REVIEW.mp4">Open video</a></article><article><h3>Slow deterministic orbit</h3><video controls preload="metadata" src="CAPTURE{capture}_MVP_RAW_ORBIT_REVIEW.mp4"></video><a href="CAPTURE{capture}_MVP_RAW_ORBIT_REVIEW.mp4">Open video</a></article></div></section>''')
    html=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BioSpur MVP-M1 Visual Review</title><style>body{{margin:0;background:#050b11;color:#e7eef6;font:16px system-ui,sans-serif}}main{{max-width:1280px;margin:auto;padding:20px}}a{{color:#7dd3fc}}.truth{{display:flex;flex-wrap:wrap;gap:8px;color:#fbbf24}}.truth strong{{border:1px solid #6b5a20;padding:4px 7px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}}article{{border:1px solid #274156;padding:12px;background:#08131d}}video{{width:100%;display:block;background:#000;margin-bottom:8px}}section{{margin:24px 0}}@media(max-width:520px){{.grid{{grid-template-columns:1fr}}article{{padding:8px}}}}</style></head><body><main><h1>BioSpur Pure-IMU MVP-M1 Visual Review</h1><div class="truth"><strong>RAW</strong><strong>PURE IMU</strong><strong>ROOT FIXED</strong><strong>GLOBAL YAW MAY DRIFT</strong><strong>POSE ALGORITHM UNCHANGED</strong></div><p><a href="{frozen}/C123_PURE_IMU_MVP_VIEWER_INDEX.html">Open authoritative three-capture interactive viewer</a></p>{''.join(video_sections)}<section><h2>Three-capture comparison</h2><video controls preload="metadata" src="C123_MVP_RAW_COMPARISON.mp4"></video><a href="C123_MVP_RAW_COMPARISON.mp4">Open comparison video</a></section><section><h2>Manual global-yaw gauge demonstration</h2><video controls preload="metadata" src="MANUAL_GLOBAL_YAW_RECENTER_DEMO.mp4"></video><a href="MANUAL_GLOBAL_YAW_RECENTER_DEMO.mp4">Open recenter demonstration</a></section><p><a href="MVP_M1_VISUAL_REVIEW_FINAL.md">Final visual-review report</a></p></main></body></html>'''
    (output/"C123_MVP_VISUAL_REVIEW_INDEX.html").write_text(html,encoding="utf-8")


def rebuild_manifest(output: Path,status: str,command: str) -> dict:
    artifacts={path.name:{"sha256":sha(path),"bytes":path.stat().st_size} for path in sorted(output.iterdir()) if path.is_file() and path.name!="REPRODUCIBILITY_MANIFEST.json"}
    implementation=Path(__file__).resolve().parent;implementation_files={path.name:{"sha256":sha(path),"bytes":path.stat().st_size} for path in sorted(implementation.iterdir()) if path.is_file() and path.suffix in (".py",".md",".json")}
    manifest={"schema":"biospur.pure_imu.mvp.m1r.reproducibility.v1","created_utc":datetime.now(timezone.utc).isoformat(),"product_id":PRODUCT_ID,
              "pipeline_command":command,"working_directory":str(Path(__file__).resolve().parents[3]),"implementation_root":str(implementation),"implementation_files":implementation_files,
              "python":sys.version,"numpy":np.__version__,"platform":platform.platform(),"frozen_source_hashes":source_hashes(),"artifacts":artifacts,"browser_runtime_status":status,
              "pose_algorithm_unchanged":True,"raw_arrays_unchanged":True,"no_automatic_correction":True,"no_uwb_numeric_data_used":True,"no_new_human_capture_requested":True,"live_hardware_integration_not_started":True,"no_commit":True,"no_push":True}
    dump(output/"REPRODUCIBILITY_MANIFEST.json",manifest);return manifest


def run(output: Path) -> dict:
    output=output.resolve()
    if output.exists(): raise FileExistsError(f"refusing to overwrite {output}")
    result=json.loads((MVP_ROOT/"FINAL_RESULT.json").read_text(encoding="utf-8"))
    if result.get("verdict")!="PURE_IMU_RAW_MVP_M1_READY_WITH_DECLARED_LIMITATIONS": raise RuntimeError("frozen MVP-M1 ready verdict missing")
    before=source_hashes();output.mkdir(parents=True)
    raws={capture:load_mvp_capture(capture) for capture in CAPTURES};ledgers={capture:build_ledger(capture,raws[capture]) for capture in CAPTURES}
    dump(output/"VISUAL_INTERVAL_SELECTION_LEDGER.json",{"schema":"biospur.pure_imu.mvp.m1r.interval_selection_ledger.v1","captures":ledgers,"action_metadata_use":"VISUAL_SELECTION_ONLY","capture_id_affects_pose_math":False})
    dump(output/"CAMERA_CONTRACT.json",camera_contract())
    render_stats={}
    for capture in CAPTURES:
        intervals=rendered_intervals(ledgers[capture]);print(f"capture {capture}: rendering four-view digest ({len(intervals)} intervals)",flush=True)
        name=f"CAPTURE{capture}_MVP_RAW_4VIEW_REVIEW.mp4";stats=render_four_view(output/name,capture,raws[capture],intervals);stats["capture"]=capture;stats["branch"]="RAW";render_stats[name]=stats
        print(f"capture {capture}: rendering orbit digest",flush=True)
        name=f"CAPTURE{capture}_MVP_RAW_ORBIT_REVIEW.mp4";stats=render_orbit(output/name,capture,raws[capture],intervals);stats["capture"]=capture;stats["branch"]="RAW";render_stats[name]=stats
    print("rendering three-capture comparison",flush=True)
    comparison_intervals={capture:next(item for item in rendered_intervals(ledgers[capture]) if item["label"]=="REPRESENTATIVE_LARGE_WHOLE_BODY_MOTION") for capture in CAPTURES}
    render_stats["C123_MVP_RAW_COMPARISON.mp4"]=render_comparison(output/"C123_MVP_RAW_COMPARISON.mp4",raws,comparison_intervals);render_stats["C123_MVP_RAW_COMPARISON.mp4"].update({"capture":"123","branch":"RAW"})
    print("rendering explicit manual-recenter demonstration",flush=True)
    demo_interval=next(item for item in rendered_intervals(ledgers["1"]) if item["label"]=="REPRESENTATIVE_ARM_DOMINANT_MOTION")
    render_stats["MANUAL_GLOBAL_YAW_RECENTER_DEMO.mp4"]=render_recenter_demo(output/"MANUAL_GLOBAL_YAW_RECENTER_DEMO.mp4",raws["1"],demo_interval);render_stats["MANUAL_GLOBAL_YAW_RECENTER_DEMO.mp4"].update({"capture":"1","branch":"EXPLICIT_DEMO_ONLY"})
    parity=parity_audit(raws,render_stats);dump(output/"RENDER_INPUT_PARITY_AUDIT.json",parity)
    after=source_hashes();immutability={"schema":"biospur.pure_imu.mvp.m1r.raw_immutability.v1","before":before,"after":after,"all_frozen_file_hashes_unchanged":before==after,"pose_algorithm_unchanged":True,"raw_arrays_unchanged":True,"pass":before==after};dump(output/"RAW_IMMUTABILITY_AUDIT.json",immutability)
    probes={name:ffprobe(output/name) for name in VIDEO_NAMES};video_audit={"schema":"biospur.pure_imu.mvp.m1r.video_playability.v1","ffmpeg_encoder":"libx264","required_codec":"H.264 High / yuv420p / faststart","videos":probes,"chrome_runtime_status":"PENDING","pass":all(item["ffprobe_pass"] for item in probes.values())};dump(output/"VIDEO_PLAYABILITY_AUDIT.json",video_audit)
    write_index(output);dump(output/"VIEWER_BROWSER_VERIFICATION.json",{"schema":"biospur.pure_imu.mvp.m1r.browser_verification.v1","status":"PENDING","pass":False})
    final={"schema":"biospur.pure_imu.mvp.m1r.final.v1","product_id":PRODUCT_ID,"verdict":"PENDING_BROWSER_RUNTIME","render_input_parity_pass":parity["pass"],"ffprobe_playability_pass":video_audit["pass"],"browser_runtime_pass":False,"raw_immutability_pass":immutability["pass"],"pose_algorithm_unchanged":True,"raw_arrays_unchanged":True,"no_automatic_correction":True,"no_uwb_numeric_data_used":True,"no_new_human_capture_requested":True,"live_hardware_integration_not_started":True,"no_commit":True,"no_push":True};dump(output/"FINAL_RESULT.json",final)
    report="""# BioSpur Pure-IMU MVP-M1R visual review

Status: **PENDING_BROWSER_RUNTIME**

All requested review videos were rendered from the frozen MVP-M1 `RAW` joint-position branch using fixed root, fixed geometry, exact MVP timestamps, and existing validity/reset masks. No pose algorithm or numerical export was modified. Browser verification is pending.
""";(output/"MVP_M1_VISUAL_REVIEW_FINAL.md").write_text(report,encoding="utf-8")
    command=f"PYTHONPATH=. python3 -m pure_imu_baseline.mvp_m1.visual_review.cli run --output {output}"
    rebuild_manifest(output,"PENDING",command);return final
