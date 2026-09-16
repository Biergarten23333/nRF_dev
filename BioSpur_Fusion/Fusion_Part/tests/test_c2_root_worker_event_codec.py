import hashlib
import json
import time
from dataclasses import replace
from types import MappingProxyType

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.async_root_worker_u7d import AsyncRootWorker
from biospur_fusion.c2_uwb_root_world.root_worker_event_codec import decode_event,encode_event
from test_c2_async_root_worker import _config,_corroboration,_envelope,_event,_group,_imu


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,
                      allow_nan=False).encode()


def _reseal(document):
    core={key:document[key] for key in ("schema","type","payload")}
    document["sha256"]=hashlib.sha256(_canonical(core)).hexdigest()
    return _canonical(document)


def test_v1_roundtrip_is_byte_exact_for_every_allowed_variant_and_readonly():
    rows=_group(target=np.array([.35,0,0.]));activity,consensus,contact=_corroboration(rows)
    event=_event(rows,dynamic_envelope=_envelope(),activity=activity,consensus=consensus,contact=contact)
    blob=encode_event(event);decoded=decode_event(blob)
    assert encode_event(decoded)==blob and decoded.payload==event.payload
    assert isinstance(decoded.contact.previous_state,MappingProxyType)
    assert not decoded.activity.impulse_mps.flags.writeable
    assert not decoded.consensus.root_position_m.flags.writeable
    imu=decode_event(encode_event(_imu(3,.015)))
    assert imu.kind=="IMU" and not imu.payload.specific_force_sensor_mps2.flags.writeable


@pytest.mark.parametrize("mutation",("extra","missing","version","tag","digest","nonfinite","shape","wrong_type"))
def test_codec_rejects_adversarial_documents(mutation):
    document=json.loads(encode_event(_imu(0,.005)))
    if mutation=="extra":document["extra"]=1
    elif mutation=="missing":del document["payload"]["sequence"]
    elif mutation=="version":document["schema"]="biospur.c2.root-worker-event.v2"
    elif mutation=="tag":document["payload"]["data"]["type"]="ForeignImu"
    elif mutation=="digest":document["sha256"]="0"*64
    elif mutation=="nonfinite":
        document["payload"]["data"]["measurement_time_s"]="NaN"
    elif mutation=="shape":document["payload"]["data"]["specific_force_sensor_mps2"]=[0,0]
    elif mutation=="wrong_type":document["payload"]["sequence"]=True
    blob=_canonical(document) if mutation in ("extra","digest") else _reseal(document)
    with pytest.raises((TypeError,ValueError)):decode_event(blob)


def test_submit_encode_failure_closes_child_without_advancing_time_or_queue():
    worker=AsyncRootWorker(_config());previous=worker._last;started=time.monotonic()
    bad=replace(_event(_group()),dynamic_envelope="not-an-envelope")
    with pytest.raises(TypeError):worker.submit(bad)
    assert worker._last==previous and worker._closed
    assert not worker._process.is_alive() and time.monotonic()-started<=2.


def test_submit_queue_failure_closes_child_and_preserves_original_error():
    worker=AsyncRootWorker(_config());worker._input.close();started=time.monotonic()
    with pytest.raises((ValueError,OSError)):worker.submit(_imu(0,.005))
    assert worker._closed and not worker._process.is_alive() and time.monotonic()-started<=2.


def test_child_decode_failure_propagates_and_child_exits(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.async_root_worker_u7d as owner
    worker=AsyncRootWorker(_config());monkeypatch.setattr(owner,"encode_event",lambda _:b"{}")
    worker.submit(_imu(0,.005));started=time.monotonic()
    with pytest.raises(RuntimeError,match="DTO key set mismatch"):
        worker.close_and_collect(0)
    assert not worker._process.is_alive() and time.monotonic()-started<=2.


def test_ready_worker_transports_corroborated_fall_and_drains_exactly():
    rows=_group(target=np.array([.35,0,0.]));activity,consensus,contact=_corroboration(rows)
    worker=AsyncRootWorker(_config(_envelope(.001)))
    worker.submit(_event(rows,dynamic_envelope=_envelope(2.,type(_envelope().reachability_class).DYNAMIC_FALL),
        activity=activity,consensus=consensus,contact=contact))
    actual,final=worker.close_and_collect(1)
    assert actual[0]["decision"]=="ACCEPT_CORROBORATED_DYNAMIC"
    assert final["sentinel_received"] and final["processed_event_count"]==1
    assert final["input_queue_size_after_join"]==0 and final["process_exitcode"]==0
