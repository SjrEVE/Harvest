"""Qt boundary for the studio screens: slots in, JSON text out (QML-friendly).

Parsing/serialisation lives here so `desktop/backend.py` stays importable without Qt and its
queries stay testable as plain Python.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Slot

from santhau_studio.desktop.backend import StudioBackend


class BackendBridge(QObject):
    def __init__(self, backend: StudioBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend

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

    @Slot(result=str)
    def controlCenter(self) -> str:
        return self._dump(self.backend.control_center())

    @Slot(result=str)
    def sources(self) -> str:
        return self._dump(self.backend.sources())

    @Slot(result=str)
    def jobs(self) -> str:
        return self._dump(self.backend.jobs())

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
