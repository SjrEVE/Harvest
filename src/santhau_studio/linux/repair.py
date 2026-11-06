"""Maintenance CLI operations; previews never migrate, enqueue or call HTTP."""

from contextlib import closing
from pathlib import Path

from santhau_studio.storage import collection_scope
from santhau_studio.storage.warehouse import WarehouseWriter, connect_read_only


def configure_scope(root, **kwargs):
    with closing(
        WarehouseWriter.open(Path(root), create_schema=True, role="configure-scope")
    ) as writer:
        result = collection_scope.configure(writer.conn, **kwargs)
        writer.conn.commit()
        return result


def coverage_report(root):
    with closing(connect_read_only(Path(root) / "warehouse.sqlite")) as conn:
        conn.execute("BEGIN")
        return collection_scope.report(conn, Path(root))


def repair_gaps(root, *, execute=False):
    from santhau_studio.workers.scoped_maintenance import discover

    root = Path(root)
    if not execute:
        with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
            conn.execute("BEGIN")
            return discover(conn, root, execute=False)
    with closing(
        WarehouseWriter.open(root, create_schema=True, role="repair-configuration")
    ) as writer:
        return discover(writer.conn, root, execute=True)
