#!/usr/bin/env python3
"""Validate frozen inputs and emit a preregistration manifest (host-only)."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from .contract import CalibrationContract
from .state_machine import ACTIONS, ActionMachine

ROOT=Path(__file__).resolve().parents[3]
DEPLOY=ROOT/"B306_Part/deployments/current_room_autopos_20260811_183541"

def main():
    p=argparse.ArgumentParser();p.add_argument("--slots",type=Path,required=True);p.add_argument("--out",type=Path,required=True);a=p.parse_args()
    contract=CalibrationContract(DEPLOY/"V4IO_LAYOUT.json",DEPLOY/"CAPTURE_BOUND_GEOMETRY_MANIFEST.json",a.slots).validate()
    result={"schema":"biospur-ten-node-body-preregistration-v1","contract":contract,
      "formal_duration_guidance_s":sum(x[2] for x in ACTIONS),"formal_max_s":120,
      "fit_validation_partition":ActionMachine().frozen_partition(),
      "validation_is_untouched":True,"global_yaw":"GAUGE_FREEDOM",
      "production_imu_policy":{"accelerometer_matrix":"IDENTITY","shared_accelerometer_bias":0,
       "session_gyro_bias":"INITIAL_STILL","device_specific_calibration":False,
       "q1_guards":["norm/NIS rejection","gyro-bias tracking","quaternion normalization/sign continuity","covariance Cholesky"]},
      "forbidden_ground_truth":["IMU_DOUBLE_INTEGRATION","MANUAL_DIRECTION","MANUAL_DISTANCE","MANUAL_ANGLE","MANUAL_SPEED","T_POSE_WORLD_DIRECTION"]}
    a.out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("PREREGISTRATION_READY")
if __name__=="__main__":main()
