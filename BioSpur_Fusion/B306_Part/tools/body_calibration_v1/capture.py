#!/usr/bin/env python3
"""Phase-2 orchestration shell.

The actual serial transport is intentionally injected. This module owns the
single decoder lifecycle, readiness/T0 state transitions, token brackets and
clean-stop semantics without opening hardware during offline preparation.
"""
from __future__ import annotations
import json, time
from pathlib import Path
from .contract import validate_readiness
from .state_machine import ACTIONS, READY, ActionMachine, action_start_message

class CaptureLifecycle:
    def __init__(self, run_dir: Path, transport):
        self.run_dir=run_dir; self.transport=transport; self.machine=ActionMachine(); self.events=[]
        self.opened=False; self.formal=False
    def open_once(self):
        if self.opened: raise RuntimeError("serial lifecycle already opened")
        self.run_dir.mkdir(parents=True,exist_ok=False);self.transport.open(self.run_dir/"fusion_host_raw.cobs.bin");self.opened=True
        self.events.append({"event":"COLLECTOR_OPEN","monotonic_ns":time.monotonic_ns()})
    def mark_live_and_ready(self, observation):
        if not self.opened or self.formal: raise RuntimeError("invalid readiness transition")
        validate_readiness(observation)
        health=self.transport.health()
        if health["decoded_queue_depth"] or health["queue_drops"]: raise RuntimeError("queue not stable")
        self.formal=True;self.events.append({"event":"FORMAL_T0","monotonic_ns":time.monotonic_ns(),"raw_offset":health["raw_bytes"]})
    def token(self,value,now=None):
        if not self.formal: raise RuntimeError("formal T0 not established")
        self.machine.accept(value,time.monotonic_ns() if now is None else now)
    def action_start_if_due(self,now=None):
        description=self.machine.start_if_due(time.monotonic_ns() if now is None else now)
        return action_start_message(description)
    def close(self):
        if self.opened:self.transport.clean_stop()
        self.events.extend(self.machine.events)
        (self.run_dir/"OPERATOR_TOKENS_AND_LIFECYCLE.json").write_text(json.dumps(self.events,indent=2)+"\n")

def prompt(machine: ActionMachine):
    if machine.state=="WAIT_READY" and machine.current:
        name,text,_seconds,_role=machine.current
        return f"下一动作：{text}\n准备好从电脑返回采集位置时，只输入 {READY[name]}。紧急终止只输入 ABORT_CAPTURE。"
    if machine.state=="PRE_ACTION": return "PRE_ACTION_TRANSITION_UNSCORED：等待完整 10.000 秒。"
    if machine.state=="SCORING": return "动作结束后原地静止至少3秒，再回电脑输入 STOP。"
    return "采集动作已完成。"
