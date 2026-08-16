#!/usr/bin/env python3
"""Build deterministic row-aligned IMU time-context sidecars without value decode."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from selective_npy_time_reader import ReaderStats, iter_time_projection

CONTEXT_FIELDS = (
    "context_schema_version", "split_class", "selector_name", "view_row_index",
    "hardware_node_id", "raw_record_index", "occurrence_index_within_record",
    "boot_epoch", "node_timer_us", "common_time_ns", "common_time_sigma_ns",
    "clock_mapping_ref", "clock_mapping_valid", "clock_uncertainty_model_ref",
    "sample_age_model_ref", "time_quality_status", "source_view_sha256",
    "source_time_sidecar_sha256", "source_time_ledger_sha256",
)
KEEP_COLUMNS = frozenset({1, 5, 15, 17, 18, 22, 23, 24, 25})
NODES = ("BSF31CC","BSFC2CC","BSFAA61","BSF1120","BSFB165","BSFEC35","BSF44AD","BSF3C79","BSF6C53","BSF8BC4")


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()


def selected_csv_fields(line: bytes, wanted=KEEP_COLUMNS) -> dict[int, bytes]:
    """Return selected unquoted fields; nonselected measurement spans are not sliced."""
    out={}; start=0; index=0
    for pos,byte in enumerate(line):
        if byte in (44,10,13):
            if index in wanted: out[index]=line[start:pos]
            if byte==44:
                index+=1; start=pos+1; continue
            break
    if len(out)!=len(wanted): raise RuntimeError("view routing schema/row incomplete")
    return out


def exact_int(raw: bytes) -> int:
    value=Decimal(raw.decode("ascii"))
    if value!=value.to_integral_value():raise RuntimeError("non-integral identity/time field")
    return int(value)


def read_requests(view: Path, split: str, expected_hash: str):
    if sha256(view)!=expected_hash: raise RuntimeError(f"{split} view hash mismatch")
    requests={}; order=[]; selectors=set(); nodes=set()
    with gzip.open(view,"rb") as src:
        header=src.readline()
        if b"value_0,value_1,value_2,value_3,value_4,value_5" not in header:
            raise RuntimeError("unexpected frozen view schema")
        for row_index,line in enumerate(src):
            f=selected_csv_fields(line); node=f[5].decode("ascii"); record=int(f[1])
            timer=exact_int(f[15]); sequence=exact_int(f[17]); key=(node,record,timer,sequence)
            if key in requests: raise RuntimeError("duplicate requested join key")
            item={"split_class":f[22].decode("ascii"),"selector_name":f[23].decode("ascii"),
                  "view_row_index":row_index,"hardware_node_id":node,"raw_record_index":record,
                  "view_node_timer_us":timer,"view_sequence":sequence,"view_valid":exact_int(f[18]),
                  "view_common_time_ns":exact_int(f[24]),"view_clock_status":exact_int(f[25])}
            if item["split_class"]!=split or node not in NODES: raise RuntimeError("split/node scope violation")
            requests[key]=item;order.append(key);selectors.add(item["selector_name"]);nodes.add(node)
    return requests,order,sorted(selectors),sorted(nodes)


def build_models(clock_path: Path, clock_sha: str):
    clock=json.loads(clock_path.read_text()); models={}
    for node in NODES:
        model=clock["clock_models"][node]; boot=int(model["boot_epoch"]); ref=f"{node}/boot-{boot}/segment-0"
        models[ref]={"hardware_node_id":node,"boot_epoch":boot,"clock_segment":0,
          "mapping_ref":f"sha256:{clock_sha}#/clock_models/{node}",
          "valid_timer_domain_us":[int(model["first_timer_us"]),int(model["last_timer_us"])],
          "a_ns_per_us":float(model["a_ns_per_us"]),"b_ns":float(model["b_ns"]),
          "mapping_valid":True,"residual_uncertainty":{"representation":"ROBUST_SIGMA_BOUND_REFERENCE","sigma_ns":float(model["sigma_ns"]),"clean_residual_max_us":float(model["clean_residual_max_us"]),"clean_residual_p95_us":float(model["clean_residual_p95_us"])},
          "sample_age_model":{"support_us":[0,5000],"distribution":"UNKNOWN_BOUNDED","fixed_delay_forbidden":True}}
    return {"schema":"biospur-phase0-input-time-uncertainty-v1","source_common_clock_sha256":clock_sha,"models":models}


def deterministic_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj,sort_keys=True,indent=2)+"\n")


def write_context(path: Path, rows: list[dict]) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0,compresslevel=9) as out:
            out.write((",".join(CONTEXT_FIELDS)+"\n").encode())
            for row in rows:
                out.write((",".join(str(row[k]) for k in CONTEXT_FIELDS)+"\n").encode())


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--config",required=True);ap.add_argument("--output-dir",required=True);a=ap.parse_args()
    cfg=json.loads(Path(a.config).read_text()); out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    for name,src in cfg["sources"].items():
        if sha256(Path(src["realpath"]))!=src["sha256"]:raise RuntimeError(f"source hash mismatch: {name}")
    models=build_models(Path(cfg["sources"]["common_clock"]["realpath"]),cfg["sources"]["common_clock"]["sha256"])
    model_tmp=out/"TIME_UNCERTAINTY_MODELS.json";deterministic_json(model_tmp,models);model_hash=sha256(model_tmp)
    ledger=Path(cfg["sources"]["time_ledger"]["realpath"]); results={}; total_stats={}
    for split in ("D1","D2"):
        view_cfg=cfg["views"][split]; requests,order,selectors,nodes=read_requests(Path(view_cfg["realpath"]),split,view_cfg["sha256"])
        matched={}; aggregate=ReaderStats()
        for node in NODES:
            stats=ReaderStats()
            for row in iter_time_projection(ledger,f"imu_{node}",chunk_size=cfg["opaque_scratch_bytes"],stats=stats):
                key=(node,row["raw_record_index"],row["node_timer_us"],row["sequence"])
                req=requests.get(key)
                if req is None:continue
                if key in matched:raise RuntimeError("one-to-many ledger join")
                if row["node_timer_us"]!=req["view_node_timer_us"] or row["sequence"]!=req["view_sequence"]:
                    raise RuntimeError(f"identity/time mismatch key={key} ledger_timer={row['node_timer_us']} view_timer={req['view_node_timer_us']} ledger_sequence={row['sequence']} view_sequence={req['view_sequence']}")
                if row["global_time_ns"]!=req["view_common_time_ns"]:raise RuntimeError("common time difference nonzero")
                if row["status"]!=1 or req["view_clock_status"]!=1 or req["view_valid"]!=1:raise RuntimeError("invalid joined time row")
                ref=f"{node}/boot-{row['boot_epoch']}/segment-0"; model=models["models"].get(ref)
                if model is None:raise RuntimeError("missing boot/mapping model")
                lo,hi=model["valid_timer_domain_us"]
                if not lo<=row["node_timer_us"]<=hi:raise RuntimeError("timer domain violation")
                matched[key]={"context_schema_version":"biospur-imu-time-context-v1",**{k:req[k] for k in ("split_class","selector_name","view_row_index","hardware_node_id","raw_record_index")},
                  "occurrence_index_within_record":row["raw_sample_index"],
                  "boot_epoch":row["boot_epoch"],"node_timer_us":row["node_timer_us"],"common_time_ns":row["global_time_ns"],"common_time_sigma_ns":row["global_time_sigma_ns"],
                  "clock_mapping_ref":model["mapping_ref"],"clock_mapping_valid":"true","clock_uncertainty_model_ref":f"sha256:{model_hash}#/models/{ref}",
                  "sample_age_model_ref":f"sha256:{model_hash}#/models/{ref}/sample_age_model","time_quality_status":"VALID",
                  "source_view_sha256":view_cfg["sha256"],"source_time_sidecar_sha256":cfg["sources"]["time_sidecar"]["sha256"],"source_time_ledger_sha256":cfg["sources"]["time_ledger"]["sha256"]}
            for field in vars(aggregate):setattr(aggregate,field,getattr(aggregate,field)+getattr(stats,field))
            aggregate.max_opaque_scratch_bytes=max(aggregate.max_opaque_scratch_bytes,stats.max_opaque_scratch_bytes)
        missing=set(requests)-set(matched);extra=set(matched)-set(requests)
        if missing or extra:raise RuntimeError(f"join cardinality failure {split}: missing={len(missing)} extra={len(extra)}")
        rows=[matched[k] for k in order]; tmp=out/f"{split}_IMU_TIME_CONTEXT.tmp.csv.gz";write_context(tmp,rows);h=sha256(tmp);dest=out/f"{split}_IMU_TIME_CONTEXT_{h}.csv.gz";os.replace(tmp,dest)
        results[split]={"realpath":str(dest.resolve()),"sha256":h,"rows":len(rows),"nodes":nodes,"selectors":selectors,"common_time_max_difference_ns":0}
        total_stats[split]=vars(aggregate)
    report={"schema":"biospur-phase0-input-context-build-v1","contexts":results,"time_uncertainty_models":{"realpath":str(model_tmp.resolve()),"sha256":model_hash},"reader_stats":total_stats,
      "join_key":["hardware_node_id","raw_record_index","node_timer_us","sequence"],"D3_measurement_numeric_decodes":0,"D3_measurement_arrays":0,"measurement_fields_retained":0,"measurement_values_logged":0}
    deterministic_json(out/"BUILD_REPORT.json",report);print(json.dumps(report,sort_keys=True));

if __name__=="__main__":main()
