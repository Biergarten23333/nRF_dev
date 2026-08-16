import ast
import importlib.util
import io
import struct
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
MOD = ROOT / "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase0_input_v1/selective_npy_time_reader.py"
spec = importlib.util.spec_from_file_location("selective_reader", MOD)
reader = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reader
spec.loader.exec_module(reader)


def npy_bytes(descr, rows, *, fortran=False, version=(1, 0)):
    header = repr({"descr": descr, "fortran_order": fortran, "shape": (len(rows),)})
    prefix = 10 if version == (1, 0) else 12
    padding = (64 - ((prefix + len(header) + 1) % 64)) % 64
    encoded = (header + " " * padding + "\n").encode("latin1")
    out = io.BytesIO(); out.write(b"\x93NUMPY"); out.write(bytes(version))
    out.write(struct.pack("<H" if version == (1, 0) else "<I", len(encoded))); out.write(encoded)
    for row in rows: out.write(row)
    return out.getvalue()


def fixture(tmp_path, endian="<", *, fortran=False, descr_override=None):
    descr = descr_override or [
        ("boot_epoch", endian + "u2"), ("sequence", endian + "u2"),
        ("node_timer_us", endian + "u8"), ("global_time_ns", endian + "i8"),
        ("global_time_sigma_ns", endian + "u8"), ("master_arrival_ms", endian + "u8"),
        ("acc_raw", endian + "i2", (3,)), ("gyro_raw", endian + "i2", (3,)),
        ("range_mm", endian + "u2", (8,)), ("raw_record_index", endian + "u8"),
        ("raw_sample_index", "|u1"), ("status", "|u1"),
    ]
    prefix = "<" if endian == "<" else ">"
    poison = [30001, -30002, 30003, -30004, 30005, -30006]
    row = b"".join([
        struct.pack(prefix+"H", 7), struct.pack(prefix+"H", 11),
        struct.pack(prefix+"Q", 123456), struct.pack(prefix+"q", 987654321),
        struct.pack(prefix+"Q", 42000), struct.pack(prefix+"Q", 555),
        struct.pack(prefix+"3h", *poison[:3]), struct.pack(prefix+"3h", *poison[3:]),
        struct.pack(prefix+"8H", *([54321]*8)), struct.pack(prefix+"Q", 99), b"\x03\x01",
    ])
    p = tmp_path / "x.npz"
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("imu_BSF31CC.npy", npy_bytes(descr, [row], fortran=fortran))
    return p, poison


@pytest.mark.parametrize("endian", ["<", ">"])
def test_only_whitelist_and_endian(tmp_path, endian):
    p, poison = fixture(tmp_path, endian)
    stats = reader.ReaderStats(); rows = list(reader.iter_time_projection(p, "imu_BSF31CC", stats=stats))
    assert rows == [{"hardware_node_id":"BSF31CC", "boot_epoch":7, "sequence":11,
                     "node_timer_us":123456, "global_time_ns":987654321,
                     "global_time_sigma_ns":42000, "raw_record_index":99,
                     "raw_sample_index":3, "status":1}]
    assert not set(rows[0]) & {"acc_raw", "gyro_raw", "range_mm", "master_arrival_ms"}
    assert all(v not in rows[0].values() for v in poison)
    assert stats.measurement_numeric_decodes == stats.measurement_arrays == 0
    assert stats.measurement_fields_retained == stats.measurement_values_logged == 0


def test_numpy_apis_can_be_poisoned(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    def forbidden(*a, **k): raise AssertionError("forbidden NumPy API called")
    monkeypatch.setattr(np, "load", forbidden); monkeypatch.setattr(np, "frombuffer", forbidden); monkeypatch.setattr(np, "fromfile", forbidden)
    p, _ = fixture(tmp_path)
    assert next(reader.iter_time_projection(p, "imu_BSF31CC"))["boot_epoch"] == 7


def test_chunk_size_invariance_and_bound(tmp_path):
    p, _ = fixture(tmp_path)
    outs=[]
    for chunk in (1, 7, 64, 4096):
        s=reader.ReaderStats(); outs.append(list(reader.iter_time_projection(p,"imu_BSF31CC",chunk_size=chunk,stats=s)))
        assert s.max_opaque_scratch_bytes <= chunk
    assert outs.count(outs[0]) == len(outs)


def test_measurement_decoder_cannot_be_called(tmp_path, monkeypatch):
    p, _ = fixture(tmp_path)
    original=reader._decode
    def guarded(field, raw, stats):
        assert field.name in reader.ALLOWED_FIELDS
        return original(field,raw,stats)
    monkeypatch.setattr(reader,"_decode",guarded)
    list(reader.iter_time_projection(p,"imu_BSF31CC"))


@pytest.mark.parametrize("kind", ["fortran", "object", "overlap", "malformed", "native_endian"])
def test_fail_closed_layouts(tmp_path, kind):
    if kind == "fortran":
        p,_=fixture(tmp_path,fortran=True)
    elif kind == "object":
        p,_=fixture(tmp_path,descr_override=[("boot_epoch","|O8")])
    elif kind == "overlap":
        p,_=fixture(tmp_path,descr_override={"names":["boot_epoch"],"formats":["<u2"],"offsets":[0],"itemsize":1})
    elif kind == "native_endian":
        p,_=fixture(tmp_path,descr_override=[("boot_epoch","=u2")])
    else:
        p=tmp_path/"x.npz"
        with zipfile.ZipFile(p,"w") as z:z.writestr("imu_BSF31CC.npy",b"not-npy")
    with pytest.raises(reader.SelectiveNpyError): list(reader.iter_time_projection(p,"imu_BSF31CC"))
