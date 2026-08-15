"""Canonical artifacts and strict IMU-only ledger loading."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any, Mapping
import zipfile

import numpy as np


def sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block:=handle.read(4<<20):digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    def native(item):
        if isinstance(item,dict):return {str(key):native(val) for key,val in item.items()}
        if isinstance(item,(list,tuple)):return [native(val) for val in item]
        if isinstance(item,np.integer):return int(item)
        if isinstance(item,np.floating):return float(item)
        if isinstance(item,np.bool_):return bool(item)
        return item
    return (json.dumps(native(value),sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)+"\n").encode()


def dump_json(path: Path,value: Any) -> None:
    Path(path).write_bytes(canonical_json_bytes(value))


def dump_json_atomic(path: Path,value: Any) -> None:
    """Strict JSON write with fsync and same-directory atomic replacement."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+".tmp")
    payload=canonical_json_bytes(value)
    with temporary.open("wb") as handle:
        handle.write(payload);handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,path)
    directory_fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory_fd)
    finally:os.close(directory_fd)


def savez_deterministic(path:Path,arrays:Mapping[str,np.ndarray]) -> None:
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for key in sorted(arrays):
            payload=io.BytesIO();np.lib.format.write_array(payload,np.asarray(arrays[key]),allow_pickle=False);info=zipfile.ZipInfo(f"{key}.npy",date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_STORED;info.external_attr=0o600<<16;archive.writestr(info,payload.getvalue())


def load_calibration_ledger(path:Path,gates:Mapping[str,Any]) -> tuple[dict[str,np.ndarray],dict[str,tuple[int,int]],dict]:
    path=Path(path)
    if path.name!="CALIBRATION_TYPED_LEDGER.npz" or any(token in str(path).lower() for token in ("heldout","walk","final_still")):raise ValueError("calibration-only typed ledger required")
    required=set(gates["allowed_npz_keys"]);arrays={};opened=[]
    with np.load(path,allow_pickle=False) as source:
        available=set(source.files)
        if not required<=available:raise ValueError(f"missing keys: {sorted(required-available)}")
        for key in sorted(required):arrays[key]=source[key].copy();opened.append(key)
    forbidden=[key for key in opened if key.startswith("uwb_") or "t4" in key.lower()]
    if forbidden:raise RuntimeError(f"forbidden modalities opened: {forbidden}")
    rows=arrays.pop("action_windows");windows={str(r["name"]):(int(r["start_ns"]),int(r["stop_ns"])) for r in rows}
    if set(windows)!=set(gates["calibration_actions"]):raise ValueError(f"calibration action mismatch: {sorted(windows)}")
    imus={key.removeprefix("imu_"):value for key,value in arrays.items()}
    audit={"schema":"biospur-preview-data-access-audit-v0","path":str(path.resolve()),"sha256":sha256(path),"opened_npz_keys":opened,"available_key_count":len(available),"forbidden_modalities_opened":forbidden,"uwb":False,"t4":False,"anchor":False,"operator_measurements":False,"walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED","golf_swing":"SEALED_PENDING_CALIBRATION_PASS","boxing":"SEALED_PENDING_CALIBRATION_PASS"}
    return imus,windows,audit
