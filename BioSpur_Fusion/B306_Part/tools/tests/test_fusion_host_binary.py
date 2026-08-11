import struct
import unittest

from B306_Part.tools.fusion_host_binary import (
    FrameStreamDecoder,
    HostFrame,
    KIND_IMU,
    KIND_QOS,
    KIND_QUEUE_COUNTERS,
    KIND_REPLY,
    KIND_TELEMETRY,
    KIND_TEXT,
    KIND_UWB,
    decode_superframe_flags,
    encode_frame,
    frame_to_line,
    resolve_superframe_mod16,
)
from B306_Part.tools.fusion_session import imu_sequence_gaps
from B306_Part.tools.confirm_b306_v32 import extract_token


class FusionHostBinaryTest(unittest.TestCase):
    def roundtrip(self, kind: int, payload: bytes) -> str:
        expected = HostFrame(kind, 0x3C79, 42, 123456, payload)
        decoder = FrameStreamDecoder()
        encoded = encode_frame(expected)
        frames = decoder.feed(encoded[:3]) + decoder.feed(encoded[3:])
        self.assertEqual(frames, [expected])
        self.assertEqual(decoder.errors, 0)
        return frame_to_line(frames[0])

    def test_text(self):
        self.assertEqual(
            self.roundtrip(KIND_TEXT, b"FUSION_TEST ok=1\n"),
            "FUSION_TEST ok=1",
        )

    @staticmethod
    def imu_payload(count: int, sequence: int) -> bytes:
        prefix = struct.pack("<BBHQh", 6, count, sequence, 9000, 25)
        sample = struct.pack("<Hhhhhhh", 0, 1, 2, 3, 4, 5, 6)
        return prefix + sample * count

    def test_imu_batch_2(self):
        line = self.roundtrip(KIND_IMU, self.imu_payload(2, 7))
        self.assertIn("name=BSF3C79", line)
        self.assertIn("seq=7", line)
        self.assertIn("n=2", line)

    def test_imu_batch_5(self):
        line = self.roundtrip(KIND_IMU, self.imu_payload(5, 9))
        self.assertIn("seq=9", line)
        self.assertIn("n=5", line)
        self.assertEqual(line.count(";"), 4)

    def test_imu_batches_8_10_and_16(self):
        for count in (8, 10, 16):
            with self.subTest(count=count):
                line = self.roundtrip(KIND_IMU, self.imu_payload(count, 100))
                self.assertIn(f"n={count}", line)
                self.assertEqual(line.count(";"), count - 1)

    def test_imu_batch_17_rejected(self):
        with self.assertRaisesRegex(Exception, "invalid IMU record length"):
            self.roundtrip(KIND_IMU, self.imu_payload(17, 100))

    def test_imu_sequence_gap_reconstruction_for_mixed_batches(self):
        lines = [
            self.roundtrip(KIND_IMU, self.imu_payload(2, 10)),
            self.roundtrip(KIND_IMU, self.imu_payload(5, 12)),
            self.roundtrip(KIND_IMU, self.imu_payload(2, 18)),
        ]
        self.assertEqual(imu_sequence_gaps(lines), (1, 3))

    def test_imu_sequence_gap_reconstruction_for_5_8_10(self):
        lines = [
            self.roundtrip(KIND_IMU, self.imu_payload(5, 100)),
            self.roundtrip(KIND_IMU, self.imu_payload(8, 105)),
            self.roundtrip(KIND_IMU, self.imu_payload(10, 113)),
        ]
        self.assertEqual(imu_sequence_gaps(lines), (0, 3))

    def test_reply(self):
        text = b"IMU STATUS OK"
        payload = struct.pack("<BBHBH", 5, 4, 7 + len(text), 0, 9) + text
        self.assertIn("text=IMU STATUS OK", self.roundtrip(KIND_REPLY, payload))

    def test_telemetry_v5(self):
        payload = bytearray(239 + 24)
        struct.pack_into("<BBH", payload, 0, 5, 2, 239)
        line = self.roundtrip(KIND_TELEMETRY, bytes(payload))
        self.assertIn("imu_missed_deadlines=0", line)

    def test_uwb(self):
        payload = bytearray(184)
        struct.pack_into("<BBHII", payload, 0, 5, 1, 184, 3, 10)
        payload[12 + 16 : 12 + 24] = b"\xff" * 8
        struct.pack_into("<5Q", payload, 102, 100, (1 << 64) - 1, 101, 102, (1 << 64) - 1)
        struct.pack_into("<9I", payload, 142, 1, 2, 2, 0, 0, 0, 0, 0, 0)
        struct.pack_into("<HBBBB", payload, 178, 50000, 0, 1, 1, 0x0F)
        line = self.roundtrip(KIND_UWB, bytes(payload))
        self.assertIn("verdict=healthy", line)
        self.assertIn("ranges=-", line)

    def test_uwb_all_eight_slots_and_signed_cfo_extremes(self):
        payload=bytearray(184);struct.pack_into("<BBHII",payload,0,7,1,184,3,10)
        body=12;struct.pack_into("<I",payload,body,123);payload[body+4:body+9]=(0x123456789a).to_bytes(5,"little")
        struct.pack_into("<HBHH",payload,body+9,0xBEEF,7,1200,1000)
        payload[body+16:body+24]=bytes((1,2,3,4,5,6,7,0xff));payload[body+24:body+32]=bytes(range(8))
        struct.pack_into("<8H",payload,body+32,*range(100,108));struct.pack_into("<8H",payload,body+48,*range(200,208))
        payload[body+64:body+72]=bytes(range(90,98));struct.pack_into("<8h",payload,body+72,-32768,-1,0,1,2,3,4,32767)
        payload[body+88]=0x7f;payload[body+89]=3
        struct.pack_into("<5Q",payload,102,100,*([2**64-1]*4));struct.pack_into("<9I",payload,142,*([0]*9));struct.pack_into("<HBBBB",payload,178,50000,0,1,1,0)
        line=self.roundtrip(KIND_UWB,bytes(payload))
        for token in ("guard_us=1200","spacing_us=1000","anchor_id=1,2,3,4,5,6,7,255","rank=0,1,2,3,4,5,6,7","range_mm=100,101,102,103,104,105,106,107","t_round_us=200,201,202,203,204,205,206,207","quality=90,91,92,93,94,95,96,97","cfo_ppm_q8=-32768,-1,0,1,2,3,4,32767","valid_mask=0x7f"):
            self.assertIn(token,line)

    def test_imu_tuple_count_and_width_are_strict(self):
        line=self.roundtrip(KIND_IMU,self.imu_payload(5,9));fields=dict(x.split("=",1) for x in line.split() if "=" in x)
        tuples=fields["samples"].split(";");self.assertEqual(len(tuples),int(fields["n"]));self.assertTrue(all(len(x.split(","))==7 for x in tuples))

    def test_relay8_superframe_flags_and_legacy_semantics(self):
        payload = bytearray(184)
        struct.pack_into("<BBHII", payload, 0, 5, 1, 184, 3, 10)
        payload[12 + 16 : 12 + 24] = b"\xff" * 8
        payload[12 + 89] = 0x01 | 0x80 | (15 << 3)
        struct.pack_into(
            "<5Q", payload, 102, 100, (1 << 64) - 1, 101, 102,
            (1 << 64) - 1,
        )
        struct.pack_into("<9I", payload, 142, 1, 2, 2, 0, 0, 0, 0, 0, 0)
        struct.pack_into("<HBBBB", payload, 178, 50000, 0, 1, 1, 0x0F)
        line = self.roundtrip(KIND_UWB, bytes(payload))
        self.assertIn("strobe_sent=1", line)
        self.assertIn("sf_valid=1 sf_mod16=15", line)
        self.assertEqual(decode_superframe_flags(0x79), (False, None))
        self.assertEqual(decode_superframe_flags(0xF9), (True, 15))

    def test_superframe_mod16_wrap_and_fit_disambiguation(self):
        self.assertEqual(resolve_superframe_mod16(15, 15, True), 15)
        self.assertEqual(resolve_superframe_mod16(16, 0, True), 16)
        self.assertEqual(resolve_superframe_mod16(100, 5, True), 101)
        self.assertEqual(resolve_superframe_mod16(102, 5, True), 101)
        self.assertIsNone(resolve_superframe_mod16(100, None, False))
        with self.assertRaisesRegex(ValueError, "modulo 16"):
            resolve_superframe_mod16(100, 16, True)

    def test_queue_counters(self):
        payload = struct.pack(
            "<BBHIIIIHHHIIIIIIII",
            7, 5, 58, 1000, 1, 2, 3, 4, 5, 6, 700, 8000,
            100, 200, 300, 4, 5, 6,
        ) + struct.pack(
            "<IIII", 95, 198, 297, 0,
        )
        line = self.roundtrip(KIND_QUEUE_COUNTERS, payload)
        self.assertIn("q_drop_imu=1", line)
        self.assertIn("q_hwm_ctl=6", line)
        self.assertIn("publisher_max_us=8000", line)
        self.assertIn("enq_ctl=300", line)
        self.assertIn("delivered_uwb=198", line)

    def test_qos(self):
        payload = struct.pack(
            "<BBH14I2H37H",
            1, 1, 7,
            1000, 1000, 10000, 2, 20, 1, 30, 2, 3, 4,
            5, 100, 200, 300,
            10, 30,
            *([1] * 37),
        )
        line = self.roundtrip(KIND_QOS, payload)
        self.assertIn("spacing=ON", line)
        self.assertIn("reports=20", line)
        self.assertIn("event_gaps=1", line)
        self.assertIn("imu_epoch_defer_drop=5", line)
        self.assertIn("delivered_ctl=300", line)

    def test_corrupt_record_does_not_consume_next_boundary(self):
        good = encode_frame(HostFrame(KIND_TEXT, 0, 2, 3, b"ok\n"))
        bad = bytearray(encode_frame(HostFrame(KIND_TEXT, 0, 1, 2, b"bad\n")))
        bad[2] ^= 0x80
        decoder = FrameStreamDecoder()
        frames = decoder.feed(bytes(bad) + good)
        self.assertEqual([frame.payload for frame in frames], [b"ok\n"])
        self.assertEqual(decoder.errors, 1)

    def test_boot_confirm_token_parser(self):
        self.assertEqual(
            extract_token("BOOT CONFIRM PREPARED token=12ABCDEF confirmed=0"),
            "12ABCDEF",
        )
        with self.assertRaisesRegex(Exception, "token absent"):
            extract_token("BOOT CONFIRM PREPARED confirmed=0")


if __name__ == "__main__":
    unittest.main()
