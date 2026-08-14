import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from body_calibration_v1.capture import CaptureLifecycle
from body_calibration_v1.contract import *

class Fake:
    def __init__(self,backlog=0):self.n=0;self.backlog=backlog;self.stopped=False
    def open(self,_):self.n+=1
    def health(self):return {"decoded_queue_depth":self.backlog,"queue_drops":0,"raw_bytes":123}
    def clean_stop(self):self.stopped=True
def obs():return {"master":MASTER,"central":CENTRAL,"anchors":list(ANCHORS),"listeners_ok":True,"peers":[{"name":n,"connected":True,"subscribed":True,"marker":MARKER,"fwid":FWID,"active_sha":ACTIVE_SHA,"confirmed":1} for n in sorted(EXPECTED_NODES)]}
def test_one_open_formal_t0_and_token_brackets(tmp_path):
    f=Fake();c=CaptureLifecycle(tmp_path/"run",f);c.open_once()
    with pytest.raises(RuntimeError):c.open_once()
    c.mark_live_and_ready(obs());c.token("READY_INITIAL_STILL",100)
    with pytest.raises(Exception):c.action_start_if_due(100+9_999_999_999)
    assert c.action_start_if_due(100+10_000_000_000).startswith("🔔 ACTION_START")
    c.token("STOP",200+10_000_000_000);c.close()
    assert f.n==1 and f.stopped and (tmp_path/"run/OPERATOR_TOKENS_AND_LIFECYCLE.json").exists()
def test_backlog_blocks_t0(tmp_path):
    c=CaptureLifecycle(tmp_path/"run",Fake(2));c.open_once()
    with pytest.raises(RuntimeError,match="queue"):c.mark_live_and_ready(obs())
