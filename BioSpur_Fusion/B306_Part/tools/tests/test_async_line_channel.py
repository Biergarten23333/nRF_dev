import os
import pty
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from async_line_channel import ThreadedLineChannel
from fusion_host_binary import FrameStreamDecoder,HostFrame,KIND_TEXT,encode_frame


class AsyncLineChannelTests(unittest.TestCase):
    def make_channel(self):
        master, slave = pty.openpty()
        temp = tempfile.TemporaryDirectory()
        log_path = Path(temp.name) / "capture.log"
        log_file = log_path.open("w", encoding="utf-8", buffering=1)
        channel = ThreadedLineChannel(
            os.ttyname(slave),
            log_file,
            "SYNTH",
            backlog_red_records=10000,
        )
        channel.transport_mode = "text"
        return master, slave, temp, log_path, log_file, channel

    def test_background_drain_survives_consumer_pause(self):
        master, slave, temp, log_path, log_file, channel = self.make_channel()
        count = 5000

        def produce():
            for base in range(0, count, 100):
                payload = "".join(
                    f"FUSION_UWB name=BSFTEST sweep={index}\n"
                    for index in range(base, min(base + 100, count))
                )
                os.write(master, payload.encode())

        producer = threading.Thread(target=produce)
        producer.start()
        time.sleep(1.2)
        received = []
        deadline = time.monotonic() + 10.0
        while len(received) < count and time.monotonic() < deadline:
            line = channel.read(deadline)
            if line is not None:
                received.append(line)
        producer.join()
        health = channel.health_snapshot()
        channel.close()
        log_file.close()
        os.close(master)
        os.close(slave)
        try:
            self.assertEqual(len(received), count)
            self.assertEqual(
                [int(line.rsplit("=", 1)[1]) for line in received],
                list(range(count)),
            )
            self.assertEqual(health["decoded_queue_drops"], 0)
            self.assertEqual(health["log_queue_drops"], 0)
            self.assertEqual(health["red_markers"], 0)
            text = log_path.read_text(encoding="utf-8")
            self.assertEqual(text.count("SYNTH_RX FUSION_UWB"), count)
        finally:
            temp.cleanup()

    def test_boundary_discard_is_explicit_and_counted(self):
        master, slave, temp, log_path, log_file, channel = self.make_channel()
        os.write(master, b"A one\nB two\nC three\n")
        deadline = time.monotonic() + 2.0
        while channel.health_snapshot()["decoded_queue_depth"] < 3:
            if time.monotonic() >= deadline:
                self.fail("background drain did not receive test records")
            time.sleep(0.01)
        boundary = channel.discard_pending("unit_test")
        channel.close()
        log_file.close()
        os.close(master)
        os.close(slave)
        try:
            self.assertEqual(boundary["discarded_records"], 3)
            self.assertEqual(boundary["kinds"], {"A": 1, "B": 1, "C": 1})
            self.assertIn("HOST_DRAIN_BOUNDARY", log_path.read_text())
        finally:
            temp.cleanup()

    def test_records_are_archived_while_consumer_is_paused(self):
        master, slave, temp, log_path, log_file, channel = self.make_channel()
        count = 1000
        payload = "".join(f"FUSION_UWB sweep={i}\n" for i in range(count))
        os.write(master, payload.encode())
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if (
                channel.health_snapshot()["decoded_queue_depth"] == count
                and log_path.read_text(encoding="utf-8").count(
                    "SYNTH_RX FUSION_UWB"
                )
                == count
            ):
                break
            time.sleep(0.02)
        health = channel.health_snapshot()
        archived = log_path.read_text(encoding="utf-8").count(
            "SYNTH_RX FUSION_UWB"
        )
        channel.close()
        log_file.close()
        os.close(master)
        os.close(slave)
        try:
            self.assertEqual(health["decoded_queue_depth"], count)
            self.assertEqual(archived, count)
            self.assertEqual(health["decoded_queue_drops"], 0)
            self.assertEqual(health["log_queue_drops"], 0)
        finally:
            temp.cleanup()

    def test_watchdog_emits_red_when_reader_stalls(self):
        master, slave, temp, log_path, log_file, channel = self.make_channel()
        channel._reader_stop.set()
        channel._reader.join(timeout=2.0)
        channel._reader_heartbeat = time.monotonic() - 2.0
        deadline = time.monotonic() + 2.0
        while (
            channel.health_snapshot()["red_markers"] == 0
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        health = channel.health_snapshot()
        channel.close()
        log_file.close()
        os.close(master)
        os.close(slave)
        try:
            self.assertGreaterEqual(health["red_markers"], 1)
            self.assertIn("kind=reader_stall", log_path.read_text())
        finally:
            temp.cleanup()

    def test_close_flushes_partial_log_batch(self):
        master, slave, temp, log_path, log_file, channel = self.make_channel()
        os.write(master, b"ONE record\n")
        deadline = time.monotonic() + 2.0
        while (
            channel.health_snapshot()["decoded_queue_depth"] < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        channel.close()
        log_file.close()
        os.close(master)
        os.close(slave)
        try:
            self.assertIn("SYNTH_RX ONE record", log_path.read_text())
        finally:
            temp.cleanup()

    def test_raw_binary_tee_replays_order_crc_and_has_no_drops(self):
        master,slave=pty.openpty()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);log=(root/'x.log').open('w',buffering=1);raw=(root/'raw.bin').open('wb',buffering=0)
            ch=ThreadedLineChannel(os.ttyname(slave),log,'SYNTH',raw_file=raw,backlog_red_records=20000)
            ch.transport_mode='binary';frames=[HostFrame(KIND_TEXT,0,i,i,f"row={i}\n".encode()) for i in range(10000)]
            blob=b''.join(encode_frame(x) for x in frames)
            def produce():
                for i in range(0,len(blob),8192):os.write(master,blob[i:i+8192])
            t=threading.Thread(target=produce);t.start();received=[];deadline=time.monotonic()+15
            while len(received)<len(frames) and time.monotonic()<deadline:
                x=ch.read(deadline)
                if x is not None:received.append(x)
            t.join();ch.close();raw.close();log.close();health=ch.health_snapshot()
            replay=FrameStreamDecoder();decoded=replay.feed((root/'raw.bin').read_bytes())
            self.assertEqual([x.sequence for x in decoded],list(range(10000)))
            self.assertEqual(replay.errors,0);self.assertEqual(health['raw_queue_drops'],0)
            self.assertEqual(health['raw_bytes_submitted'],health['raw_bytes_written']);self.assertEqual(len(received),10000)
        os.close(master);os.close(slave)


if __name__ == "__main__":
    unittest.main()
