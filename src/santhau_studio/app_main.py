"""GUI entry point: the studio shell (Milestone F).

The QML screens read through `BackendBridge` only — serving snapshot for data, the worker pipe
for control — and the GUI process never opens the warehouse for writing. `--smoke <png>` keeps
the packaging proof from A8: it renders the real shell against a real data root, captures the
window and reports PASS/FAIL without leaving an app open.
"""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from santhau_studio.desktop.backend import StudioBackend, default_data_root
from santhau_studio.desktop.bridge import BackendBridge

APP_TITLE = "Săn Thầu Data Studio"


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


def qml_entry_point() -> Path | None:
    """Find Main.qml in a packaged layout first, then the source tree."""
    candidates = (
        Path(sys.executable).parent / "qml" / "Main.qml",
        Path(__file__).resolve().parent / "desktop" / "qml" / "Main.qml",
    )
    return next((path for path in candidates if path.exists()), None)


class WindowGrabber(QObject):
    """Expose QML window capture to the smoke path without QML-side knowledge of the test."""

    def __init__(self, app: QGuiApplication, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app

    def grab(self, root: object, shot: Path) -> str:
        root.raise_()
        root.requestActivate()
        for _ in range(40):
            self._app.processEvents()
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


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdout()
    argv = list(sys.argv if argv is None else argv)
    data_root, argv = _take_data_root(argv)
    smoke_shot = _take_smoke_args(argv)

    app = QGuiApplication(argv)
    app.setApplicationName("SanThauDataStudio")
    backend = BackendBridge(StudioBackend(data_root))
    qml_path = qml_entry_point()
    if qml_path is None:
        print("missing QML entry point (looked next to the exe and in the source tree)", file=sys.stderr)
        return 2

    engine = QQmlApplicationEngine(app)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("appTitle", APP_TITLE)
    engine.addImportPath(str(qml_path.parent))
    engine.load(qml_path.as_uri())
    if not engine.rootObjects():
        print("QML load failed", file=sys.stderr)
        return 3

    root = engine.rootObjects()[0]
    if smoke_shot is not None:
        return _run_smoke(app, root, backend, Path(smoke_shot))
    return app.exec()


def _take_data_root(argv: list[str]) -> tuple[Path | None, list[str]]:
    if "--data-root" not in argv:
        return None, argv
    index = argv.index("--data-root")
    argv = [arg for i, arg in enumerate(argv) if i != index]
    if index < len(argv) and not argv[index].startswith("-"):
        return Path(argv.pop(index)), argv
    return default_data_root(), argv


def _take_smoke_args(argv: list[str]) -> str | None:
    """Remove --smoke [path] from argv so Qt never sees our flags."""
    if "--smoke" not in argv:
        return None
    index = argv.index("--smoke")
    argv.pop(index)
    if index < len(argv) and not argv[index].startswith("-"):
        return argv.pop(index)
    return "smoke.png"


def _run_smoke(app: QGuiApplication, root: object, backend: BackendBridge, shot: Path) -> int:
    """Render proof: the shell loaded, the backend answered from a real data root, pixels exist."""
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    onboarding = backend.onboarding()
    grabbed = WindowGrabber(app).grab(root, shot)
    ok = bool(onboarding) and '"data_root"' in onboarding and shot.exists() and bool(grabbed)
    print(f"smoke onboarding: {onboarding[:300]}")
    print(f"smoke screenshot: {shot} exists={shot.exists()} size={grabbed}")
    print(f"smoke verdict: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
