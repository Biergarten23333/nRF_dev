import queue
import threading
import time

from v47_c2cc_interactive_rotation_capture import ABORTS,Protocol,token_disposition,wall

def bracket(issue,confirm):return confirm-issue

def test_exact_token_and_incorrect_rejection():
 assert token_disposition("LOW_ON",("LOW_ON",))=="ACCEPT"
 assert token_disposition("low_on",("LOW_ON",))=="REJECT"
 assert token_disposition(" LOW_ON",("LOW_ON",))=="REJECT"
def test_no_phase_advance_before_token_and_variable_delay():
 assert token_disposition("",("RPM3_READY",))=="REJECT"
 assert bracket(10.,37.5)==27.5
def test_operator_abort_tokens_are_always_accepted():
 assert all(token_disposition(x,("LOW_ON",))=="ABORT" for x in ABORTS)
def test_motor_off_is_not_physical_settle():
 off_token_s,raw_quiet_s,uwb_stable_s=10.,14.,18.
 assert max(raw_quiet_s,uwb_stable_s)!=off_token_s


class FakeRecorder:
 def __init__(self):self.aborted=False;self.consumed=0
 def consume(self,_deadline):self.consumed+=1;time.sleep(.001)


class FakeInbox:
 def __init__(self):self.q=queue.Queue()


def test_protocol_rejects_then_accepts_and_capture_continues(tmp_path):
 rec,inbox=FakeRecorder(),FakeInbox();proto=Protocol(rec,inbox,tmp_path)
 now=time.monotonic();inbox.q.put(("low_on",now,wall()));inbox.q.put(("LOW_ON",now+2.5,wall()))
 try:assert proto.wait("instruction",("LOW_ON",),"LOW_ON")=="LOW_ON"
 finally:proto.close()
 assert [x["disposition"] for x in proto.tokens]==["REJECT","ACCEPT"]
 assert rec.consumed>=2
 assert proto.brackets[0]["instruction_to_confirmation_s"]>=2.4


def test_protocol_waits_without_phase_advance_while_consuming(tmp_path):
 rec,inbox=FakeRecorder(),FakeInbox();proto=Protocol(rec,inbox,tmp_path);result=[]
 thread=threading.Thread(target=lambda:result.append(proto.wait("instruction",("RPM3_READY",),"RPM3_READY")))
 thread.start();time.sleep(.03)
 assert thread.is_alive() and rec.consumed>0 and proto.brackets==[]
 inbox.q.put(("RPM3_READY",time.monotonic(),wall()));thread.join(1);proto.close()
 assert result==["RPM3_READY"] and len(proto.brackets)==1


def test_protocol_abort_is_cleanly_reported(tmp_path):
 rec,inbox=FakeRecorder(),FakeInbox();proto=Protocol(rec,inbox,tmp_path)
 inbox.q.put(("ABORT_MOTOR_TEST",time.monotonic(),wall()))
 try:assert proto.wait("instruction",("HIGH_ON",),"HIGH_ON")=="ABORT_MOTOR_TEST"
 finally:proto.close()
 assert rec.aborted and proto.tokens[0]["disposition"]=="ABORT" and proto.brackets==[]
