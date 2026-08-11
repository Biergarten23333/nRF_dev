#!/usr/bin/env python3
"""Append-only, non-mutating v47 V45 GUARD evidence support."""
from __future__ import annotations
import json, os, re, time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA="biospur-v47-guard-evidence-v1"
COMMAND="V45 GUARD"
MUTATING_TOKENS=("ACK", "REBOOT", "PREPARE", "COMMIT", "UPLOAD", "PENDING", "FORCE", "CLEAR", "RATE=", "START", "STOP")
FIELDS=("rcv","cause","frozen_ms","streak","max","latched","intent","unk_sreq","named_sreq","rr")
REPLY=re.compile(r"^FUSION_REPLY\b.*\bname=(BSF[0-9A-F]{4})\b.*\btext=(V45 GUARD .*)$")

def wall(): return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
def safe_command(command):
    if command != COMMAND or any(x in command.upper() for x in MUTATING_TOKENS):
        raise ValueError("diagnostic command is not on the immutable read-only allowlist")
    return command
def parse_guard_text(text):
    if not text.startswith("V45 GUARD "): return {"status":"malformed_response","values":{},"missing_fields":list(FIELDS)}
    raw={}
    for token in text.split()[2:]:
        if "=" in token:
            k,v=token.split("=",1);raw[k]=v
    values={}; malformed=[]
    for k in FIELDS:
        if k not in raw: continue
        try: values[k]=int(raw[k],16 if k=="rr" else 10)
        except ValueError: malformed.append(k)
    missing=[k for k in FIELDS if k not in raw]
    status="ok" if not missing and not malformed else ("malformed_response" if malformed else "missing_field")
    return {"status":status,"values":values,"missing_fields":missing,"malformed_fields":malformed}
def parse_master_reply(raw, requested_node):
    m=REPLY.match(raw)
    if not m: return {"status":"malformed_response","requested_node":requested_node,"responding_node":None,"raw_reply":raw}
    responding,text=m.groups(); parsed=parse_guard_text(text)
    return {**parsed,"status":"wrong_node" if responding!=requested_node else parsed["status"],
            "requested_node":requested_node,"responding_node":responding,"raw_reply":raw,"guard_text":text}
def delta(baseline,current):
    out={}
    for k in FIELDS:
        a=baseline.get("values",{}).get(k);b=current.get("values",{}).get(k)
        out[k]=None if a is None or b is None else b-a
    return out

class GuardSampler:
    """Single-flight fleet sampler; callers continue draining Fusion records."""
    def __init__(self,nodes,path,timeout_s=8.0,stagger_s=0.25):
        self.nodes=tuple(nodes);self.path=Path(path);self.timeout_s=timeout_s;self.stagger_s=stagger_s
        self.phase=None;self.queue=[];self.pending=None;self.deadline=0.;self.next_send=0.;self.baseline={}
    def start(self,phase,mono=None):
        if self.phase is not None: return False
        self.phase=phase;self.queue=list(self.nodes);self.pending=None;self.next_send=mono if mono is not None else time.monotonic();return True
    def _append(self,row):
        row={"schema":SCHEMA,"phase":self.phase,"host_monotonic":time.monotonic(),"host_wall":wall(),**row}
        if self.phase!="t0_baseline" and row.get("requested_node") in self.baseline:row["delta_from_t0"]=delta(self.baseline[row["requested_node"]],row)
        if self.phase=="t0_baseline" and row.get("status") in {"ok","missing_field"}:self.baseline[row["requested_node"]]=row
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open("a") as f:f.write(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n");f.flush();os.fsync(f.fileno())
        return row
    def tick(self,send,mono=None):
        now=time.monotonic() if mono is None else mono
        if self.phase is None:return None
        if self.pending and now>=self.deadline:
            n=self.pending;self.pending=None;self.next_send=now+self.stagger_s;return self._append({"status":"timeout","requested_node":n,"responding_node":None,"raw_reply":None,"values":{},"missing_fields":list(FIELDS)})
        if self.pending is None and self.queue and now>=self.next_send:
            n=self.queue.pop(0);send(f"{n} {safe_command(COMMAND)}");self.pending=n;self.deadline=now+self.timeout_s
        if self.pending is None and not self.queue:self.phase=None
        return None
    def on_line(self,line):
        if not self.pending or not line.startswith("FUSION_REPLY "):return None
        # Other asynchronous control replies may share the same channel. They
        # remain in the raw Fusion log and cannot satisfy a GUARD request.
        if " text=V45 GUARD " not in line:return None
        row=parse_master_reply(line,self.pending)
        # A wrong-node reply is evidence, but cannot satisfy the requested node.
        self._append(row)
        if row["status"]=="wrong_node":return row
        self.pending=None;self.next_send=time.monotonic()+self.stagger_s
        return row
    @property
    def active(self):return self.phase is not None
