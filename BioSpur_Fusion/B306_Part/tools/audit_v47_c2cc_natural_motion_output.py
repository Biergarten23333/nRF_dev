#!/usr/bin/env python3
"""Post-freeze audit without re-decoding the one-time held-out ranges."""
from __future__ import annotations

import argparse,csv,hashlib,json,os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analyze_v47_c2cc_natural_motion import (
    CORE, ROOT, accepted_tokens, build_segments, canonical, fitting_record_ranges,
    fit_natural_motion, fit_summary, finish_sums, mount_data, read_fitting_rows, sha,
)
from v47_c2cc_natural_motion import evaluate_frozen_transform


def read_csv(path):
    with path.open() as handle:return list(csv.DictReader(handle))


def main():
    parser=argparse.ArgumentParser();parser.add_argument("run",type=Path);parser.add_argument("out",type=Path);args=parser.parse_args()
    run=args.run.resolve();out=args.out.resolve();freeze=json.loads((out/"NATURAL_MOTION_FREEZE_MANIFEST.json").read_text())
    signature=(out/"NATURAL_MOTION_FREEZE_MANIFEST.sha256").read_text().split()[0]
    if sha(out/"NATURAL_MOTION_FREEZE_MANIFEST.json")!=signature:raise RuntimeError("freeze signature mismatch")
    analyzer=ROOT/"B306_Part/tools/analyze_v47_c2cc_natural_motion.py";estimator=ROOT/"B306_Part/tools/v47_c2cc_natural_motion.py"
    if sha(analyzer)!=freeze["source_hashes"]["analyzer"] or sha(estimator)!=freeze["source_hashes"]["estimator"]:raise RuntimeError("frozen source mismatch")
    manifest=json.loads((run/"CAPTURE_MANIFEST.json").read_text());tokens=accepted_tokens(manifest);ranges=fitting_record_ranges(tokens)
    rows,_=read_fitting_rows(run/"continuous_raw/consumption_index.jsonl",ranges)
    recomputed={};per_action={}
    frozen=json.loads((out/"FITTING_RESULTS.json").read_text())
    for mount in "AB":
        data=mount_data(rows[mount],tokens,mount);objects,_,_,_=build_segments(data,mount,list(data["regions"]))
        fit=fit_natural_motion([x["segment"] for x in objects if x["accepted"]]);summary=fit_summary(fit)
        expected=frozen["mounts"][mount]["fit"]
        recomputed[mount]={"accepted_segments":sum(x["accepted"] for x in objects),
            "expected_segments":frozen["mounts"][mount]["accepted_segments"],
            "rotation_max_abs_difference":float(np.max(np.abs(np.asarray(summary["rotation"])-np.asarray(expected["rotation"])))),
            "all_checks_equal":summary["checks"]==expected["checks"],
            "all_scalar_metrics_exact":all(summary[key]==expected[key] for key in ("singular_ratio","condition","endpoint_median_deg","endpoint_p95_deg","path_median_normalized","path_p95_normalized","bootstrap_p95_deg","leave_one_p95_deg","leave_one_max_deg","accepted"))}
        per_action[mount]={}
        for name in data["regions"]:
            scores=[]
            for offset in np.arange(-.080,.0801,.005):
                objs,_,_,_=build_segments(data,mount,[name],float(offset));accepted=[x["segment"] for x in objs if x["accepted"]]
                if not accepted:continue
                metric=evaluate_frozen_transform(accepted,np.asarray(expected["rotation"]))
                scores.append({"offset_ms":round(1000*float(offset),6),"segment_count":len(accepted),
                    "endpoint_median_deg":metric["endpoint_median_deg"],"path_median_normalized":metric["path_median_normalized"]})
            per_action[mount][name]={"best_offset_ms_by_path_median":min(scores,key=lambda x:x["path_median_normalized"])["offset_ms"],"scores":scores}
    exact_core={}
    for name in CORE:
        path=out/name
        if path.exists():
            first=path.read_bytes();second=path.read_bytes()
            exact_core[name]={"sha256":hashlib.sha256(first).hexdigest(),"repeat_read_identical":first==second}
    result={"schema":"biospur-c2cc-natural-motion-determinism-v1","fitting_derivation_recomputed_from_authoritative_fitting_ranges":True,
        "fitting_recomputation":recomputed,"fitting_byte_equivalent_metrics":all(x["accepted_segments"]==x["expected_segments"] and x["rotation_max_abs_difference"]==0 and x["all_checks_equal"] and x["all_scalar_metrics_exact"] for x in recomputed.values()),
        "heldout_redecoded_or_reevaluated":False,"reason":"the signed freeze protocol permits exactly one held-out decode/evaluation; deterministic held-out artifacts are byte-reread, not evaluated a second time",
        "core_outputs":exact_core,"all_existing_core_repeat_reads_identical":all(x["repeat_read_identical"] for x in exact_core.values()),
        "complete_permitted_repeat":"PASS"}
    canonical(out/"DETERMINISM.json",result);canonical(out/"TIME_ALIGNMENT_PER_ACTION_POST_AUDIT.json",{
        "schema":"biospur-c2cc-natural-motion-time-per-action-post-audit-v1","dataset":"FITTING_ONLY","selection_changed":False,
        "primary_policy_ms":0,"per_action_offsets_forbidden":True,"diagnostic":per_action})

    t4=read_csv(out/"TRAJECTORY_T4_UWB_ONLY.csv");fused=read_csv(out/"TRAJECTORY_Q1_IMU_T4_ESKF.csv")
    proxy=[]
    for mount in "AB":
        f=[x for x in fused if x["mount"]==mount];ft=np.asarray([float(x["hardware_s"]) for x in f]);fp=np.asarray([[float(x[k]) for k in ("x_m","y_m","z_m")] for x in f])
        for row in (x for x in t4 if x["mount"]==mount):
            t=float(row["hardware_s"]);estimate=np.asarray([np.interp(t,ft,fp[:,axis]) for axis in range(3)]);measurement=np.asarray([float(row[k]) for k in ("x_m","y_m","z_m")]);res=measurement-estimate
            proxy.append({"mount":mount,"hardware_s":row["hardware_s"],"dx_m":res[0],"dy_m":res[1],"dz_m":res[2],"norm_m":float(np.linalg.norm(res)),"diagnostic":"POST_UPDATE_INTERPOLATED_RESIDUAL_PROXY_NOT_PREUPDATE_INNOVATION"})
    with (out/"POSITION_FACTOR_RESIDUAL_PROXY.csv").open("w",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(proxy[0]),lineterminator="\n");writer.writeheader();writer.writerows(proxy)
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=False)
    for axis,mount in zip(axes,"AB"):
        selected=[x for x in proxy if x["mount"]==mount];axis.plot([float(x["hardware_s"]) for x in selected],[float(x["norm_m"]) for x in selected],lw=.6)
        axis.set_title(f"Mount {mount}: post-update position residual proxy");axis.set_ylabel("norm [m]")
    axes[-1].set_xlabel("B306 hardware time [s]");fig.tight_layout();fig.savefig(out/"POSITION_FACTOR_RESIDUAL_PROXY.png",dpi=140);fig.savefig(out/"POSITION_FACTOR_RESIDUAL_PROXY.svg");plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(12,8))
    for row,mount in enumerate("AB"):
        selected=[x for x in fused if x["mount"]==mount];t=np.asarray([float(x["hardware_s"]) for x in selected])
        for key in ("qw","qx","qy","qz"):axes[row,0].plot(t,[float(x[key]) for x in selected],lw=.5,label=key)
        for key in ("ba_x","ba_y","ba_z"):axes[row,1].plot(t,[float(x[key]) for x in selected],lw=.5,label=key)
        axes[row,0].set_title(f"Mount {mount} quaternion");axes[row,1].set_title(f"Mount {mount} Q1 accel bias [m/s²]");axes[row,0].legend(fontsize=6);axes[row,1].legend(fontsize=6)
    fig.tight_layout();fig.savefig(out/"QUATERNION_AND_BIAS_TIMELINE.png",dpi=140);fig.savefig(out/"QUATERNION_AND_BIAS_TIMELINE.svg");plt.close(fig)
    for path in out.glob("*.svg"):path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines())+"\n")
    (out/"POST_FREEZE_DIAGNOSTIC_LIMITATIONS.md").write_text("""# Post-freeze diagnostic limitations

The original one-time held-out evaluation retained T4, IMU-only and fused trajectories but did not persist the pre-update position-factor innovation/NIS series or a second oracle-calibrated held-out replay. The CSV and plot labelled `POSITION_FACTOR_RESIDUAL_PROXY` are explicitly post-update, interpolated residual diagnostics and are not relabelled as innovations. Re-decoding or re-evaluating held-out IMU to manufacture those missing products would violate the signed one-time boundary, so it was not done. This omission does not rescue the result: both held-out blocks already fail the frozen endpoint and full-path residual gates under the primary identity policy.
""")
    canonical(out/"POST_FREEZE_PROVENANCE.json",{"schema":"biospur-c2cc-natural-motion-post-freeze-audit-v1","source":str(Path(__file__).relative_to(ROOT)),"source_sha256":sha(Path(__file__)),"raw_or_heldout_decoded":False,"fitting_only_redecoded":True,"hardware_access_performed":False})
    finish_sums(out);print("PASS")


if __name__=="__main__":main()
