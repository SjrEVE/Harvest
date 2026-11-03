"""The packaged layout differs by one directory level from the source tree (§2.2 topology).

`WarehouseWriter` used a fixed `parents[3]` to find the frozen DDL; in the Nuitka deployment the
package sits directly under the app folder, so that formula pointed one level above it and the
packaged worker died at startup on a missing `docs/ddl/g0_b_proposed.sql`. This pins the search.
"""

from __future__ import annotations

from pathlib import Path

from santhau_studio.storage.warehouse import find_schema


def _make_tree(root: Path, module_dir: Path) -> Path:
    ddl = root / "docs" / "ddl" / "g0_b_proposed.sql"
    ddl.parent.mkdir(parents=True)
    ddl.write_text("-- fixture", encoding="utf-8")
    module_dir.mkdir(parents=True, exist_ok=True)
    return ddl


def test_finds_ddl_in_source_tree_layout(tmp_path: Path) -> None:
    root = tmp_path
    module_dir = root / "src" / "santhau_studio" / "storage"
    ddl = _make_tree(root, module_dir)
    assert find_schema(module_dir) == ddl


def test_finds_ddl_in_packaged_layout(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    module_dir = stage / "santhau_studio" / "storage"
    ddl = _make_tree(stage, module_dir)
    assert find_schema(module_dir) == ddl


def test_returns_none_when_the_ddl_is_absent(tmp_path: Path) -> None:
    module_dir = tmp_path / "pkg" / "santhau_studio" / "storage"
    module_dir.mkdir(parents=True)
    assert find_schema(module_dir) is None
