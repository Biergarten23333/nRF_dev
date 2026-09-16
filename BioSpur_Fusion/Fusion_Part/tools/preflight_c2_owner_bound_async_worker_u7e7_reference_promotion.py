#!/usr/bin/env python3
"""No-raw schema/count/hash preflight for the U7E7 current-contract reference."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import resource
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC = ROOT / "logs/c2_owner_bound_async_worker_u7e7_reference_diagnostic_20260906T235000Z"
DIAGNOSTIC_SEAL = "69035426c90a212e1673150c970544a6b2bd0d73674ee7abf723775cd13fcb1f"
CANDIDATE = DIAGNOSTIC / "CURRENT_REFERENCE_CANDIDATE_NON_PROMOTED.jsonl"
CANDIDATE_SHA256 = "28e0981ce2664a8951a1dd059f958b2b832b661f0ec87f7452bbbe699ed82700"
DIAGNOSTIC_TOOL = ROOT / "tools/diagnose_c2_owner_bound_async_worker_u7e7_reference.py"
DIAGNOSTIC_TOOL_SHA256 = "2ddc1cd8420134797bdffeebe20da3566497f20b2b2550ca9b35857b452535d7"
REVISION_004 = ROOT / "logs/c2_owner_bound_async_worker_u7e7_action04_revision_004_20260906T233000Z"
REVISION_004_SEAL = "8cb643c049620c250fe1bf1adedf706089de7b63ec9945cdd220e4acef873098"
OLD_U3 = ROOT / "logs/c2_offline_unified_u3_20260906T160900Z"
OLD_U5B = ROOT / "logs/c2_tight_range_u5b_revision_002_action04_first5s_20260906T144646Z"
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 5_000_000
EXPECTED_REVISION_004_SOURCE_HASHES = {
    "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py": "f3f96f40f2cad9f433ba108676271ca43f92df3c0317dca06734f3d9ab46c265",
    "src/biospur_fusion/c2_uwb_root_world/offline_unified_wiring.py": "71c1d1a20bc8aa882f14ae91ef64d60b9655da9561d4b46cb7f683629b01ae9b",
    "src/biospur_fusion/c2_uwb_root_world/owner_bound_async_worker.py": "9b1e09dd8c56492d4f43e90192b2d838af71d1c82838fe0082b54a73b0671f1e",
    "src/biospur_fusion/c2_uwb_root_world/root_worker_owner_wiring.py": "e219ee769b3224123d686b0ba90da935fbfc737e5c071e746cfdd79b29120de7",
    "src/biospur_fusion/c2_uwb_root_world/tight_range.py": "334989bd297e70b6a622693f1651102a1f5a28449cc5f3af436d96f6d3271914",
    "tests/test_c2_owner_bound_async_worker.py": "35ea665b4ef3ec83f652235e964ec08da5fc10dda8fd6fe66f58f1d2f96c5977",
    "tools/run_c2_owner_bound_async_worker_u7e4_action04.py": "c5c4978a618405bd418569503baf8a973b02e87f7202d2556c6d9fc83c73d78e",
    "tools/run_c2_owner_bound_async_worker_u7e6_action04.py": "b7812d84825f38c809cbbf11cfe965a99f00c49e1b61b84b068a76352cd82c36",
    "tools/run_c2_owner_bound_async_worker_u7e7_action04.py": "468adf8fccd1205e82b33c29ab5d60b94ee13d9d3a8228455e8c8708e2f2039b",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_seal(path: Path, expected: str) -> None:
    if sha256(path / "SHA256SUMS") != expected:
        raise RuntimeError(f"seal digest mismatch: {path}")
    for line in (path / "SHA256SUMS").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        if sha256(path / relative) != digest:
            raise RuntimeError(f"seal member mismatch: {path / relative}")


def seal(path: Path) -> str:
    members = sorted(item for item in path.iterdir() if item.is_file() and item.name != "SHA256SUMS")
    (path / "SHA256SUMS").write_text("".join(f"{sha256(item)}  {item.name}\n" for item in members))
    return sha256(path / "SHA256SUMS")


def finite_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    args.output.mkdir(parents=True); started = time.perf_counter()
    command = ("timeout --signal=TERM --kill-after=5s 60s env PYTHONPATH=src:tools:. .venv-v0/bin/python "
        f"tools/preflight_c2_owner_bound_async_worker_u7e7_reference_promotion.py --output {args.output}")
    (args.output / "COMMAND.txt").write_text(command + "\n")
    base = {"schema":"biospur.c2.u7e7.reference-promotion-preflight.v1","command":command,
        "raw_opened":False,"dataset_opened":False,"action04_capture_opened":False,"HXX_opened":False,
        "async_worker_used":False,"solver_run":False,"candidate_promoted":False,
        "calibrated_R":False,"scientific_pass":False,"product_ready":False,"production_ready":False,
        "limits":{"wall_s":60,"rss_kib":RSS_CAP_KIB,"evidence_bytes":EVIDENCE_CAP_BYTES}}
    try:
        verify_seal(DIAGNOSTIC,DIAGNOSTIC_SEAL);verify_seal(REVISION_004,REVISION_004_SEAL)
        if sha256(CANDIDATE)!=CANDIDATE_SHA256:raise RuntimeError("candidate hash mismatch")
        if sha256(DIAGNOSTIC_TOOL)!=DIAGNOSTIC_TOOL_SHA256:raise RuntimeError("diagnostic tool hash mismatch")
        diagnostic=json.loads((DIAGNOSTIC/"RESULT.json").read_text())
        if (diagnostic.get("status")!="CURRENT_CONTRACT_REFERENCE_CANDIDATE_NON_PROMOTED"
                or diagnostic.get("candidate_promoted") is not False
                or diagnostic.get("old_mismatch_groups")!=[40]
                or diagnostic.get("scalar_batch_global_max",float("inf"))>1e-12):
            raise RuntimeError("diagnostic qualification mismatch")
        revision_contract=json.loads((REVISION_004/"CONTRACT.json").read_text())
        revision_failure=json.loads((REVISION_004/"FAILURE.json").read_text())
        if revision_contract.get("source_hashes")!=EXPECTED_REVISION_004_SOURCE_HASHES:
            raise RuntimeError("revision_004 execution source hashes changed")
        if (revision_failure.get("failure_type")!="RuntimeError"
                or not str(revision_failure.get("failure_message","")).startswith("sealed reference mismatch:")):
            raise RuntimeError("revision_004 did not reach terminal numeric mismatch")
        old_u3=[json.loads(line) for line in (OLD_U3/"GROUPS.jsonl").read_text().splitlines()]
        candidate=[json.loads(line) for line in CANDIDATE.read_text().splitlines()]
        if len(candidate)!=41 or len(old_u3)!=41:raise RuntimeError("group count mismatch")
        output_path=args.output/"CURRENT_CONTRACT_REFERENCE.jsonl"
        node_count=0;weights_count=0
        with output_path.open("w") as destination:
            for index,(current,historical) in enumerate(zip(candidate,old_u3)):
                if set(current)!={"group","root_state","nodes"} or current["group"]!=index:
                    raise RuntimeError(f"candidate group schema mismatch: {index}")
                root=current["root_state"]
                if len(root)!=6 or not all(finite_number(value) for value in root):
                    raise RuntimeError(f"root state schema mismatch: {index}")
                nodes=current["nodes"]
                if len(nodes)!=10 or len({row.get("node") for row in nodes})!=10:
                    raise RuntimeError(f"node inventory mismatch: {index}")
                qualified_nodes=[]
                for row in nodes:
                    if set(row)!={"node","decision","rank","prior_nis","condition","weights"}:
                        raise RuntimeError(f"node schema mismatch: {index}")
                    decision=row["decision"]
                    if (not isinstance(row["node"],str) or len(decision)!=3 or decision[0]!=row["node"]
                            or not isinstance(decision[1],bool) or not isinstance(decision[2],str)
                            or isinstance(row["rank"],bool) or not isinstance(row["rank"],int)
                            or not finite_number(row["prior_nis"]) or not finite_number(row["condition"])
                            or len(row["weights"])!=8 or not all(finite_number(value) for value in row["weights"])):
                        raise RuntimeError(f"node field mismatch: {index}/{row.get('node')}")
                    qualified_nodes.append(row);node_count+=1;weights_count+=8
                record={"schema":"biospur.c2.u7e7.current-contract-reference.v1","group":index,
                    "root_state_fields":["x","y","z","vx","vy","vz"],"root_state":root,
                    "transaction_reason":historical["transaction_reason"],"link_count":int(historical["links"]),
                    "discrete_field_provenance":{
                        "source":"historical U3 discrete fields only",
                        "revision_004_check":"PASSED_BEFORE_TERMINAL_NUMERIC_MISMATCH",
                        "revision_004_seal":REVISION_004_SEAL},"nodes":qualified_nodes}
                destination.write(json.dumps(record,sort_keys=True,separators=(",",":"))+"\n")
        if node_count!=410 or weights_count!=3280:raise RuntimeError("reference field count mismatch")
        reference_sha=sha256(output_path)
        historical={"old_u3_numeric":"HISTORICAL_PRE_CONTEXT_TAIL_NON_PROMOTED",
            "old_u5b_numeric":"HISTORICAL_PRE_CONTEXT_TAIL_NON_PROMOTED",
            "old_u3_discrete_fields":"REUSED_ONLY_AFTER_REVISION_004_DISCRETE_CHECK_PASSED"}
        all_failure_seals={key:value for key,value in diagnostic["bound_seals"].items()
            if "action04" in key or "u7e6" in key or "u7e7" in key}
        manifest={"schema":"biospur.c2.u7e7.current-contract-reference-manifest.v1",
            "reference_path":output_path.name,"reference_sha256":reference_sha,
            "source_candidate_sha256":CANDIDATE_SHA256,"diagnostic_tool_sha256":DIAGNOSTIC_TOOL_SHA256,
            "diagnostic_seal":DIAGNOSTIC_SEAL,"revision_004_seal":REVISION_004_SEAL,
            "revision_004_execution_source_hashes":EXPECTED_REVISION_004_SOURCE_HASHES,
            "counts":{"groups":41,"root_states":41,"nodes":node_count,"node_weight_vectors":410,"weights":weights_count},
            "historical_numeric_seals":historical,"preserved_failure_seals":all_failure_seals,
            "candidate_promoted":False,"promotion_requires_monitor":True}
        (args.output/"REFERENCE_MANIFEST.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
        gates={"diagnostic_seal":True,"candidate_hash":True,"diagnostic_tool_hash":True,
            "revision_004_seal":True,"revision_004_source_hashes":True,
            "revision_004_discrete_before_numeric":True,"schema":True,"counts":True,
            "no_raw_dataset_hxx_async_solver":True,"wall":time.perf_counter()-started<60,
            "rss":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)<RSS_CAP_KIB}
        status="READY_FOR_MONITOR_CURRENT_CONTRACT_REFERENCE_PROMOTION" if all(gates.values()) else "BLOCKED_REFERENCE_PROMOTION_PREFLIGHT"
        result={**base,"status":status,"gates":gates,"reference_sha256":reference_sha,
            "counts":manifest["counts"],"historical_numeric_seals":historical,
            "preserved_failure_seals":all_failure_seals,"revision_004_execution_source_hashes":EXPECTED_REVISION_004_SOURCE_HASHES,
            "wall_s":time.perf_counter()-started,"maximum_rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)}
        (args.output/"RESULT.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        (args.output/"REPORT.md").write_text(f"# U7E7 current-contract reference promotion preflight\n\nStatus: `{status}`. "
            "The complete 41-group/410-node reference passed schema, count, and immutable hash checks. "
            "Old U3/U5B numeric seals remain historical pre-context-tail and non-promoted. "
            "No dataset/raw capture, HXX, async worker, or solver was opened or run. Promotion remains a monitor decision.\n")
        if sum(item.stat().st_size for item in args.output.iterdir() if item.is_file())>=EVIDENCE_CAP_BYTES:
            raise RuntimeError("evidence cap exceeded")
        digest=seal(args.output);print(json.dumps({"status":status,"seal_sha256":digest,
            "reference_sha256":reference_sha,"wall_s":result["wall_s"],"rss_kib":result["maximum_rss_kib"]},sort_keys=True))
        return 0 if all(gates.values()) else 2
    except BaseException as exc:
        failure={**base,"status":"BLOCKED_REFERENCE_PROMOTION_PREFLIGHT","failure":f"{type(exc).__name__}: {exc}",
            "traceback":traceback.format_exc(),"wall_s":time.perf_counter()-started,
            "maximum_rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)}
        (args.output/"FAILURE.json").write_text(json.dumps(failure,indent=2,sort_keys=True)+"\n")
        digest=seal(args.output);print(json.dumps({"status":failure["status"],"seal_sha256":digest,"failure":str(exc)},sort_keys=True));return 2


if __name__=="__main__":
    raise SystemExit(main())
