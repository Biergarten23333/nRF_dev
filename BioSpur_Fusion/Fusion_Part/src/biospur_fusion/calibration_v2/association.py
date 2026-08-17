from __future__ import annotations
from collections import Counter
import math
import numpy as np

ROLES=("pelvis","torso","upper_arm_left","upper_arm_right","forearm_left","forearm_right","thigh_left","thigh_right","shank_left","shank_right")

def validate_problem(nodes,roles,score):
    if len(nodes)!=len(set(nodes)) or len(roles)!=len(set(roles)) or len(nodes)!=len(roles):
        raise ValueError("association must be one-to-one with unique nodes and roles")
    a=np.asarray(score,float)
    if a.shape!=(len(nodes),len(roles)) or not np.isfinite(a).all():
        raise ValueError("finite square score matrix required")
    return a

def topk_assignments(nodes,roles,score,k=10):
    """Exact K-best one-to-one assignment by subset dynamic programming."""
    a=validate_problem(nodes,roles,score); n=len(nodes)
    dp={0:[(0.0,())]}
    for i in range(n):
        nxt={}
        for mask,candidates in dp.items():
            for value,assignment in candidates:
                for j in range(n):
                    if mask>>j&1: continue
                    key=mask|1<<j
                    nxt.setdefault(key,[]).append((value+float(a[i,j]),assignment+(j,)))
        dp={m:sorted(v,key=lambda x:(-x[0],x[1]))[:k] for m,v in nxt.items()}
    out=[]
    for value,idx in dp[(1<<n)-1]:
        out.append({"score":value,"mapping":{nodes[i]:roles[idx[i]] for i in range(n)}})
    return out

def assignment_key(mapping,nodes):
    return tuple(mapping[n] for n in nodes)

def wilson_lower(successes,total,z=1.6448536269514722):
    if total<=0: return 0.0
    p=successes/total; d=1+z*z/total
    return (p+z*z/(2*total)-z*math.sqrt(p*(1-p)/total+z*z/(4*total*total)))/d

def bootstrap_assignments(nodes,roles,block_scores,replicates=500,seed=20260817):
    blocks=np.asarray(block_scores,float)
    if blocks.ndim!=3 or blocks.shape[1:]!=(len(nodes),len(roles)) or len(blocks)<2:
        raise ValueError("at least two complete block score matrices required")
    rng=np.random.default_rng(seed); keys=[]; margins=[]
    for _ in range(replicates):
        sample=blocks[rng.integers(0,len(blocks),len(blocks))].mean(axis=0)
        top=topk_assignments(nodes,roles,sample,2)
        keys.append(assignment_key(top[0]["mapping"],nodes)); margins.append(top[0]["score"]-top[1]["score"])
    counts=Counter(keys); winner,nwin=counts.most_common(1)[0]
    bindings={nodes[i]:Counter(k[i] for k in keys) for i in range(len(nodes))}
    return {"replicates":replicates,"winner":dict(zip(nodes,winner)),"complete_frequency":nwin/replicates,
            "wilson_lower_one_sided_95":wilson_lower(nwin,replicates),
            "binding_frequency":{n:bindings[n][winner[i]]/replicates for i,n in enumerate(nodes)},
            "margin_mean":float(np.mean(margins)),"margin_standard_error":float(np.std(margins,ddof=1)/math.sqrt(replicates)),
            "selection_counts":{"|".join(k):v for k,v in counts.most_common(10)}}

def permutation_null_margins(nodes,roles,block_scores,permutations=1000,seed=20260818):
    blocks=np.asarray(block_scores,float); rng=np.random.default_rng(seed); margins=[]
    for _ in range(permutations):
        shuffled=np.stack([b[:,rng.permutation(len(roles))] for b in blocks])
        top=topk_assignments(nodes,roles,shuffled.mean(axis=0),2)
        margins.append(top[0]["score"]-top[1]["score"])
    return {"valid_permutations":permutations,"margin_p99":float(np.quantile(margins,0.99)),"resolution":1/(permutations+1)}

def freeze_classification(bootstrap,null,observed_margin,leave_action,leave_anchor,uwb_off,facing_present,repetition_complete):
    binding_ok=min(bootstrap["binding_frequency"].values())>=.95
    stable=(bootstrap["complete_frequency"]>=.95 and bootstrap["wilson_lower_one_sided_95"]>=.90 and binding_ok and
            observed_margin>2*bootstrap["margin_standard_error"] and observed_margin>null["margin_p99"] and
            all(leave_action.values()) and all(leave_anchor.values()) and uwb_off and facing_present and repetition_complete)
    if stable:return "AUTHORITATIVE_FREEZE_CANDIDATE"
    if bootstrap["complete_frequency"]>=.80 and facing_present:return "PROVISIONAL_TOPK"
    return "INSUFFICIENT_EVIDENCE"

