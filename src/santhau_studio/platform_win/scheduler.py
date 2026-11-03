"""Task Scheduler integration, opt-in only (§2.3).

The schedule is two daily triggers, 08:00 and 20:00, on one task whose `MultipleInstancesPolicy`
is `IgnoreNew` — a 20:00 launch must never stack a second collector on a machine where the
morning run is still busy. The task name is scoped to the data root, so two data roots get two
independent tasks and neither silently overwrites the other.

Nothing here runs unless the user opts in from Settings (UI-10); `create()` and `delete()` are the
only two ways to change the machine, and `plan()` is what the UI shows before asking.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

TASK_PREFIX = "SanThauDataStudio"
TRIGGER_TIMES = ("08:00", "20:00")
_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Săn Thầu Data Studio — thu thập định kỳ 08:00 và 20:00 (chỉ khi người dùng bật)</Description>
  </RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""
_TRIGGER = (
    '<CalendarTrigger><StartBoundary>2026-01-01T{time}:00</StartBoundary>'
    "<Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    "</CalendarTrigger>"
)


class SchedulerError(RuntimeError):
    """schtasks refused the change; the message carries its own exit code."""


@dataclass(frozen=True)
class SchedulePlan:
    task_name: str
    times: tuple[str, ...]
    command: str
    arguments: str
    working_dir: str
    xml: str


def task_name(data_root: Path) -> str:
    folded = str(Path(data_root).resolve()).casefold().encode("utf-8")
    return f"{TASK_PREFIX}-{hashlib.sha256(folded).hexdigest()[:12]}"


def plan(data_root: Path, executable: Path) -> SchedulePlan:
    root = Path(data_root).resolve()
    plan_xml = _TASK_XML.format(
        triggers="".join(_TRIGGER.format(time=time) for time in TRIGGER_TIMES),
        command=_xml_escape(str(Path(executable).resolve())),
        arguments=_xml_escape(f'--data-root "{root}" --scheduled --profile default --idle-exit-seconds 600'),
        working_dir=_xml_escape(str(root.parent)),
    )
    return SchedulePlan(
        task_name=task_name(root),
        times=TRIGGER_TIMES,
        command=str(Path(executable).resolve()),
        arguments=f'--data-root "{root}" --scheduled --profile default --idle-exit-seconds 600',
        working_dir=str(root.parent),
        xml=plan_xml,
    )


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def create(data_root: Path, executable: Path) -> SchedulePlan:
    """Register the task. Requires an explicit user action; never called on startup."""
    one = plan(data_root, executable)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as handle:
        handle.write(one.xml)
        xml_path = Path(handle.name)
    try:
        _run(["schtasks", "/Create", "/TN", one.task_name, "/XML", str(xml_path), "/F"])
    finally:
        xml_path.unlink(missing_ok=True)
    return one


def delete(data_root: Path) -> bool:
    """Remove the task; True when it existed and was deleted."""
    result = _run(["schtasks", "/Query", "/TN", task_name(data_root)], check=False)
    if result.returncode != 0:
        return False
    _run(["schtasks", "/Delete", "/TN", task_name(data_root), "/F"])
    return True


def query(data_root: Path) -> dict:
    """Current registration state for the Settings screen."""
    name = task_name(data_root)
    result = _run(["schtasks", "/Query", "/TN", name, "/FO", "LIST"], check=False)
    present = result.returncode == 0
    return {"task_name": name, "present": present, "detail": result.stdout if present else None}


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if check and result.returncode != 0:
        raise SchedulerError(f"{' '.join(command[:2])} failed ({result.returncode}): {result.stderr.strip()[:300]}")
    return result


def parse_plan(plan_xml: str) -> ElementTree.Element:
    """Parsed view for tests: proves the XML says what the plan claims."""
    return ElementTree.fromstring(plan_xml)
