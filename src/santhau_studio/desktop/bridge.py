"""Qt boundary for the studio screens: slots in, JSON text out (QML-friendly).

Parsing/serialisation lives here so `desktop/backend.py` stays importable without Qt and its
queries stay testable as plain Python.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from santhau_studio.desktop.backend import StudioBackend
from santhau_studio.desktop.operations import MaintenanceProcess
from santhau_studio.desktop.preferences import save_data_root
from santhau_studio.desktop.supervisor import WorkerSupervisor
from santhau_studio.platform_win.process import (
    acquire_single_instance,
    release_single_instance,
    single_instance_name,
)


class BackendBridge(QObject):
    dataRootChanged = Signal()  # noqa: N815 - QML signal naming

    def __init__(self, backend: StudioBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.supervisor = WorkerSupervisor(backend.data_root)
        self.maintenance = MaintenanceProcess(self)

    @staticmethod
    def _dump(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _payload(text: str) -> dict:
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @Slot(result=str)
    def dataRootPath(self) -> str:
        return str(self.backend.data_root)

    @Slot(result=str)
    def appVersion(self) -> str:
        return self._dump(self.backend.onboarding()["app_version"])

    @Slot(result=str)
    def onboarding(self) -> str:
        return self._dump(self.backend.onboarding())

    @Slot(str, result=str)
    def validateDataRoot(self, path: str) -> str:
        return self._dump(self.backend.validate_data_root(path))

    @Slot(str, result=str)
    def selectDataRoot(self, path: str) -> str:
        if self.maintenance.state.get("running"):
            return self._dump({"ok": False, "error": "Chờ tác vụ bảo trì hoàn tất trước khi đổi thư mục."})
        result = self.backend.validate_data_root(path)
        if not result["ok"]:
            return self._dump(result)
        selected = Path(result["path"])
        old_name = single_instance_name(str(self.backend.data_root))
        new_name = single_instance_name(str(selected))
        if not acquire_single_instance(new_name):
            return self._dump({"ok": False, "error": "Thư mục này đang mở trong cửa sổ khác."})
        try:
            save_data_root(selected)
        except OSError as exc:
            if old_name != new_name:
                release_single_instance(new_name)
            return self._dump({"ok": False, "error": str(exc)})
        self.backend = StudioBackend(selected)
        self.supervisor = WorkerSupervisor(selected)
        if old_name != new_name:
            release_single_instance(old_name)
        self.dataRootChanged.emit()
        return self._dump({"ok": True, "path": str(selected)})

    @Slot(result=str)
    def controlCenter(self) -> str:
        return self._dump(self.backend.control_center())

    @Slot(result=str)
    def sources(self) -> str:
        return self._dump(self.backend.sources())

    @Slot(result=str)
    def jobs(self) -> str:
        return self._dump(self.backend.jobs())

    @Slot(result=str)
    def startWorker(self) -> str:
        return self._dump(self.supervisor.ensure_running())

    @Slot(str, str, result=str)
    def workerCommand(self, command: str, job_id: str) -> str:
        return self._dump(self.backend.worker_command(command, job_id or None))

    @Slot(str, result=str)
    def jobStatus(self, job_id: str) -> str:
        return self._dump(self.backend.job_status(job_id))

    @Slot(str, result=str)
    def enqueueCollection(self, payload_json: str) -> str:
        return self._dump(self.backend.enqueue_collection(self._payload(payload_json)))

    @Slot(str, result=str)
    def coverage(self, family: str) -> str:
        return self._dump(self.backend.coverage(family or None))

    @Slot(str, result=str)
    def search(self, payload_json: str) -> str:
        return self._dump(self.backend.search(self._payload(payload_json)))

    @Slot(int, result=str)
    def recordDetail(self, record_id: int) -> str:
        return self._dump(self.backend.record_detail(int(record_id)))

    @Slot(result=str)
    def deepQueue(self) -> str:
        return self._dump(self.backend.deep_queue())

    @Slot(result=str)
    def integrity(self) -> str:
        return self._dump(self.backend.integrity())

    @Slot(result=str)
    def settings(self) -> str:
        return self._dump(self.backend.settings())

    @Slot(str, str, result=str)
    def startMaintenance(self, action: str, source: str) -> str:
        return self._dump(self.maintenance.start(action, self.backend.data_root, source))

    @Slot(result=str)
    def maintenanceStatus(self) -> str:
        return self._dump(self.maintenance.state)
