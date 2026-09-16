"""Loader must not confuse initialized directional tracking with a 3D fix."""
import json
from dataclasses import replace

from run_c2_continuous_archive_ab import raw_events
from test_c2_continuous_full_state_feedback import CLOCK,row_at


def test_partial_tracking_retains_one_to_three_valid_links_and_original_epochs(tmp_path):
    records=[]
    for i,mask in enumerate((0,1,3,7,15,255)):
        r=replace(row_at(.01+i*.12,[2.,1.,1.]),valid_mask=mask)
        records.append(dict(node=r.node,boot_epoch=r.boot,availability_global_ns=int((.03+i*.12)*1e9),
            payload=dict(packet_sequence=r.sequence,sweep=r.sweep,strobe_us=r.strobe_us,
                frame_us=r.frame_us,anchor_id=r.anchor_ids,range_mm=list(map(int,r.ranges_mm)),
                t_round_us=r.t_round_us,quality_percent=r.quality,valid_mask=mask,identity=0,node_ms=0)))
    (tmp_path/'UWB.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    clocks={records[0]['node']:CLOCK}
    old=raw_events(tmp_path,clocks,0.,1.)
    new=raw_events(tmp_path,clocks,0.,1.,True)
    assert [r.valid_mask for _,r,_ in old]==[15,255]
    assert [r.valid_mask for _,r,_ in new]==[1,3,7,15,255]
    assert new[-2:]==old
    assert all(availability>epoch for epoch,_,availability in new)
