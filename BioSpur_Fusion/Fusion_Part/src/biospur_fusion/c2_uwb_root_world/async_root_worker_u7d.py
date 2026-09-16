"""U7D READY-handshaked ROOT worker using the canonical event-v1 codec."""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import queue
import time

from biospur_fusion.c2_uwb_root_world.async_root_worker import (
    _OwnedEngine, RootWorkerConfig, RootWorkerEvent, THREAD_ENV, run_synchronous)
from biospur_fusion.c2_uwb_root_world.root_worker_event_codec import decode_event, encode_event


def _worker_main(config,input_queue,output_queue):
    if any(os.environ.get(key)!=value for key,value in THREAD_ENV.items()):
        output_queue.put(("ERROR","thread environment not frozen"));return
    engine=_OwnedEngine(config);output_queue.put(("READY",{"ready_ns":time.perf_counter_ns()}));count=0
    try:
        while True:
            item=input_queue.get()
            if item is None:break
            blob,submitted_ns=item;event=decode_event(blob);result=engine.process(event,submitted_ns);count+=1
            if result["kind"]=="UWB":output_queue.put(("RESULT",result))
        final=engine.final();final.update({"sentinel_received":True,"processed_event_count":count})
        output_queue.put(("FINAL",final))
    except BaseException as exc:output_queue.put(("ERROR",f"{type(exc).__name__}: {exc}"))


class AsyncRootWorker:
    def __init__(self,config:RootWorkerConfig):
        self.config=config;self._last=-math.inf;self._closed=False
        self.queue_high_watermark=0;self.submit_blocking_ms=[]
        for key,value in THREAD_ENV.items():os.environ[key]=value
        context=mp.get_context("spawn");self._input=context.Queue(maxsize=config.queue_capacity)
        self._output=context.Queue(maxsize=config.queue_capacity)
        self._process=context.Process(target=_worker_main,args=(config,self._input,self._output))
        started=time.perf_counter_ns();self._process.start()
        try:kind,payload=self._output.get(timeout=30.)
        except queue.Empty as exc:
            self._abort();raise RuntimeError("worker READY timeout") from exc
        if kind!="READY":self._abort();raise RuntimeError(payload)
        self.cold_start_ms=(time.perf_counter_ns()-started)*1e-6;self.child_ready_ns=int(payload["ready_ns"])

    @property
    def pid(self):return self._process.pid

    def _abort(self):
        self._closed=True
        try:self._input.put(None,timeout=.1)
        except BaseException:pass
        self._process.join(timeout=1.5)
        if self._process.is_alive():self._process.terminate();self._process.join(timeout=.4)
        for channel in (self._input,self._output):
            try:channel.close();channel.cancel_join_thread()
            except BaseException:pass

    def submit(self,event:RootWorkerEvent):
        if self._closed:raise RuntimeError("worker input closed")
        started=time.perf_counter_ns()
        try:
            blob=encode_event(event)
            if event.availability_time_s<self._last:raise ValueError("producer event order reversed")
            self._input.put((blob,started),timeout=5.)
        except BaseException:
            self._abort();raise
        self._last=event.availability_time_s
        self.submit_blocking_ms.append((time.perf_counter_ns()-started)*1e-6)
        try:self.queue_high_watermark=max(self.queue_high_watermark,self._input.qsize())
        except NotImplementedError:pass

    def close_and_collect(self,expected_results:int,timeout_s:float=30.):
        self._closed=True;self._input.put(None,timeout=5.);results=[];final=None
        deadline=time.monotonic()+timeout_s
        try:
            while final is None:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise RuntimeError("worker result timeout")
                kind,payload=self._output.get(timeout=remaining)
                if kind=="ERROR":raise RuntimeError(payload)
                if kind=="RESULT":results.append(payload)
                elif kind=="FINAL":final=payload
                else:raise RuntimeError(f"unexpected worker message {kind}")
            self._process.join(timeout=1.5)
            if self._process.is_alive():raise RuntimeError("worker did not drain")
            if self._process.exitcode!=0 or len(results)!=expected_results:raise RuntimeError("worker result loss")
            try:qsize=self._input.qsize()
            except NotImplementedError:qsize=None
            final.update({"input_queue_size_after_join":qsize,"process_exitcode":self._process.exitcode,
                "process_alive_after_join":self._process.is_alive()})
            return results,final
        except BaseException:
            self._abort();raise
        finally:
            if not self._process.is_alive():
                for channel in (self._input,self._output):
                    try:channel.close();channel.cancel_join_thread()
                    except BaseException:pass


__all__=["AsyncRootWorker","RootWorkerConfig","RootWorkerEvent","THREAD_ENV","run_synchronous"]
