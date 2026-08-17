import ast,struct
from pathlib import Path
import numpy as np

from biospur_fusion.calibration_v2.phase2r.decoder import HEADER,MAGIC,VERSION,crc16_ccitt_false
from biospur_fusion.imu_pose_v1.decoder import decode_imu_only

ROOT=Path(__file__).resolve().parents[5]/'BioSpur_Fusion/Fusion_Part'


def cobs(raw):
    out=bytearray();start=0
    while start<len(raw):
        end=start
        while end<len(raw) and raw[end] and end-start<254:end+=1
        out.append(end-start+1);out.extend(raw[start:end]);start=end+1 if end<len(raw) else end
    return bytes(out)


def frame(kind,node,arrival,payload):
    body=HEADER.pack(MAGIC,VERSION,kind,node,len(payload),1,arrival)+payload
    return cobs(body+struct.pack('<H',crc16_ccitt_false(body)))+b'\0'


def test_mixed_container_never_decodes_uwb_payload():
    imu=struct.pack('<BBHQh',7,1,1,1_000_000,0)+struct.pack('<Hhhhhhh',0,0,0,16384,0,0,0)
    samples,a=decode_imu_only(frame(3,0x1120,1000,imu)+frame(1,0x1120,1001,b'\xff'*184),include_start_s=0,include_stop_s=2)
    assert len(samples)==1 and a.imu_numeric_scalars==6 and a.uwb_numeric_scalars==a.uwb_arrays==0


def test_production_import_graph_has_no_old_core_or_uwb():
    paths=list((ROOT/'src/biospur_fusion/imu_pose_v1').glob('*.py'));assert paths
    for path in paths:
        tree=ast.parse(path.read_text());imports=[]
        for n in ast.walk(tree):
            if isinstance(n,ast.ImportFrom):imports.append(n.module or '')
            elif isinstance(n,ast.Import):imports.extend(x.name for x in n.names)
        assert not any('articulated_v2' in x or 'anchor_fusion' in x or '.uwb' in x for x in imports),path


def test_no_root_translation_contact_zupt_or_bone_stretch_state():
    text='\n'.join(p.read_text() for p in (ROOT/'src/biospur_fusion/imu_pose_v1').glob('*.py'))
    assert 'hard_zupt' not in text.lower() and 'bone_stretch' not in text.lower() and 'per_node_free_xyz' not in text.lower()
