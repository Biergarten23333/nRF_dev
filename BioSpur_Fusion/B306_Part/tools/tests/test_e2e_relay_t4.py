import csv
import json
import math
import re
import struct
import tempfile
import unittest
from pathlib import Path

from B306_Part.tools.e2e_relay_t4 import (
    RelayedUwbArchive,
    decode_relayed_uwb,
    run_frozen_t4,
)
from B306_Part.tools.fusion_host_binary import HostFrame, KIND_UWB


ROOT = Path(__file__).resolve().parents[3]
ARCHIVED_VALID_LOG = (
    ROOT
    / "B306_Part"
    / "logs"
    / "relay3_bringup_20260726"
    / "board2"
    / "identity"
    / "live_identity_queries.log"
)


def make_kind1(
    *,
    node_id: int = 0x3C79,
    host_sequence: int = 42,
    master_ms: int = 123456,
    node_sequence: int = 7,
    sweep: int = 99,
    ranges: tuple[int, ...] = (1000,) * 8,
    valid_mask: int = 0xFF,
    quality: tuple[int, ...] = (90,) * 8,
    flags: int = 1,
) -> HostFrame:
    payload = bytearray(184)
    struct.pack_into("<BBHII", payload, 0, 7, 1, 184, node_sequence, 555)
    body = memoryview(payload)[12:102]
    struct.pack_into("<I", body, 0, sweep)
    body[4:9] = bytes.fromhex("0102030405")
    struct.pack_into("<H", body, 9, 0x065F)
    body[11] = 1
    struct.pack_into("<HH", body, 12, 1200, 1000)
    body[16:24] = bytes(range(8))
    body[24:32] = bytes(range(8))
    struct.pack_into("<8H", body, 32, *ranges)
    struct.pack_into("<8H", body, 48, *(2000 + index for index in range(8)))
    body[64:72] = bytes(quality)
    struct.pack_into("<8h", body, 72, *(-8 + index for index in range(8)))
    body[88] = valid_mask
    body[89] = flags
    return HostFrame(
        KIND_UWB, node_id, host_sequence, master_ms, bytes(payload)
    )


class E2ERelayT4Test(unittest.TestCase):
    def test_exact_kind1_offsets(self):
        frame = make_kind1(
            ranges=(1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700),
            valid_mask=0xAD,
            quality=(81, 82, 83, 84, 85, 86, 87, 88),
        )
        decoded = decode_relayed_uwb(frame)
        self.assertEqual(decoded.peer_name, "BSF3C79")
        self.assertEqual(decoded.sweep, 99)
        self.assertEqual(decoded.poll_tx_ts, 0x0504030201)
        self.assertEqual(decoded.identity_code, 0x065F)
        self.assertEqual(decoded.anchor_ids, tuple(range(8)))
        self.assertEqual(decoded.ranges_mm[3], 1300)
        self.assertEqual(decoded.t_round_us[7], 2007)
        self.assertEqual(decoded.quality_percent, tuple(range(81, 89)))
        self.assertEqual(decoded.cfo_ppm_q8[0], -8)
        self.assertEqual(decoded.valid_mask, 0xAD)
        self.assertEqual(decoded.valid_anchor_count, 5)

    def test_relay8_epoch_fields_reach_archive(self):
        frame = make_kind1(flags=0x80 | (14 << 3) | 0x01)
        decoded = decode_relayed_uwb(frame)
        self.assertTrue(decoded.superframe_valid)
        self.assertEqual(decoded.superframe_mod16, 14)
        with tempfile.TemporaryDirectory() as temp:
            archive = RelayedUwbArchive(Path(temp))
            archive.observe_host_frame(frame)
            archive.close()
            with (Path(temp) / "relayed_uwb_epochs.csv").open(newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["superframe_valid"], "1")
            self.assertEqual(row["superframe_mod16"], "14")

    def test_stream_archive_and_histogram(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = RelayedUwbArchive(Path(temp))
            archive.observe_host_frame(make_kind1(valid_mask=0x00))
            archive.observe_host_frame(
                make_kind1(
                    host_sequence=43,
                    master_ms=123556,
                    node_sequence=8,
                    sweep=100,
                    valid_mask=0x0F,
                )
            )
            snapshot = archive.snapshot(["BSF3C79", "BSFDEAD"])
            archive.close()
            self.assertEqual(
                snapshot["BSF3C79"]["valid_anchor_count_histogram"]["0"], 1
            )
            self.assertEqual(
                snapshot["BSF3C79"]["valid_anchor_count_histogram"]["4"], 1
            )
            self.assertEqual(
                snapshot["BSF3C79"]["fraction_with_at_least_one_valid"], 0.5
            )
            self.assertEqual(snapshot["BSFDEAD"]["records"], 0)
            with (Path(temp) / "tr_all.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 16)
            self.assertEqual(sum(int(row["valid"]) for row in rows), 4)
            self.assertEqual(rows[8]["status"], "O")
            self.assertEqual(rows[12]["status"], "E")

    def test_archived_valid_slice_is_range_order_ground_truth(self):
        # The archived host log is decoded text and therefore does not retain
        # quality/rank/t_round.  It still provides independent ground truth for
        # node identity, sweep, mask, anchor ordering, and millimetre ranges.
        line = next(
            row
            for row in ARCHIVED_VALID_LOG.read_text().splitlines()
            if "FUSION_UWB " in row and "valid=0xff" in row
        )
        name = re.search(r"\bname=(BSF[0-9A-F]+)", line)
        if name is None:
            # Old identity logs use an unlabelled RX prefix.
            name_value = "BSFC2CC"
            node_id = 0xC2CC
        else:
            name_value = name.group(1)
            node_id = int(name_value[3:], 16)
        sweep = int(re.search(r"\bsweep=(\d+)", line).group(1))
        pairs = re.search(r"\branges=([0-7]:\d+(?:,[0-7]:\d+){7})", line)
        self.assertIsNotNone(pairs)
        ranges_by_anchor = {
            int(anchor): int(distance)
            for anchor, distance in (
                item.split(":") for item in pairs.group(1).split(",")
            )
        }
        frame = make_kind1(
            node_id=node_id,
            sweep=sweep,
            ranges=tuple(ranges_by_anchor[index] for index in range(8)),
            valid_mask=0xFF,
            # Not archived in text; explicit synthetic value.
            quality=(77,) * 8,
        )
        decoded = decode_relayed_uwb(frame)
        self.assertEqual(decoded.peer_name, name_value)
        self.assertEqual(decoded.sweep, sweep)
        self.assertEqual(
            decoded.ranges_mm,
            tuple(ranges_by_anchor[index] for index in range(8)),
        )
        self.assertEqual(decoded.valid_anchor_count, 8)

    def test_frozen_t4_end_to_end_on_synthetic_static_data(self):
        anchors = (
            (0, 0, 0),
            (4000, 0, 0),
            (0, 4000, 0),
            (4000, 4000, 0),
            (0, 0, 2500),
            (4000, 0, 2500),
            (0, 4000, 2500),
            (4000, 4000, 2500),
        )
        target = (1500, 1800, 900)
        ranges = tuple(
            round(
                math.sqrt(
                    sum((target[index] - anchor[index]) ** 2 for index in range(3))
                )
            )
            for anchor in anchors
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layout_path = root / "layout.json"
            layout_path.write_text(
                json.dumps(
                    {
                        "anchors": [
                            {
                                "id": index,
                                "label": chr(ord("A") + index),
                                "x_mm": xyz[0],
                                "y_mm": xyz[1],
                                "z_mm": xyz[2],
                                "d_anchor_mm": 0,
                            }
                            for index, xyz in enumerate(anchors)
                        ]
                    }
                )
            )
            archive = RelayedUwbArchive(root / "capture")
            for index in range(20):
                archive.observe_host_frame(
                    make_kind1(
                        host_sequence=index,
                        master_ms=100000 + 100 * index,
                        node_sequence=index,
                        sweep=index,
                        ranges=ranges,
                    )
                )
            archive.close()
            result = run_frozen_t4(
                layout_path,
                root / "capture" / "tr_all.csv",
                root / "capture" / "relayed_uwb_epochs.csv",
                root / "solve",
            )
            cluster = result["per_tag"]["BSF3C79"]["cluster"]
            self.assertEqual(
                result["per_tag"]["BSF3C79"]["frames_solved"], 20
            )
            for actual, expected in zip(cluster["mean_xyz_mm"], target):
                self.assertLess(abs(actual - expected), 10.0)


if __name__ == "__main__":
    unittest.main()
