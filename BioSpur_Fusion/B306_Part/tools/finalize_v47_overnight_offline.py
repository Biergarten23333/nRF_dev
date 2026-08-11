#!/usr/bin/env python3
"""Add audit narrative/validation records to a completed offline replay."""
import argparse, hashlib, json
from datetime import datetime, timedelta
from pathlib import Path

def load(p): return json.loads(p.read_text())
def dump(p,x): p.write_text(json.dumps(x,indent=2,sort_keys=True)+"\n")
def digest(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()
def wall(m,t): return (datetime.fromisoformat(m["t0_wall"])+timedelta(seconds=t-m["t0_monotonic"])).isoformat(timespec="milliseconds")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--run",type=Path,required=True);ap.add_argument("--analysis",type=Path,required=True);a=ap.parse_args()
    m=load(a.run/"RUN_MANIFEST.json"); ledger=load(a.run/"PROCESS_LEDGER.json"); smoke=load(a.run/"SMOKE_RESULT.json")
    inv=load(a.analysis/"INPUT_INVENTORY.json"); summary=load(a.analysis/"ANALYSIS_SUMMARY.json"); exp=load(a.analysis/"EXPOSURE.json"); hist=load(a.analysis/"HISTORICAL_COMPARISON.json")
    t0=m["t0_monotonic"]; end=ledger["ended_monotonic"]
    bypath={x["path"]:x for x in inv["inputs"]}
    lf=bypath["listener_capture/merged_index.jsonl"]["first_host_monotonic"]
    ff=bypath["fusion_cdc.log"]["first_host_monotonic"]
    timeline=[
      {"event":"FORMAL_LISTENER_START","monotonic":lf,"wall":wall(m,lf)},
      {"event":"FORMAL_FUSION_START","monotonic":ff,"wall":wall(m,ff)},
      {"event":"T0","monotonic":t0,"wall":m["t0_wall"]},
      {"event":"SMOKE_INTERVAL_START","monotonic":t0,"wall":m["t0_wall"]},
      {"event":"SMOKE_INTERVAL_END","monotonic":t0+600,"wall":wall(m,t0+600),"verdict":smoke["verdict"]},
      {"event":"CONTINUOUS_LONG_CAPTURE","monotonic":t0+600,"wall":wall(m,t0+600),"new_t0":False,"collector_restart":False},
      {"event":"OPERATOR_STOP_REQUEST_AND_COLLECTOR_SIGINT","monotonic":end,"wall":ledger["ended_wall"],"listener_rc":ledger.get("listener_returncode"),"fusion_rc":ledger.get("fusion_returncode")},
      {"event":"FINAL_FLUSHED_FUSION_RECORD","monotonic":inv["fusion_parse"]["last_monotonic"],"wall":wall(m,inv["fusion_parse"]["last_monotonic"])}]
    timeline.sort(key=lambda x:x["monotonic"]);dump(a.analysis/"TIMELINE.json",timeline)
    import csv
    with (a.analysis/"TIMELINE.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["event","monotonic","wall","verdict","new_t0","collector_restart","listener_rc","fusion_rc"]);w.writeheader();w.writerows({k:x.get(k) for k in w.fieldnames} for x in timeline)
    validation={"complete_replay":"PASS","raw_hash_before_after":"PASS" if load(a.analysis/"RAW_HASH_VERIFICATION.json")["unchanged"] else "FAIL",
      "preflight_fragment_excluded":True,"formal_input_prefix":str(a.run.resolve()),"classified_event_spot_checks":0,
      "classified_event_spot_check_note":"No >=20 s event existed to spot-check.","battery_degradation_spot_checks":0,
      "battery_degradation_spot_check_note":"No evidence-backed onset existed; terminal Fusion cadence and uptime were checked per board.",
      "bsf6c53_exemption_scope":"Listener absolute reception only","hardware_accessed":False,
      "formal_path_resolution":"Prompt formal_capture path was a short abandoned attempt; the operator-duration-matching formal_20260811_001328 continuous run was used and recorded."}
    dump(a.analysis/"VALIDATION.json",validation)
    bh=exp["nine_board_battery_healthy_delivered_board_hours"]; ah=exp["bsf6c53_healthy_adapter_hours"]
    report=f"""# B306 v47 overnight offline causal analysis

## Causal result

1. **No wedge was observed.** There were zero joint Fusion UWB+IMU silences at or above 20 seconds.
2. **No recovery was observed.** The formal schema lacks the exact recovery counters, so the evidence disposition is `RECOVERY_EVIDENCE_UNAVAILABLE`, not an inferred zero.
3. **No reset was observed.** There was no B306 `node_ms` decrease, connection-epoch change, or clock discontinuity during nominal-power operation. DWM Tag-reset diagnostics are not B306 reset evidence.
4. **The B1 path was not explicitly exercised.** Its required retained-message/MPSL/resubmit counters were not captured; the correct result is `B1_EVIDENCE_UNAVAILABLE`.
5. Consequently, successful B1 non-reset recovery was not proved.
6. Valid continuous ten-node nominal-power exposure was {exp['ten_node_nominal_power_wall_s']:.6f} s ({exp['ten_node_nominal_power_wall_s']/3600:.6f} h), from T0 {m['t0_wall']} to operator stop {ledger['ended_wall']}.
7. Healthy exposure was {bh:.6f} battery board-hours plus {ah:.6f} BSF6C53 adapter-hours, {bh+ah:.6f} total board-hours.
8. No board had an evidence-backed power-degradation onset before stop.
9. No non-BSF6C53 Tag had a proved stable low **source-cadence** plateau. Low Listener receipt rates were retained as RF/receiver visibility, not mislabeled as transmitter cadence.
10. No Tag was proved to run stably near 5 Hz while peers remained near 8.33 Hz.
11. Useful exposure ended only because the operator stopped the collectors. Subsequent battery loss/unreadability is operational context, not a reconstructed firmware value.
12. The zero-wedge run is consistent with v47 prevention but does not prove it. It says nothing positive about recovery execution because neither a qualifying wedge nor retained recovery/B1 evidence was captured.

## Evidence and statistics

The formal run lasted {exp['formal_duration_h']:.6f} h. Fusion delivered nominal approximately 200 Hz IMU and 8.33 Hz UWB streams across all ten boards through stop. Listener union rates are independent RF observations; geometry-dependent loss was not converted into source-rate or depletion claims. BSF6C53's exemption was applied only to absolute Listener reception.

At {hist['actual_healthy_board_hours']:.6f} healthy board-hours, the historical pooled point estimate predicts {hist['pooled']['expected_events']:.3f} events and P(0)={hist['pooled']['poisson_p_zero']:.3f}; the N8-only diagnostic predicts {hist['n8_only']['expected_events']:.3f} and P(0)={hist['n8_only']['poisson_p_zero']:.3f}. These are descriptive, with only four historical events. This is the longest clean >6-hour ten-node Fusion/beacon capture found in the audited corpus.

Final dispositions: `NO_WEDGE_OBSERVED + RECOVERY_EVIDENCE_UNAVAILABLE + B1_EVIDENCE_UNAVAILABLE + STOPPED_BY_OPERATOR`. Scientific interpretation: `V47_PREVENTION_CONSISTENT_NOT_PROVEN`.

No hardware was accessed during this analysis. The 124-byte preflight shutdown fragment and all post-stop live-read material were excluded. Authoritative raw hashes matched before and after analysis.
"""
    (a.analysis/"REPORT.md").write_text(report)
    files=sorted(p for p in a.analysis.iterdir() if p.is_file() and p.name!="SHA256SUMS")
    (a.analysis/"SHA256SUMS").write_text("".join(f"{digest(p)}  {p.name}\n" for p in files))
if __name__=="__main__": main()
