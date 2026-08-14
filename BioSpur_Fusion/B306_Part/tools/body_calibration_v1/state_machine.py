"""Per-action READY/10-second transition/ACTION_START/STOP state machine."""
from __future__ import annotations
from dataclasses import dataclass, field

PRE_ACTION_NS = 10_000_000_000
ACTIONS = (
 ("initial_still","自然站立并保持静止约5秒。",5,"fit"),
 ("t_pose","保持 T-Pose 约5秒。",5,"fit"),
 ("arms","左臂抬起并放下5次，再右臂5次，再双臂5次。",12,"fit"),
 ("elbows","左肘屈伸5次并自然旋转左前臂5次，再完成右侧。",15,"fit"),
 ("knees","抬起左膝5次，再抬起右膝5次。",12,"fit"),
 ("heels","左脚跟向后抬起5次，再完成右脚跟5次。",12,"fit"),
 ("squats","做两次自然深蹲。",10,"fit"),
 ("trunk","躯干自然左转、右转、前倾并恢复，各做3次。",12,"fit"),
 ("walk","小范围自然走动、转身并回到静止。",12,"validation"),
 ("final_still","自然站立并保持静止约8秒。",8,"validation"),
)
READY = {name:f"READY_{name.upper()}" for name,*_ in ACTIONS}
class TokenError(ValueError): pass

@dataclass
class ActionMachine:
 state:str="WAIT_READY";index:int=0;events:list[dict]=field(default_factory=list);transition_deadline_ns:int|None=None
 @property
 def current(self): return ACTIONS[self.index] if self.index<len(ACTIONS) else None
 @property
 def expected(self): return READY[self.current[0]] if self.state=="WAIT_READY" and self.current else "STOP" if self.state=="SCORING" else None
 def accept(self,token:str,monotonic_ns:int):
  token=token.strip()
  if token=="ABORT_CAPTURE":
   if self.state in ("ABORTED","COMPLETE"):raise TokenError("abort after terminal state")
   self.state="ABORTED";self.events.append({"event":"CAPTURE_ABORT","token":token,"monotonic_ns":monotonic_ns});return
  if self.state in ("ABORTED","COMPLETE"):raise TokenError("token after terminal state")
  if token!=self.expected:raise TokenError(f"expected {self.expected}, got {token}")
  if self.state=="WAIT_READY":
   self.transition_deadline_ns=monotonic_ns+PRE_ACTION_NS;self.state="PRE_ACTION"
   self.events.append({"event":"TOKEN_RECEIVED","token":token,"monotonic_ns":monotonic_ns,
    "classification":"PRE_ACTION_TRANSITION_UNSCORED","end_monotonic_ns":self.transition_deadline_ns,"action":self.current[0]})
  else:
   self.events.append({"event":"ACTION_STOP_UPPER_BOUND","token":"STOP","monotonic_ns":monotonic_ns,"action":self.current[0]})
   self.index+=1;self.transition_deadline_ns=None;self.state="COMPLETE" if self.index==len(ACTIONS) else "WAIT_READY"
 def start_if_due(self,monotonic_ns:int):
  if self.state!="PRE_ACTION":raise TokenError("not in pre-action transition")
  if monotonic_ns<self.transition_deadline_ns:raise TokenError("ACTION_START is early")
  start=max(monotonic_ns,self.transition_deadline_ns);self.state="SCORING"
  self.events.append({"event":"ACTION_START","monotonic_ns":start,"action":self.current[0],"description":self.current[1]});return self.current[1]
 def frozen_partition(self):return {name:role for name,_p,_s,role in ACTIONS}

def action_start_message(description):
 return f"🔔 ACTION_START — {description}\n完成后原地静止3秒，再回电脑输入 STOP。"
