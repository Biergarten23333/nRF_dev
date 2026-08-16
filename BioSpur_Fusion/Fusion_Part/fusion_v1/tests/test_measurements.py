import struct
from fusion_v1.io.raw_frames import HostFrame
from fusion_v1.io.measurements import decode_imu

def test_imu_physical_time_is_base_plus_delta():
    p=struct.pack("<BBHQhHhhhhhh",7,1,42,1_000_000,25,5000,1,2,3,4,5,6)
    f=HostFrame(1,0,1,"x",3,0x1234,0,9,p)
    o=list(decode_imu(f))[0]
    assert o.native_time_us==1_005_000 and o.values==(1,2,3,4,5,6)

