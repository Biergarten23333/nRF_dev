import struct
from fusion_v1.io.raw_frames import cobs_decode, crc16_ccitt_false, decode, incomplete_tail_bytes, HEADER, MAGIC

def _encode(raw: bytes) -> bytes:
    out=bytearray([0]); ci=0; code=1
    for b in raw:
        if b==0: out[ci]=code; ci=len(out); out.append(0); code=1
        else: out.append(b); code+=1
    out[ci]=code
    return bytes(out)

def test_crc_known_vector(): assert crc16_ccitt_false(b"123456789") == 0x29B1
def test_cobs_zero_roundtrip(): assert cobs_decode(_encode(b"a\0b")) == b"a\0b"
def test_host_header():
    body=HEADER.pack(MAGIC,1,5,0x1234,3,7,9)+b"abc"
    encoded=_encode(body+struct.pack("<H",crc16_ccitt_false(body)))
    f=decode(1,0,len(encoded)+1,encoded)
    assert (f.node,f.kind,f.payload)==("BSF1234",5,b"abc")
def test_incomplete_tail_is_counted_not_decoded(tmp_path):
    p=tmp_path/"raw.bin"; p.write_bytes(b"abc\0tail")
    assert incomplete_tail_bytes(p)==4
