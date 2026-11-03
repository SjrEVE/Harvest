"""Asynchronous collector maintenance, with one observable operation at a time."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment

from santhau_studio.desktop.supervisor import collector_command


class MaintenanceProcess(QObject):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.state: dict = {"running": False}
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._failed)

    def start(self, action: str, root: Path, source: str = "") -> dict:
        if self.state.get("running"):
            return {"ok": False, "error": "Một tác vụ đang chạy; chờ kết quả trước."}
        command = collector_command()
        if command is None:
            return {"ok": False, "error": "Không tìm thấy collector."}
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        source_root = Path(__file__).resolve().parents[2]
        if (source_root / "santhau_studio").is_dir():
            environment.insert("PYTHONPATH", str(source_root) + os.pathsep + environment.value("PYTHONPATH"))
        self.process.setProcessEnvironment(environment)
        args = [*command[1:], "--data-root", str(root), "--maintenance", action]
        if source:
            args.extend(["--source", source])
        self.state = {"running": True, "action": action}
        self.process.start(command[0], args)
        return {"ok": True, "running": True}

    def _finished(self, code: int, _status) -> None:
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        try:
            result = json.loads(output.strip().splitlines()[-1])
        except (ValueError, IndexError):
            detail = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
            result = {"ok": False, "error": detail[-400:] or f"Collector exit {code}"}
        self.state.update(result, running=False)

    def _failed(self, _error) -> None:
        self.state.update(running=False, ok=False, error=self.process.errorString())
