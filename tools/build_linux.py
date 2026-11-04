"""Build a collector-only Linux bundle, Debian package and checksummed manifest."""

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/linux"
VERSION = "0.0.1~rc3"


def run(*args):
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("Build requires Linux x86-64")
    OUT.mkdir(parents=True, exist_ok=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    files = subprocess.check_output(
        ["git", "ls-files", "src", "packaging/linux", "tools/build_linux.py", "uv.lock"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    manifest = {
        "source_commit": head,
        "working_tree_dirty": dirty,
        "inputs": {p: sha(ROOT / p) for p in files},
        "platform": "linux-x86_64",
        "minimum_glibc": "2.35",
        "gui": False,
        "statistics": False,
        "build_packages": {
            d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
        },
    }
    info = OUT / "build-info.json"
    info.write_text(
        json.dumps(
            {
                "source_commit": head,
                "adapter_code_sha256": hashlib.sha256(
                    (ROOT / "src/santhau_studio/collectors/egp_home.py")
                    .read_bytes()
                    .replace(b"\r\n", b"\n")
                ).hexdigest(),
            },
            indent=2,
        )
    )
    excluded = (
        "PySide6",
        "PyQt6",
        "santhau_studio.desktop",
        "santhau_studio.storage.serving",
        "santhau_studio.storage.read_model",
        "santhau_studio.storage.identity_actions",
        "santhau_studio.workers.service",
        "santhau_studio.workers.ipc",
        "santhau_studio.workers.maintenance",
        "santhau_studio.platform_win.process",
        "santhau_studio.platform_win.scheduler",
        "santhau_studio.platform_win.shutdown_signal",
        "santhau_studio.platform_win.restart_manager",
    )
    run(
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=no-qt",
        "--python-flag=isolated",
        "--jobs=2",
        "--output-filename=santhau-collector",
        f"--output-dir={OUT}/nuitka",
        "--include-package=tzdata",
        "--include-package-data=tzdata",
        "--include-package-data=certifi",
        "--include-data-files=docs/ddl/g0_b_proposed.sql=docs/ddl/g0_b_proposed.sql",
        "--include-data-files=packaging/linux/config/egp-profile.json=config/egp-profile.json",
        f"--include-data-files={info}=config/build-info.json",
        *(f"--nofollow-import-to={name}" for name in excluded),
        "src/santhau_studio/collector_main.py",
    )
    dist = OUT / "nuitka/collector_main.dist"
    if not (dist / "santhau-collector").is_file():
        raise RuntimeError("Standalone output missing")
    if any("pyside" in p.name.lower() or "libqt" in p.name.lower() for p in dist.rglob("*")):
        raise RuntimeError("Unexpected Qt library in collector-only artifact")
    notices = dist / "LICENSES"
    notices.mkdir(exist_ok=True)
    for name in (
        "httpx",
        "httpcore",
        "h11",
        "certifi",
        "anyio",
        "idna",
        "tenacity",
        "PyYAML",
        "typing_extensions",
        "tzdata",
        "Nuitka",
    ):
        package = importlib.metadata.distribution(name)
        for file in package.files or []:
            if any(word in str(file).lower() for word in ("license", "copying", "notice")):
                source = Path(package.locate_file(file))
                if source.is_file() and source.suffix not in (".py", ".pyc"):
                    destination = notices / name / file.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    for file in (ROOT / "LICENSES").rglob("*"):
        if file.is_file() and "python" in str(file).lower():
            shutil.copy2(file, notices / file.name)
    for name in (
        "libssl3",
        "libffi8",
        "libsqlite3-0",
        "liblzma5",
        "libbz2-1.0",
        "zlib1g",
        "libgcc-s1",
        "libstdc++6",
    ):
        copyright_file = Path("/usr/share/doc") / name / "copyright"
        if copyright_file.is_file():
            destination = notices / "system" / name
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(copyright_file, destination / "copyright")
    bundle = OUT / "santhau-collector-0.0.1-RC3-linux-x86_64"
    bundle.mkdir(exist_ok=True)
    shutil.copytree(dist, bundle / "app", dirs_exist_ok=True)
    for name in ("install.sh", "uninstall.sh"):
        shutil.copy2(ROOT / "packaging/linux" / name, bundle / name)
        (bundle / name).chmod(0o755)
    shutil.copy2(ROOT / "docs/linux-collector.md", bundle / "HUONG-DAN.md")
    archive = OUT / (bundle.name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle, arcname=bundle.name)
    debroot = OUT / "deb-root"
    shutil.copytree(dist, debroot / "opt/santhau-collector", dirs_exist_ok=True)
    (debroot / "usr/bin").mkdir(parents=True, exist_ok=True)
    link = debroot / "usr/bin/santhau-collector"
    if not link.exists() and not link.is_symlink():
        link.symlink_to("/opt/santhau-collector/santhau-collector")
    services = debroot / "usr/lib/systemd/user"
    services.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "packaging/linux/santhau-collector.service", services / "santhau-collector.service"
    )
    control = debroot / "DEBIAN"
    control.mkdir(exist_ok=True)
    (control / "control").write_text(
        f"""Package: santhau-collector
Version: {VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: SjrEVE <270443947+SjrEVE@users.noreply.github.com>
Depends: libc6 (>= 2.35), libgcc-s1, libstdc++6, zlib1g, ca-certificates
Description: San Thau collector-only service
 Six-family weekly collection with durable raw storage and coverage proof.
 No GUI, dashboard, statistics or serving snapshot generation.
""",
        encoding="utf-8",
    )
    for directory in [debroot, *[p for p in debroot.rglob("*") if p.is_dir()]]:
        directory.chmod(0o755)
    for file in debroot.rglob("*"):
        if file.is_file() and not file.is_symlink():
            file.chmod(0o755 if file.name == "santhau-collector" or file.suffix == ".so" else 0o644)
    deb = OUT / f"santhau-collector_{VERSION}_amd64.deb"
    run("dpkg-deb", "--root-owner-group", "--build", debroot, deb)
    manifest["artifacts"] = {p.name: sha(p) for p in (deb, archive)}
    manifest["standalone_files"] = {
        p.relative_to(dist).as_posix(): sha(p) for p in dist.rglob("*") if p.is_file()
    }
    for name, digest in manifest["inputs"].items():
        if sha(ROOT / name) != digest:
            raise RuntimeError("Source changed during build")
    (OUT / "release-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (OUT / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in manifest["artifacts"].items()),
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "status": "BUILT_NOT_YET_ACCEPTED",
                "artifacts": manifest["artifacts"],
                "source_commit": head,
            }
        )
    )


if __name__ == "__main__":
    main()
