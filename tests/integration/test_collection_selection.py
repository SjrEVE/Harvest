"""Paused and running jobs must remain independently selectable after the redesign."""
import time
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlApplicationEngine
from test_collection_date_ui import click, spin
from test_desktop_qml import _qt_app

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.bridge import BackendBridge


def test_pause_resume_targets_selected_job(tmp_path):
    app = _qt_app()
    backend = StudioBackend(tmp_path)
    backend.jobs = lambda: {"active":[
        {"job_id":"paused-first","families":["TBMT"],"lifecycle":"PAUSED","can_pause":False,"can_resume":True,"can_stop":True},
        {"job_id":"running-second","families":["KHLCNT"],"lifecycle":"RUNNING","can_pause":True,"can_resume":False,"can_stop":True}],
        "queued":[],"done":[],"worker":{"running":True,"pipe_ok":True}}
    commands=[]
    backend.worker_command = lambda command,job_id=None: commands.append((command,job_id)) or {"ok":True}
    bridge=BackendBridge(backend)
    engine=QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend",bridge)
    engine.rootContext().setContextProperty("appTitle","Kiểm tra chọn tác vụ")
    engine.load((Path(__file__).parents[2]/"src/santhau_studio/desktop/qml/Main.qml").as_uri())
    window=engine.rootObjects()[0]
    try:
        target=window.findChild(QObject,"collectionTarget")
        assert target is not None
        spin(app,lambda:target.property("count")==2)
        target.setProperty("currentIndex",1)
        click(window,window.findChild(QObject,"collectionPause"))
        spin(app,lambda:len(commands)==1)
        assert commands[-1]==("PAUSE","running-second")
        target.setProperty("currentIndex",0)
        click(window,window.findChild(QObject,"collectionResume"))
        spin(app,lambda:len(commands)==2)
        assert commands[-1]==("RESUME","paused-first")
    finally:
        window.close()
        bridge.invalidateReads()
        while bridge.pool.activeThreadCount():
            app.processEvents()
            time.sleep(.01)
        bridge.pool.waitForDone()
