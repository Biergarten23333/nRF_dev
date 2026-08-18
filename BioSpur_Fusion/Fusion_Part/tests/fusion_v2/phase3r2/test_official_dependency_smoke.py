from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
LOCK = ROOT / "config/fusion_v2/phase3r2/PHASE3R2_DEPENDENCY_LOCK.json"


def test_exact_official_vqf_and_qmt_sources_are_clean_and_executable():
    lock = json.loads(LOCK.read_text())
    dependencies = lock["dependencies"]
    python = dependencies["VQF"]["runtime_python"]
    vqf_root = dependencies["VQF"]["source_root"]
    qmt_root = dependencies["qmt"]["source_root"]
    assert subprocess.check_output(["git", "-C", vqf_root, "rev-parse", "HEAD"], text=True).strip() == dependencies["VQF"]["commit"]
    assert subprocess.check_output(["git", "-C", qmt_root, "rev-parse", "HEAD"], text=True).strip() == dependencies["qmt"]["commit"]
    assert subprocess.check_output(["git", "-C", vqf_root, "status", "--porcelain"]) == b""
    assert subprocess.check_output(["git", "-C", qmt_root, "status", "--porcelain"]) == b""
    extension = Path(python).parents[1] / "lib/python3.12/site-packages/vqf/vqf.cpython-312-x86_64-linux-gnu.so"
    assert hashlib.sha256(extension.read_bytes()).hexdigest() == dependencies["VQF"]["runtime_extension_sha256"]
    smoke = r'''import json, numpy as np, qmt
from vqf import VQF
n=401; gyro=np.zeros((n,3)); accel=np.tile([0.,0.,9.80665],(n,1))
result=VQF(gyrTs=.005,accTs=.005).updateBatchFullState(gyro,accel)
identity=qmt.qmult(np.array([1.,0.,0.,0.]),np.array([1.,0.,0.,0.]))
print(json.dumps({"count":len(result["quat6D"]),"finite":bool(np.isfinite(result["quat6D"]).all()),"qmt_identity":np.asarray(identity).tolist()},sort_keys=True))'''
    payload = json.loads(subprocess.check_output([python, "-c", smoke], text=True))
    assert payload == {"count": 401, "finite": True, "qmt_identity": [1.0, 0.0, 0.0, 0.0]}
