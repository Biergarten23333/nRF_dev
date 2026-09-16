#!/usr/bin/env python3
"""Synthetic-only preflight for offline U4 root/pose/contact sequencing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import time

import numpy as np

from biospur_fusion.c2_uwb_root_world.ankle_contact import (
    DualFootFootholdCorrector, FootContactEvidence, FootSupportState,
    positive_swing_cues,
)
from biospur_fusion.root_r3.models import RootState

ROOT = Path(__file__).resolve().parents[1]
U3 = ROOT / "logs/c2_offline_unified_u3_20260906T160900Z"
U3_CORRECTION = ROOT / "logs/c2_offline_unified_u3_20260906T160900Z_provenance_correction"
U3_SEAL = "938862a8c8d6daacd2c3c894865e3373eec2ae159a2a1f8f27e5c8d5559b2640"
U3_CORRECTION_SEAL = "3faa2fe70872818877a2545a828a55580d3276a53de908d19cb337bdaca9035b"
MAXIMUM_RSS_KIB = 300_000


def sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""): digest.update(block)
    return digest.hexdigest()


def write_json(path: Path,value) -> None:
    path.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")


def seal(path: Path) -> str:
    lines=[]
    for item in sorted(p for p in path.rglob("*") if p.is_file() and p.name!="SHA256SUMS"):
        lines.append(f"{sha256(item)}  {item.relative_to(path)}")
    target=path/"SHA256SUMS"; target.write_text("\n".join(lines)+"\n")
    return sha256(target)


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    if sha256(U3/"SHA256SUMS")!=U3_SEAL or sha256(U3_CORRECTION/"SHA256SUMS")!=U3_CORRECTION_SEAL:
        raise RuntimeError("sealed U3 evidence binding changed")
    args.output.mkdir(parents=True); started=time.perf_counter()
    command=("timeout 120s env PYTHONPATH=src:tools:. .venv-v0/bin/python "
             "tools/preflight_c2_offline_unified_u4.py --output "+str(args.output))
    test_command=[str(ROOT/".venv-v0/bin/python"),"-m","pytest","-q",
                  "tests/test_c2_hinge_temporal.py",
                  "tests/test_c2_causal_articulated_pose.py",
                  "tests/test_c2_causal_update_transaction.py",
                  "tests/test_c2_offline_unified_contact_wiring.py",
                  "tests/test_c2_ankle_contact.py",
                  "tests/test_c2_calibration_shared_root_imu_fusion.py"]
    completed=subprocess.run(test_command,cwd=ROOT,text=True,capture_output=True,timeout=90,
                             env={**__import__("os").environ,"PYTHONPATH":"src:tools:."})
    (args.output/"TESTS.stdout").write_text(completed.stdout)
    (args.output/"TESTS.stderr").write_text(completed.stderr)
    if completed.returncode: raise RuntimeError("focused U4 tests failed")
    sources=[
        ROOT/"src/biospur_fusion/c2_uwb_root_world/ankle_contact.py",
        ROOT/"src/biospur_fusion/c2_uwb_root_world/offline_unified_contact_wiring.py",
        ROOT/"tools/run_c2_h01_shared_root_imu_fusion.py",
        ROOT/"tests/test_c2_offline_unified_contact_wiring.py",
        ROOT/"tests/test_c2_ankle_contact.py",Path(__file__),
    ]
    forbidden=("decode_measurements","decode_uwb_only","H01_boxing","H02_golf",
               "solve_articulated_ranges","CandidateKind.ARTICULATED_IK")
    wiring_source=sources[1].read_text()
    if any(word in wiring_source for word in forbidden):
        raise RuntimeError("U4 owner imports a forbidden data/articulated path")
    # Diagnostic pure-cue cost only; transaction behavior is owned by fixtures.
    corrector=DualFootFootholdCorrector(); state=RootState(1.,np.r_[[0.,0.,1.],np.zeros(6)],np.eye(9)*.1)
    evidence={side:FootContactEvidence(side,1.,.9,True,.01,.01,0.,"FIXTURE",
                 support_state=FootSupportState.STANCE_CONFIRMED.value)
              for side in ("left","right")}
    offsets={"left":np.array([-.1,0.,-1.]),"right":np.array([.1,0.,-1.])}
    corrector.update(state,evidence=evidence,ankle_offset_world_m=offsets,
                     ankle_offset_velocity_world_mps={side:np.zeros(3) for side in offsets})
    samples=[]
    for _ in range(20_000):
        tick=time.perf_counter_ns()
        positive_swing_cues(query_time_s=1.,analytic_ankle_offset_world_m=offsets,
            root_state=state,foothold_corrector=corrector,evidence=evidence,
            maximum_root_age_s=.0075,positive_swing_height_m=.075)
        samples.append((time.perf_counter_ns()-tick)*1e-6)
    rss=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    result={
        "status":"FIXTURE_ONLY_OFFLINE_U4_QUALIFIED_ONLINE_BLOCKED",
        "execution_class":"SYNTHETIC_FIXTURE_ONLY","scientific_pass":False,
        "production_ready":False,"online_ready":False,"online_status":"ONLINE_BLOCKED",
        "raw_opened":False,"HXX_opened":False,"action04_opened":False,
        "articulated_transaction_used":False,"pose_install_used":False,
        "profile_owner":"EXPLICIT_SYNTHETIC_FOOT_STILLNESS_PROFILE_NO_FITTING",
        "u3_original_seal_sha256":U3_SEAL,
        "u3_correction_seal_sha256":U3_CORRECTION_SEAL,
        "latency_debt_ms":{"u3_group_p99":136.9203696143814,
            "u3_imu_advance_p99":11.326844617724424,"u2_root_p99":8.032351,
            "u2_articulated_p99_not_executed":350.311878},
        "bilateral_cue_microbenchmark_ms":{"calls":len(samples),
            "p50":float(np.percentile(samples,50)),"p99":float(np.percentile(samples,99)),
            "max":float(max(samples)),"diagnostic_only":True},
        "tests":{"command":" ".join(test_command),"returncode":completed.returncode,
                 "stdout":completed.stdout.strip()},
        "wall_s":time.perf_counter()-started,"maximum_rss_kib":rss,
    }
    write_json(args.output/"RESULT.json",result)
    write_json(args.output/"PROVENANCE.json",{"command":command,
        "source_hashes":{str(path.relative_to(ROOT)):sha256(path) for path in sources},
        "u3_numerical_artifacts_unchanged":True})
    if rss>=MAXIMUM_RSS_KIB: raise RuntimeError("U4 synthetic RSS gate failed")
    digest=seal(args.output)
    print(json.dumps({"status":result["status"],"rss_kib":rss,"wall_s":result["wall_s"],
                      "seal_sha256":digest},sort_keys=True))
    return 0


if __name__=="__main__": raise SystemExit(main())
