"""Collector-only CLI. No GUI, search, dashboard or serving snapshot generation."""

import argparse
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

from santhau_studio.collectors.live_builder import live_builder
from santhau_studio.collectors.network_policy import scrub_exception
from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.linux.config import data_file, initialize, make_jobs
from santhau_studio.storage.warehouse import connect_read_only
from santhau_studio.workers.jobs import WorkerController, WorkerLoop, enqueue_job


def default_root():
    return Path(
        os.environ.get("SANTHAU_DATA_ROOT")
        or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        / "santhau-collector"
    )


def output(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


def make_loop(root, *, offline=False, idle_exit=None):
    from santhau_studio.collectors.cgttrg import public_spec
    from santhau_studio.collectors.public_deep import PublicDeepFetcher
    from santhau_studio.linux import fixture

    if offline and not fixture.enabled(root):
        raise ValueError("Kho thử không hợp lệ; từ chối chạy.")
    if not offline and (root / "warehouse.sqlite").exists():
        with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key='evidence_class'").fetchone()
        if row and json.loads(row[0]).get("class") == fixture.PROFILE:
            raise ValueError("Kho synthetic chỉ chạy với --offline-test; không gọi nguồn thật.")
    if offline:
        from santhau_studio.linux.deep_fixture import OfflineDeepFetcher
    return WorkerLoop(
        root,
        page_source_builder=fixture.builder if offline else live_builder,
        idle_exit_seconds=idle_exit,
        publish_serving=False,
        resume_on_shutdown=True,
        deep_factory=OfflineDeepFetcher if offline else PublicDeepFetcher,
        deep_specs=public_spec,
    )


def run_worker(root, *, offline=False, idle_exit=None):
    if not sys.platform.startswith("linux"):
        raise ValueError("Dịch vụ nền này chỉ dành cho Linux.")
    from santhau_studio.linux import control

    loop = make_loop(root, offline=offline, idle_exit=idle_exit)
    controller = WorkerController(loop)

    def handle(payload, uid):
        if payload.get("op") == "status" and not payload.get("job_id"):
            runtime = controller({"op": "ping"}, uid)["runtime"]
            return {
                "ok": True,
                "running": True,
                "pid": os.getpid(),
                "job_id": loop._current.job_id if loop._current else None,
                "paused_jobs": sorted(loop._paused_jobs),
                "progress": runtime["progress"],
                "runtime": runtime,
            }
        if (
            payload.get("op") == "command"
            and not payload.get("job_id")
            and payload.get("command") != "FORCE_TERMINATE"
        ):
            candidates = (
                sorted(loop._paused_jobs)
                if payload.get("command") == "RESUME"
                else ([loop._current.job_id] if loop._current else [])
            )
            if len(candidates) != 1:
                return {"ok": False, "error": "Hãy chỉ rõ --job; không có đúng một tác vụ phù hợp."}
            payload = {**payload, "job_id": candidates[0]}
        return controller(payload, uid)

    server = control.ControlServer(root, handle)
    stop = threading.Event()
    worker = threading.Thread(target=loop.run, name="collector")
    ipc = threading.Thread(target=server.serve, args=(stop,), name="control")
    old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in old_handlers:
        signal.signal(sig, lambda *_: loop.flag.request_shutdown())
    try:
        worker.start()
        ipc.start()
        output({"ok": True, "pid": os.getpid(), "data_root": str(root), "collector_only": True})
        last_log = 0
        while worker.is_alive():
            worker.join(0.2)
            if time.monotonic() - last_log >= 30:
                output({"progress": loop.progress, "runtime": loop.runtime_status()})
                last_log = time.monotonic()
    finally:
        stop.set()
        if ipc.is_alive():
            ipc.join(4)
        server.close()
        controller.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    if loop.last_error:
        output({"ok": False, "error": scrub_exception(RuntimeError(loop.last_error))})
    return loop.exit_code


def self_test(root):
    from santhau_studio.linux import fixture

    fixture.initialize_fixture(root)

    def collect():
        for job in make_jobs(root, first="2026-01-01", last="2026-01-08"):
            enqueue_job(root, job)
        loop = WorkerLoop(
            root,
            page_source_builder=fixture.builder,
            publish_serving=False,
            resume_on_shutdown=True,
            idle_exit_seconds=0.02,
            poll_seconds=0.002,
        )
        loop.run()
        if loop.exit_code:
            raise RuntimeError(loop.last_error)
        with closing(connect_read_only(root / "warehouse.sqlite")) as conn:
            counts = {
                t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in (
                    "source_records",
                    "source_record_versions",
                    "observations",
                    "fetch_requests",
                    "coverage_proofs",
                )
            }
            if (
                conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or conn.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise RuntimeError("Kiểm tra toàn vẹn kho thử thất bại.")
        return counts

    first = collect()
    second = collect()
    forbidden = [
        name
        for name in sys.modules
        if name.startswith(
            (
                "PySide6",
                "santhau_studio.desktop",
                "santhau_studio.storage.serving",
                "santhau_studio.storage.read_model",
            )
        )
    ]
    if (
        first != second
        or first["source_records"] != 384
        or list(root.glob("serving*"))
        or forbidden
    ):
        raise RuntimeError(f"Kiểm tra collector-only thất bại: {first}, {second}, {forbidden}")
    output(
        {
            "ok": True,
            "evidence_class": "SYNTHETIC_OFFLINE",
            "counts": first,
            "rerun_added_records": 0,
            "network_requests": 0,
            "serving_created": False,
            "forbidden_imports": forbidden,
        }
    )
    return 0


def parser():
    cli = argparse.ArgumentParser(
        prog="santhau-collector",
        description="Bộ thu thập Linux: sáu loại, theo tuần, không giao diện/thống kê.",
    )
    cli.add_argument("--version", action="version", version="Săn Thầu Collector 0.0.1-RC3-9 (Linux x86-64)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-root",
        type=Path,
        default=default_root(),
        help="Thư mục kho dữ liệu, nằm ngoài thư mục cài",
    )
    sub = cli.add_subparsers(dest="command", required=True)
    init = sub.add_parser(
        "init",
        parents=[common],
        help="Khởi tạo kho và nạp cấu hình nguồn đã xác minh; không gọi mạng",
    )
    init.add_argument("--source-profile", type=Path)
    init.add_argument("--no-profile", action="store_true")
    deep = sub.add_parser("deep-config", parents=[common], help="Bật/tắt chạy bù CGTTRG; dừng worker trước khi đổi")
    choice = deep.add_mutually_exclusive_group(required=True)
    choice.add_argument("--enable", action="store_true")
    choice.add_argument("--disable", action="store_true")
    deep.add_argument("--batch-requests", type=int, default=30)
    sub.add_parser("deep-status", parents=[common], help="Đọc checkpoint và lỗi thu thập chi tiết")
    sub.add_parser("self-test-deep", parents=[common], help="Thử deep collector ngoại tuyến trên thư mục mới")
    replay = sub.add_parser("replay-deep", parents=[common], help="Parse lại raw bằng phiên bản mới; mặc định chỉ xem trước, không HTTP")
    replay_choice = replay.add_mutually_exclusive_group()
    replay_choice.add_argument("--execute", action="store_true")
    replay_choice.add_argument("--dry-run", action="store_true")
    replay.add_argument("--record", type=int, action="append", dest="record_ids")
    replay.add_argument("--limit", type=int, default=100)
    scope = sub.add_parser("configure-scope", parents=[common], help="Phạm vi riêng của kho; không tự gọi nguồn")
    scope.add_argument("--from", dest="first", required=True)
    scope.add_argument("--to", dest="last", required=True)
    scope.add_argument("--family", choices=(*FAMILY_ORDER, "ALL"), default="ALL")
    scope.add_argument("--enable-repair", action="store_true")
    scope.add_argument("--enable-reconcile", action="store_true")
    scope.add_argument("--quota-per-day", type=int, default=24)
    scope.add_argument("--reconcile-quota-per-day", type=int, default=2)
    sub.add_parser("coverage-status", parents=[common], help="Độ phủ toàn phạm vi; không sửa dữ liệu")
    repair = sub.add_parser("repair-gaps", parents=[common], help="Xem khoảng thiếu; mặc định dry-run, execute chỉ ghi lịch")
    mode = repair.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    enqueue = sub.add_parser(
        "enqueue", parents=[common], help="Xếp hàng; tất cả sáu loại mặc định chia theo tuần"
    )
    enqueue.add_argument("--from", dest="first", help="Ngày đầu YYYY-MM-DD")
    enqueue.add_argument("--to", dest="last", help="Ngày cuối YYYY-MM-DD, bao gồm ngày này")
    enqueue.add_argument("--family", choices=(*FAMILY_ORDER, "ALL"), default="ALL")
    enqueue.add_argument(
        "--mode", choices=("BACKFILL", "INCREMENTAL", "RECONCILE"), default="BACKFILL"
    )
    enqueue.add_argument("--max-pages", type=int, default=1000)
    enqueue.add_argument("--max-requests", type=int, default=3000)
    enqueue.add_argument("--max-seconds", type=int, default=3600)
    enqueue.add_argument("--pacing-seconds", type=float, default=3)
    enqueue.add_argument("--connect-timeout-seconds", type=float, default=15)
    enqueue.add_argument("--max-attempts", type=int, default=4)
    run = sub.add_parser(
        "run", parents=[common], help="Chạy bộ thu thập; SIGTERM lưu và xếp lại tác vụ dở"
    )
    run.add_argument("--idle-exit-seconds", type=float)
    run.add_argument("--offline-test", action="store_true", help=argparse.SUPPRESS)
    auto = sub.add_parser(
        "auto", parents=[common],
        help="Mot nut: keo nguoc tu hom nay ve date_from, tu ne ngay da co du lieu",
    )
    auto.add_argument("--execute", action="store_true",
                      help="Xep hang va chay that; mac dinh chi xem truoc, khong goi mang")
    auto.add_argument("--no-run", action="store_true",
                      help="Chi xep hang, khong chay worker (dung khi da co systemd)")
    for name in ("status", "pause", "resume", "cancel", "stop"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--job")
    sub.add_parser(
        "self-test",
        parents=[common],
        help="Thử ngoại tuyến trên một thư mục mới, không dùng dữ liệu thật",
    )
    return cli


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    if sys.platform.startswith("linux"):
        os.umask(0o077)
    root = args.data_root.expanduser().resolve()
    try:
        if args.command in ("configure-scope", "coverage-status", "repair-gaps"):
            from santhau_studio.linux.repair import configure_scope, coverage_report, repair_gaps
            if args.command == "configure-scope":
                result = configure_scope(root, first=args.first, last=args.last,
                    families=FAMILY_ORDER if args.family == "ALL" else (args.family,),
                    policy={"repair_enabled": args.enable_repair, "reconcile_enabled": args.enable_reconcile,
                            "quota_per_day": args.quota_per_day, "reconcile_quota_per_day": args.reconcile_quota_per_day})
            elif args.command == "coverage-status":
                result = coverage_report(root)
            else:
                result = repair_gaps(root, execute=args.execute)
            output(result)
            return 0
        if args.command == "replay-deep":
            from santhau_studio.workers.deep_replay import replay
            output(replay(root, execute=args.execute, record_ids=args.record_ids, limit=args.limit))
            return 0
        if args.command == "deep-config":
            from santhau_studio.linux.deep import configure_deep
            output(configure_deep(root, enabled=args.enable, batch_requests=args.batch_requests))
            return 0
        if args.command == "deep-status":
            from santhau_studio.linux.deep import deep_status
            output(deep_status(root))
            return 0
        if args.command == "self-test-deep":
            from santhau_studio.linux.deep_fixture import self_test as deep_self_test
            output(deep_self_test(root))
            return 0
        if args.command == "init":
            initialize(
                root,
                None if args.no_profile else args.source_profile or data_file("egp-profile.json"),
            )
            output({"ok": True, "data_root": str(root), "network_requests": 0})
            return 0
        if args.command == "auto":
            from santhau_studio.linux.auto_pull import auto_pull

            output(auto_pull(root, execute=args.execute, run=not args.no_run))
            return 0
        if args.command == "enqueue":
            limits = {
                key: getattr(args, key)
                for key in ("max_pages", "max_requests", "max_seconds", "pacing_seconds", "connect_timeout_seconds", "max_attempts")
            }
            jobs = make_jobs(
                root,
                mode=args.mode,
                family=args.family,
                first=args.first,
                last=args.last,
                limits=limits,
            )
            paths = [enqueue_job(root, job) for job in jobs]
            output(
                {
                    "ok": True,
                    "job_ids": [j.job_id for j in jobs],
                    "queued": [p.name for p in paths],
                    "cadence": jobs[0].scope.get("cadence", "RANGE"),
                }
            )
            return 0
        if args.command == "run":
            return run_worker(root, offline=args.offline_test, idle_exit=args.idle_exit_seconds)
        if args.command == "self-test":
            return self_test(root)
        from santhau_studio.linux.control import request

        payload = (
            {"op": "status", "job_id": args.job}
            if args.command == "status"
            else {
                "op": "command",
                "job_id": args.job,
                "command": {
                    "pause": "PAUSE",
                    "resume": "RESUME",
                    "cancel": "STOP_GRACEFUL",
                    "stop": "FORCE_TERMINATE",
                }[args.command],
            }
        )
        try:
            result = request(root, payload)
        except (FileNotFoundError, ConnectionRefusedError):
            if args.command != "status":
                raise ValueError("Bộ thu thập chưa chạy.") from None
            result = {"ok": True, "running": False}
        output(result)
        return 0 if result.get("ok") else 2
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        output({"ok": False, "error": scrub_exception(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
