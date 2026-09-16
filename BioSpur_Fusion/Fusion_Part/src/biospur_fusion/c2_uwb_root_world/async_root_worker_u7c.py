"""U7C lifecycle-qualified wrapper around the exact U7B ROOT engine.

The spawned child owns all mutable estimator/coordinator state.  Construction
returns only after that child has imported its numerical owners and constructed
the engine.  Cold start is therefore outside the producer's capture epoch.
"""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import pickle
import queue
import time

from biospur_fusion.c2_uwb_root_world.async_root_worker import (
    _OwnedEngine,
    RootWorkerConfig,
    RootWorkerEvent,
    THREAD_ENV,
    run_synchronous,
)


def _worker_main(config, input_queue, output_queue):
    if any(os.environ.get(key) != value for key, value in THREAD_ENV.items()):
        output_queue.put(("ERROR", "thread environment not frozen"))
        return
    engine = _OwnedEngine(config)
    output_queue.put(("READY", {"ready_ns": time.perf_counter_ns()}))
    processed = 0
    try:
        while True:
            item = input_queue.get()
            if item is None:
                break
            event_blob, submitted_ns = item
            event = pickle.loads(event_blob)
            result = engine.process(event, submitted_ns)
            processed += 1
            if result["kind"] == "UWB":
                output_queue.put(("RESULT", result))
        final = engine.final()
        final.update({"sentinel_received": True, "processed_event_count": processed})
        output_queue.put(("FINAL", final))
    except BaseException as exc:
        output_queue.put(("ERROR", f"{type(exc).__name__}: {exc}"))


class AsyncRootWorker:
    """One long-lived spawned ROOT owner with a READY lifecycle barrier."""

    def __init__(self, config: RootWorkerConfig):
        self.config = config
        self._last = -math.inf
        self._closed = False
        self.queue_high_watermark = 0
        self.submit_blocking_ms = []
        for key, value in THREAD_ENV.items():
            os.environ[key] = value
        context = mp.get_context("spawn")
        self._input = context.Queue(maxsize=config.queue_capacity)
        self._output = context.Queue(maxsize=config.queue_capacity)
        self._process = context.Process(
            target=_worker_main, args=(config, self._input, self._output)
        )
        started_ns = time.perf_counter_ns()
        self._process.start()
        try:
            kind, payload = self._output.get(timeout=30.0)
        except queue.Empty as exc:
            self._process.terminate()
            self._process.join(timeout=5.0)
            raise RuntimeError("worker READY timeout") from exc
        if kind != "READY":
            self._process.join(timeout=1.0)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=5.0)
            raise RuntimeError(payload)
        self.cold_start_ms = (time.perf_counter_ns() - started_ns) * 1e-6
        self.child_ready_ns = int(payload["ready_ns"])

    @property
    def pid(self):
        return self._process.pid

    def submit(self, event: RootWorkerEvent):
        if self._closed:
            raise RuntimeError("worker input closed")
        if event.availability_time_s < self._last:
            raise ValueError("producer event order reversed")
        self._last = event.availability_time_s
        submitted_ns = time.perf_counter_ns()
        started_ns = submitted_ns
        self._input.put(
            (pickle.dumps(event, protocol=5), submitted_ns), timeout=5.0
        )
        self.submit_blocking_ms.append((time.perf_counter_ns() - started_ns) * 1e-6)
        try:
            self.queue_high_watermark = max(
                self.queue_high_watermark, self._input.qsize()
            )
        except NotImplementedError:
            pass

    def close_and_collect(self, expected_results: int, timeout_s: float = 30.0):
        self._closed = True
        self._input.put(None, timeout=5.0)
        results = []
        final = None
        deadline = time.monotonic() + timeout_s
        while final is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise RuntimeError("worker result timeout")
            kind, payload = self._output.get(timeout=remaining)
            if kind == "ERROR":
                raise RuntimeError(payload)
            if kind == "RESULT":
                results.append(payload)
            elif kind == "FINAL":
                final = payload
            else:
                raise RuntimeError(f"unexpected worker message {kind}")
        self._process.join(timeout=5.0)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5.0)
            raise RuntimeError("worker did not drain")
        if self._process.exitcode != 0 or len(results) != expected_results:
            raise RuntimeError("worker result loss")
        try:
            input_qsize = self._input.qsize()
        except NotImplementedError:
            input_qsize = None
        final.update(
            {
                "input_queue_size_after_join": input_qsize,
                "process_exitcode": self._process.exitcode,
                "process_alive_after_join": self._process.is_alive(),
            }
        )
        self._input.close()
        self._input.join_thread()
        self._output.close()
        self._output.join_thread()
        return results, final


__all__ = [
    "AsyncRootWorker",
    "RootWorkerConfig",
    "RootWorkerEvent",
    "THREAD_ENV",
    "run_synchronous",
]
