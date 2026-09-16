#!/usr/bin/env python3
"""Run all actual C2 actions through arrived-only retained-frame calibration."""
import argparse
import json
from pathlib import Path
import time

from biospur_fusion.c2_sparse_nodes.inputs import FiveNodeFrontend,NODES,episode_contracts,sha
from biospur_fusion.c2_five_calibration.progressive.frame_prefix import FramePrefixSession


def main(out):
    out.mkdir(parents=True,exist_ok=False)
    sources=[Path(__file__),Path('src/biospur_fusion/c2_five_calibration/progressive/frame_prefix.py'),
             *[Path('src/biospur_fusion/c2_sparse_nodes')/n for n in
               ('inputs.py','calibration.py','functional_frames.py')]]
    binding={str(p):sha(p) for p in sources}
    (out/'CONTRACT.json').write_text(json.dumps(dict(source_hashes=binding,
        kind='FULL_RECORDED_C2_RETAINED_FRAME_PREFIX_ONLY',H_used=False,ten_node_used=False,
        source_kind='continuous_raw_five_IMU_payloads',online_transport_clock_validated=False,
        full_pose_calibration=False,subphase_updates=False),indent=2))
    session=FramePrefixSession();reader=FiveNodeFrontend();audits=[];start=time.monotonic()
    for action,contract in episode_contracts().items():
        if time.monotonic()-start>600:raise TimeoutError('frame prefix ten-minute budget')
        data,audit=reader.read({action:contract},start=reader.cursor)
        snapshot=session.ingest(action,{n:data[action][n]['imu'] for n in NODES})
        audits.append(audit)
        (out/'TRACE.json').write_text(json.dumps(session.snapshots,indent=2))
        (out/'INPUT_AUDIT.json').write_text(json.dumps(audits,indent=2))
        print(action,snapshot['updated_nodes'],flush=True)
    if any(sha(Path(p))!=v for p,v in binding.items()):raise ValueError('producer changed during run')
    (out/'RESULT.json').write_text(json.dumps(dict(completed=True,actions=len(session.snapshots),
        elapsed_seconds=time.monotonic()-start,final=session.snapshots[-1]),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path)
    main(parser.parse_args().out)
