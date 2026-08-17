import struct
import numpy as np

from biospur_fusion.calibration_v2.phase2r.decoder import HEADER, MAGIC, VERSION, crc16_ccitt_false
from biospur_fusion.io_v2.phase3_selective import selective_imu_projection


def cobs_encode(raw):
    out=bytearray(); start=0
    while start < len(raw):
        end=start
        while end < len(raw) and raw[end] and end-start < 254: end+=1
        out.append(end-start+1); out.extend(raw[start:end])
        start=end+1 if end < len(raw) and raw[end]==0 else end
    return bytes(out)


def frame(kind,node,arrival,payload,seq=1):
    body=HEADER.pack(MAGIC,VERSION,kind,node,len(payload),seq,arrival)+payload
    return cobs_encode(body+struct.pack('<H',crc16_ccitt_false(body)))+b'\0'


def imu_payload(base_us):
    return struct.pack('<BBHQh',7,1,1,base_us,0)+struct.pack('<Hhhhhhh',0,0,0,16384,0,0,0)


def test_mixed_container_selects_imu_and_never_decodes_uwb_numeric():
    payload=frame(3,0x1120,1000,imu_payload(1_000_000))+frame(1,0x1120,1001,b'\xff'*184)
    obs,audit=selective_imu_projection(payload,preparation_s=0,formal_s=2,recovery_s=0)
    assert len(obs)==1 and audit.imu_numeric_fields_decoded==7
    assert audit.uwb_numeric_fields_decoded==audit.uwb_arrays_materialized==0


def test_independent_timer_origins_map_to_common_clock_without_arrival_substitution():
    payload=(frame(3,0x1120,10_000,imu_payload(1_000_000))+
             frame(3,0x31CC,10_000,imu_payload(101_000_000)))
    obs,audit=selective_imu_projection(payload,preparation_s=0,formal_s=1,recovery_s=0)
    assert len(obs)==2 and abs(obs[0].time_s-obs[1].time_s)<1e-12
    assert "TIMER2" in audit.clock_model and "offset" in audit.clock_model
