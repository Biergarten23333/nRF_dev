from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.action00_gap02_diagnostic_plan import DiagnosticEventRouter, load_action00_gap02_diagnostic_plan
from biospur_fusion.c2_uwb_root_world.diagnostic_pelvis_orientation import DiagnosticPelvisGapOrientationOwner
from biospur_fusion.c2_uwb_root_world.diagnostic_uwb_epoch_assembler import DiagnosticUwbEpochAssembler
from biospur_fusion.ingest.events import RawByteProvenance, RecordType, TypedEvent
from test_c2_action00_gap02_diagnostic_plan import _timer_inside
from test_c2_diagnostic_pelvis_orientation import _events


ROOT=Path(__file__).resolve().parents[1]


def _record(node,timer,start,ordinal,valid_mask=0xff):
    return TypedEvent(node,7,RecordType.UWB,ordinal&0xffff,timer,None,None,0,payload={
        "packet_sequence":ordinal&0xffff,"sweep":ordinal,"strobe_us":timer,"frame_us":timer+100,
        "anchor_id":list(range(8)),"range_mm":[2000]*8,"t_round_us":[100]*8,
        "quality_percent":[90]*8,"valid_mask":valid_mask,"identity":ordinal,"node_ms":0},
        raw=RawByteProvenance(ordinal,start,start+2,f"{ordinal+100:064x}",0))


def _fixture(gauge_delta=-1):
    plan,clock,imu_events=_events(101); pelvis=DiagnosticPelvisGapOrientationOwner(plan,clock)
    for event in imu_events[:100]: assert pelvis.ingest(event) is None
    gauge=pelvis.ingest(imu_events[100]); assert gauge is not None
    router=DiagnosticEventRouter(plan,clock)
    timer=_timer_inside(plan.regions[0].start_ns)+120_000
    if gauge_delta != -1:
        pytest.skip("owned gauge time cannot be caller-adjusted")
    return plan,router,DiagnosticUwbEpochAssembler(plan=plan,clock_owner=clock,router=router,pelvis_owner=pelvis,gauge=gauge),timer


def _feed_group(plan,router,assembler,timer,ordinal,valid_masks=None):
    output=None; valid_masks=valid_masks or [0xff]*10
    for i,node in enumerate(plan.expected_nodes):
        event=router.route_original_record((_record(node,timer,plan.regions[0].start_offset+4*(ordinal+i),ordinal+i,valid_masks[i]),))[0]
        value=assembler.ingest(event)
        output=value or output
    return output


def test_first_complete_post_gauge_group_is_selected_with_four_to_eight_links():
    plan,router,assembler,timer=_fixture()
    masks=[0x0f,0x1f,0x3f,0x7f,0xff]*2
    epoch=_feed_group(plan,router,assembler,timer,10,masks)
    assert epoch.role=="BOOTSTRAP" and len(epoch.events)==10
    later=_feed_group(plan,router,assembler,timer+120_000,30,masks)
    assert later.role=="FLOW" and len(later.events)==10


def test_replay_mutation_and_under_four_links_are_byte_noops():
    plan,router,assembler,timer=_fixture(); record=_record(plan.expected_nodes[0],timer,plan.regions[0].start_offset+8,5)
    event=router.route_original_record((record,))[0]; assert assembler.ingest(event) is None
    before=assembler.owner_bytes()
    with pytest.raises(ValueError,match="replay"): assembler.ingest(event)
    assert assembler.owner_bytes()==before
    with pytest.raises(ValueError,match="foreign|mutated"): assembler.ingest(replace(event,availability_global_ns=event.availability_global_ns+1))
    assert assembler.owner_bytes()==before
    bad=router.route_original_record((_record(plan.expected_nodes[1],timer,plan.regions[0].start_offset+20,6,0x07),))[0]
    before=assembler.owner_bytes()
    with pytest.raises(ValueError,match="fewer than four"): assembler.ingest(bad)
    assert assembler.owner_bytes()==before


def test_partial_oldest_waits_until_every_missing_node_advances():
    plan,router,assembler,timer=_fixture()
    for i,node in enumerate(plan.expected_nodes[:-1]):
        event=router.route_original_record((_record(node,timer,plan.regions[0].start_offset+4*(10+i),10+i),))[0]
        assert assembler.ingest(event) is None
    # A single newer asynchronous node cannot retire the unresolved oldest bucket.
    node=plan.expected_nodes[0]
    newer=router.route_original_record((_record(node,timer+120_000,plan.regions[0].start_offset+100,40),))[0]
    assert assembler.ingest(newer) is None


def test_measurement_reversal_and_fourth_unresolved_bucket_are_byte_noops():
    plan,router,assembler,timer=_fixture(); node=plan.expected_nodes[0]
    first=router.route_original_record((_record(node,timer,plan.regions[0].start_offset+40,10),))[0]
    assert assembler.ingest(first) is None
    same=router.route_original_record((_record(node,timer,plan.regions[0].start_offset+44,11),))[0]
    before=assembler.owner_bytes()
    with pytest.raises(ValueError,match="measurement"): assembler.ingest(same)
    assert assembler.owner_bytes()==before
    for index in range(1,3):
        event=router.route_original_record((_record(node,timer+index*120_000,plan.regions[0].start_offset+48+4*index,20+index),))[0]
        assert assembler.ingest(event) is None
    fourth=router.route_original_record((_record(node,timer+3*120_000,plan.regions[0].start_offset+80,30),))[0]
    before=assembler.owner_bytes()
    with pytest.raises(OverflowError,match="capacity"): assembler.ingest(fourth)
    assert assembler.owner_bytes()==before


def test_foreign_pelvis_issuer_cannot_authorize_gauge():
    plan,clock,events=_events(101)
    first=DiagnosticPelvisGapOrientationOwner(plan,clock); second=DiagnosticPelvisGapOrientationOwner(plan,clock)
    for event in events[:100]: assert first.ingest(event) is None
    gauge=first.ingest(events[100]); router=DiagnosticEventRouter(plan,clock)
    with pytest.raises(ValueError,match="foreign|mutated"):
        DiagnosticUwbEpochAssembler(plan=plan,clock_owner=clock,router=router,pelvis_owner=second,gauge=gauge)
