"""G0-A / A8 packaging spike — GUI entry point.

Deliberately minimal: its only job is to prove that a Qt Quick/QML window, a
separate headless worker process, a Nuitka standalone folder and an Inno Setup
installer can be produced and run on Windows.

It performs no network access and reads no real data. The stdout-pipe status link
here is a spike artefact, not the §2.4 named-pipe contract, which lands in G2.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

APP_TITLE = "Săn Thầu Data Studio — G0-A packaging spike"


def force_utf8_stdout() -> None:
    """Vietnamese text on a cp1258 console raises UnicodeEncodeError on write.

    A packaged GUI has no console by default, but stdout becomes real when it is
    launched from a shell or a scheduled task, so the stream is pinned to UTF-8.
    """
    stream = sys.stdout
    if stream is None:
        return
    with contextlib.suppress(AttributeError, OSError, ValueError):
        stream.reconfigure(encoding="utf-8", errors="replace")


class WorkerBridge(QObject):
    """Reads line-delimited JSON from the worker's stdout without blocking the UI thread."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: subprocess.Popen[str] | None = None
        self._last: dict[str, object] = {"state": "not-started", "note": "worker chưa được spawn"}
        self._error: str = ""

    def start(self) -> None:
        command = self._collector_command()
        if command is None:
            self._error = "không tìm thấy SanThauCollector.exe hoặc worker_main.py"
            return
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            self._error = f"spawn thất bại: {exc}"

    @staticmethod
    def _collector_command() -> list[str] | None:
        """Locate the worker by candidate path rather than by a frozen flag.

        Nuitka standalone does not set sys.frozen, so probing the on-disk layout is
        both simpler and correct for the source tree, the .dist folder and an install.
        """
        sibling = Path(sys.executable).parent / "SanThauCollector.exe"
        if sibling.exists():
            return [str(sibling)]
        module = Path(__file__).with_name("worker_main.py")
        if module.exists():
            return [sys.executable, str(module)]
        return None

    @Slot(result=str)
    def snapshot(self) -> str:
        if self._error:
            return json.dumps({"state": "error", "note": self._error}, ensure_ascii=False)
        process = self._process
        if process is None:
            return json.dumps(self._last, ensure_ascii=False)
        line = process.stdout.readline() if process.stdout else ""
        if line:
            try:
                self._last = json.loads(line)
            except json.JSONDecodeError:
                self._last = {"state": "malformed", "note": line.strip()[:120]}
        elif process.poll() is not None:
            self._last = {"state": "exited", "note": f"worker exit {process.returncode}"}
        return json.dumps(self._last, ensure_ascii=False)

    @Slot()
    def stop(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        self._process = None


def qml_entry_point() -> Path | None:
    """Find Main.qml in a packaged layout first, then the source tree."""
    candidates = (
        Path(sys.executable).parent / "qml" / "Main.qml",
        Path(__file__).resolve().parent / "desktop" / "qml" / "Main.qml",
    )
    return next((path for path in candidates if path.exists()), None)


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdout()
    argv = list(sys.argv if argv is None else argv)
    smoke_shot = _take_smoke_args(argv)
    app = QGuiApplication(argv)
    app.setApplicationName("SanThauDataStudio")
    bridge = WorkerBridge()
    qml_path = qml_entry_point()
    if qml_path is None:
        print(
            "missing QML entry point (looked next to the exe and in the source tree)",
            file=sys.stderr,
        )
        return 2

    engine = QQmlApplicationEngine(app)
    engine.rootContext().setContextProperty("workerBridge", bridge)
    engine.rootContext().setContextProperty("appTitle", APP_TITLE)
    engine.addImportPath(str(qml_path.parent))
    engine.load(qml_path.as_uri())
    if not engine.rootObjects():
        print("QML load failed", file=sys.stderr)
        return 3

    root = engine.rootObjects()[0]
    poll = QTimer(app)
    poll.setInterval(250)
    poll.timeout.connect(lambda: root.setProperty("statusJson", bridge.snapshot()))
    bridge.start()
    poll.start()
    app.aboutToQuit.connect(bridge.stop)

    if smoke_shot is not None:
        return _run_smoke(app, root, bridge, Path(smoke_shot))
    return app.exec()


def _take_smoke_args(argv: list[str]) -> str | None:
    """Remove --smoke [path] from argv so Qt never sees our spike flags."""
    if "--smoke" not in argv:
        return None
    index = argv.index("--smoke")
    argv.pop(index)
    if index < len(argv) and not argv[index].startswith("-"):
        return argv.pop(index)
    return "smoke.png"


def _run_smoke(app: QGuiApplication, root: object, bridge: WorkerBridge, shot: Path) -> int:
    """Render proof for the packaging spike: capture the window and verify the worker reported."""
    deadline = time.monotonic() + 8.0
    seen_page = False
    last = ""
    while time.monotonic() < deadline:
        app.processEvents()
        last = str(root.property("statusJson"))
        if '"page_committed"' in last:
            seen_page = True
        if '"finished"' in last:
            break
        time.sleep(0.05)
    grabbed = _grab_window(app, root, shot)
    ok = seen_page and '"finished"' in last
    print(f"smoke status: {last}")
    print(f"smoke screenshot: {shot} exists={shot.exists()} size={grabbed}")
    print(f"smoke verdict: {'PASS' if ok and grabbed else 'FAIL'}")
    bridge.stop()
    return 0 if (ok and grabbed) else 4


def _grab_window(app: QGuiApplication, root: object, shot: Path) -> str:
    """Capture the live window, or return "" when the capture cannot be trusted.

    QScreen.grabWindow(hwnd) falls back to the screen region when the window is not
    the foreground surface, so an un-raised window silently yields a screenshot of
    whatever application happens to be behind it. Raise and activate first, then
    require a non-zero win id and a geometry that matches the window.
    """
    root.raise_()
    root.requestActivate()
    for _ in range(40):
        app.processEvents()
    if not root.isVisible():
        return ""
    win_id = int(root.winId())
    if win_id == 0:
        return ""
    screen = root.screen()
    if screen is None:
        return ""
    image = screen.grabWindow(win_id).toImage()
    if image.isNull():
        return ""
    if (image.width(), image.height()) != (int(root.width()), int(root.height())):
        return ""
    image.save(str(shot))
    return f"{image.width()}x{image.height()}"


if __name__ == "__main__":
    sys.exit(main())
