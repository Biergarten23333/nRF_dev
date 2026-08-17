from __future__ import annotations
import json

D3={"golf_swing","boxing"}; D2={"walk","final_still"}
def load_action_events(path):
    rows=[]
    with open(path,encoding="utf-8") as f:
        for line in f:
            if line.strip():rows.append(json.loads(line))
    return rows

def scientific_windows(rows):
    starts={}; out=[]
    for row in rows:
        action=row.get("action"); event=row.get("event")
        if event=="ACTION_START":starts[(action,row.get("attempt"))]=row
        if event=="ACTION_STOP" and action not in D2|D3:
            key=(action,row.get("attempt")); start=starts.get(key) or starts.get((action,None))
            if start:out.append({"action":action if not row.get("attempt") else f"{action}_attempt_{row['attempt']}","start_monotonic_s":start["monotonic"],"stop_monotonic_s":row["monotonic"],"boundary_uncertainty_s":1.0,"semantic_source":"OPERATOR_DECLARED_SEMANTIC"})
    return out

