import json
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.bridge import BackendBridge


def test_slow_read_does_not_block_events_and_stale_request_is_discarded(tmp_path):
    app = QGuiApplication.instance() or QGuiApplication([])
    backend = StudioBackend(tmp_path)
    def slow():
        time.sleep(.15)
        return {'marker': 'old'}
    backend.control_center = slow
    bridge = BackendBridge(backend)
    seen, ticks = [], []
    bridge.readReady.connect(lambda _key, _request_id, value: seen.append(json.loads(value)))
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    began = time.perf_counter()
    bridge.requestRead('dashboard', 'control_center', '{}')
    assert time.perf_counter() - began < .05
    bridge.invalidateReads()
    QTest.qWait(250)
    assert len(ticks) >= 5 and not seen
    bridge.requestRead('dashboard', 'onboarding', '{}')
    for _ in range(30):
        if seen:
            break
        QTest.qWait(50)
    assert len(seen) == 1
    timer.stop()
    bridge.pool.waitForDone()
    assert app is not None
