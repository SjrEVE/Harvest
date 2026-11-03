"""Opt-in local acceptance measurements; disabled in normal operation."""

import ctypes
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Slot


def process_counters():
    class Memory(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("faults", ctypes.c_ulong),
            *[
                (name, ctypes.c_size_t)
                for name in ("peak", "working", "pqp", "qp", "pqnp", "qnp", "pagefile", "peakfile")
            ],
        ]

    class IO(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "read_ops",
                "write_ops",
                "other_ops",
                "read_bytes",
                "write_bytes",
                "other_bytes",
            )
        ]

    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    handle = kernel.GetCurrentProcess()
    memory = Memory()
    memory.cb = ctypes.sizeof(Memory)
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [  # secret-scan:allow Win32 signature
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]  # secret-scan:allow Win32 API signature
    ctypes.windll.psapi.GetProcessMemoryInfo(  # secret-scan:allow Win32 API
        handle, ctypes.byref(memory), memory.cb
    )  # secret-scan:allow Win32 API
    io = IO()
    kernel.GetProcessIoCounters.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.GetProcessIoCounters(handle, ctypes.byref(io))
    return {
        "working_set_bytes": memory.working,
        "peak_working_set_bytes": memory.peak,
        "io_read_bytes": io.read_bytes,
        "io_write_bytes": io.write_bytes,
    }


class PerformanceProbe(QObject):
    def __init__(self, window, path, started, bridge=None):
        super().__init__(window)
        self.path = Path(path)
        self.started = started
        self.last = time.perf_counter()
        self.report = {
            "pid": os.getpid(),
            "first_frame_ms": None,
            "max_event_stall_ms": 0,
            "event_samples": 0,
            "request_to_frame_ms": {},
        }
        self.window = window
        self.reads = {}
        self.paint_pending = []
        if bridge is not None:
            bridge.readStarted.connect(self.read_started)
            bridge.readReady.connect(self.read_ready)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(1000)
        self.flush_timer.timeout.connect(self.flush)
        self.flush_timer.start()
        window.frameSwapped.connect(self.first_frame)

    @Slot(str, int, str)
    def read_started(self, key, serial, method):
        self.reads[key] = (serial, method, time.perf_counter())

    @Slot(str, int, str)
    def read_ready(self, key, serial, _value):
        measurement = self.reads.pop(key, None)
        page = {"page2": 1, "explorer": 5, "detail": 6, "history": 6}.get(key)
        if measurement and measurement[0] == serial and self.window.property("pageIndex") == page:
            self.paint_pending.append(measurement[1:])
            self.window.update()

    @Slot()
    def first_frame(self):
        now = time.perf_counter()
        for method, started in self.paint_pending:
            samples = self.report["request_to_frame_ms"].setdefault(method, [])
            samples.append((now - started) * 1000)
            del samples[:-1000]
        self.paint_pending.clear()
        if self.report["first_frame_ms"] is None:
            self.report["first_frame_ms"] = (time.perf_counter() - self.started) * 1000
            self.flush()

    @Slot()
    def tick(self):
        now = time.perf_counter()
        if self.report["first_frame_ms"] is not None:
            self.report["max_event_stall_ms"] = max(
                self.report["max_event_stall_ms"], (now - self.last) * 1000 - 16
            )
            self.report["event_samples"] += 1
        self.last = now

    @Slot()
    def flush(self):
        self.report.update(process_counters())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
