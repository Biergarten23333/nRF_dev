"""Adapter for the identity-verified v47 acquisition common clock.

This module reuses the validated timing manifest and TIME_EVENT_LEDGER only.
It has no dependency on the historical body estimator.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from pathlib import Path
import numpy as np

@dataclass(frozen=True)
class ClockModel:
    node: str; boot_epoch: int; a_ns_per_us: float; b_ns: float; sigma_ns: float
    first_timer_us: int; last_timer_us: int
    def map_ns(self, local_us):
        return np.rint(self.a_ns_per_us*np.asarray(local_us,dtype=np.float64)+self.b_ns).astype(np.int64)

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        while b:=f.read(4<<20): h.update(b)
    return h.hexdigest()

def load_models(path: Path) -> dict[str,ClockModel]:
    data=json.loads(path.read_text())
    if data.get("verdict")!="TIME_ALIGNMENT_PASS": raise ValueError("prior clock did not pass")
    return {n:ClockModel(n,int(m["boot_epoch"]),float(m["a_ns_per_us"]),float(m["b_ns"]),
        float(m["sigma_ns"]),int(m["first_timer_us"]),int(m["last_timer_us"]))
        for n,m in data["clock_models"].items()}

def verify_ledger_equivalence(ledger: Path, models: dict[str,ClockModel]):
    result={}; worst=0
    with np.load(ledger,allow_pickle=False) as z:
        for node,m in sorted(models.items()):
            per={}
            for kind in ("imu","uwb"):
                a=z[f"{kind}_{node}"]; inside=(a["boot_epoch"]==m.boot_epoch)&(a["node_timer_us"]>=m.first_timer_us)&(a["node_timer_us"]<=m.last_timer_us)
                diff=np.abs(m.map_ns(a["node_timer_us"][inside])-a["global_time_ns"][inside])
                maximum=int(diff.max(initial=0)); worst=max(worst,maximum)
                per[kind]={"rows":int(len(a)),"inside_domain":int(inside.sum()),"max_abs_difference_ns":maximum}
            result[node]=per
    return {"nodes":result,"maximum_timestamp_difference_ns":worst,"equivalent":worst<=1}

def build_sidecar(ledger: Path, models: dict[str,ClockModel], output: Path):
    arrays={}; counts={"imu":0,"uwb_range":0,"unmapped":0}
    imu_dtype=np.dtype([("raw_record_index","<u8"),("raw_sample_index","u1"),("common_time_ns","<i8"),
        ("common_time_sigma_ns","<u8"),("clock_epoch","<u2"),("clock_status","u1")])
    uwb_dtype=np.dtype([("raw_record_index","<u8"),("anchor_slot","u1"),("anchor_id","u1"),
        ("common_time_ns","<i8"),("common_time_sigma_ns","<u8"),("clock_epoch","<u2"),("clock_status","u1")])
    with np.load(ledger,allow_pickle=False) as z:
        for node,m in sorted(models.items()):
            src=z[f"imu_{node}"]; out=np.empty(len(src),imu_dtype)
            out["raw_record_index"]=src["raw_record_index"]; out["raw_sample_index"]=src["raw_sample_index"]
            out["common_time_ns"]=src["global_time_ns"]; out["common_time_sigma_ns"]=src["global_time_sigma_ns"]
            out["clock_epoch"]=src["boot_epoch"]; out["clock_status"]=src["status"]
            arrays[f"imu_{node}"]=out; counts["imu"]+=len(out); counts["unmapped"]+=int(np.count_nonzero(out["clock_status"]!=1))
            src=z[f"uwb_{node}"]; n=len(src)*8; out=np.empty(n,uwb_dtype)
            out["raw_record_index"]=np.repeat(src["raw_record_index"],8); out["anchor_slot"]=np.tile(np.arange(8,dtype=np.uint8),len(src))
            out["anchor_id"]=src["anchor_id"].reshape(-1); out["clock_epoch"]=np.repeat(src["boot_epoch"],8)
            status=np.repeat(src["status"],8); out["clock_status"]=status
            local=src["strobe_us"][:,None].astype(np.float64)+0.5*src["t_round_us"].astype(np.float64)
            out["common_time_ns"]=m.map_ns(local.reshape(-1)); out["common_time_sigma_ns"]=np.repeat(src["global_time_sigma_ns"],8)
            arrays[f"uwb_{node}"]=out; counts["uwb_range"]+=n; counts["unmapped"]+=int(np.count_nonzero(status!=1))
    np.savez_compressed(output,**arrays)
    return counts

