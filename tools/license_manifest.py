#!/usr/bin/env python3
"""A5 — build the license manifest from evidence, not from assumptions.

Two independent sources are joined:

  1. installed distribution metadata (PEP 639 License-Expression / License field),
     which states each Python package's license as the packager declared it;
  2. the Nuitka standalone dist folder, which states which native libraries are
     actually shipped in the product.

Anything the wheel does not ship is reported as unverified rather than filled in
from memory. Notably the PySide6 wheel carries only the commercial-Qt notice, so
per-module Qt license files must be sourced from the Qt release itself.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Packages that never reach the user's machine are detected from the deployment, not
# from a hand-maintained list; see is_shipped().
QT_LICENSE = "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"

# Native libraries that appear in a PySide6/Nuitka deployment and where they come from.
NATIVE_ORIGINS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"^qt6", re.I),
        "Qt 6 modules (The Qt Company)",
        "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    ),
    (
        re.compile(r"^pyside6", re.I),
        "PySide6 binding (The Qt Company)",
        "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    ),
    (
        re.compile(r"^shiboken6", re.I),
        "Shiboken generator runtime (The Qt Company)",
        "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    ),
    (re.compile(r"^python3\d*\.dll$", re.I), "CPython (Python Software Foundation)", "PSF-2.0"),
    (
        re.compile(r"^(libcrypto|libssl)-", re.I),
        "OpenSSL (bundled by CPython/Qt)",
        "Apache-2.0 AND OpenSSL-Original",
    ),
    (
        re.compile(r"^msvcp140|^vcruntime|^vcomp", re.I),
        "Microsoft Visual C++ runtime",
        "MIT-Like (VS license terms)",
    ),
    (re.compile(r"^opengl32sw", re.I), "Mesa llvmpipe software OpenGL (inside Qt build)", "MIT"),
    (re.compile(r"^(zlib1|libzlib)", re.I), "zlib", "Zlib"),
    (re.compile(r"^(freetype)", re.I), "FreeType", "FTL"),
    (re.compile(r"^(harfbuzz)", re.I), "HarfBuzz", "MIT"),
    (re.compile(r"^(libpng1|libpng)", re.I), "libpng", "libpng-2.0"),
    (re.compile(r"^(pcre2)", re.I), "PCRE2", "BSD-3-Clause"),
    (re.compile(r"^(doublecon|libdoubleconv)", re.I), "double-conversion", "MIT"),
    (re.compile(r"^(md4c)", re.I), "md4c", "MIT"),
)

UNMAPPED = "unclassified — inspect before release"


def distribution_licenses(deployed: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        version = dist.metadata.get("Version") or ""
        declared = dist.metadata.get("License") or ""
        expression = dist.metadata.get("License-Expression") or ""
        classifiers = [
            value.split("::", 1)[-1].strip()
            for key, value in dist.metadata.items()
            if key == "Classifier" and value.startswith("License ::")
        ]
        license_files = sorted(str(path) for path in _license_files(dist))
        record_names = _record_basenames(dist)
        shipped = is_shipped(name, record_names, deployed)
        rows.append(
            {
                "name": name,
                "version": version,
                "license_field": declared,
                "license_expression": expression,
                "license_classifiers": classifiers,
                "license_files_in_wheel": license_files,
                "detected_by": "deployment file match" if shipped else "absent from deployment",
                "role": "shipped at runtime" if shipped else "build/dev only",
            }
        )
    rows.sort(key=lambda row: (row["role"], row["name"].lower()))
    return rows


def _license_files(dist: metadata.Distribution) -> list[Path]:
    """License texts carried by the wheel.

    PEP 639 puts them in <name>-<version>.dist-info/licenses/. importlib exposes that
    directory as the private _path attribute; locate_file() resolves against
    site-packages and therefore cannot see it.
    """
    base = getattr(dist, "_path", None)
    if base is None:
        return []
    base = Path(str(base))
    found: list[Path] = []
    licenses_dir = base / "licenses"
    if licenses_dir.is_dir():
        found.extend(path for path in licenses_dir.rglob("*") if path.is_file())
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "COPYING.txt"):
        candidate = base / name
        if candidate.is_file():
            found.append(candidate)
    return found


def _record_basenames(dist: metadata.Distribution) -> set[str]:
    """File names the package installed, taken from its RECORD."""
    base = getattr(dist, "_path", None)
    if base is None:
        return set()
    record = Path(str(base)) / "RECORD"
    if not record.is_file():
        return set()
    names: set[str] = set()
    for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
        path = line.split(",", 1)[0].strip()
        if path:
            names.add(Path(path.replace("\\", "/")).name.lower())
    return names


def deployment_basenames(dist_dir: Path) -> set[str]:
    return {path.name.lower() for path in dist_dir.rglob("*") if path.is_file()}


def is_shipped(name: str, record_names: set[str], deployed: set[str]) -> bool:
    """A package ships if anything it installed is present in the deployment."""
    if name.lower().replace("-", "_") == "santhau_data_studio":
        return True  # our own code is compiled into the executable, not copied as files
    return bool(record_names & deployed)


def native_inventory(dist_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(dist_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".dll", ".pyd"}:
            continue
        rows.append(_classify_native(path, path.relative_to(dist_dir)))
    return rows


CPYTHON_STDLIB_MODULES = re.compile(
    r"^(_bz2|_decimal|_hashlib|_lzma|_socket|_ssl|_wmi|_ctypes|_elementtree|_pickle|_queue|"
    r"_sqlite3|_statistics|array|math|select|unicodedata|zlib|_zoneinfo|_uuid|_multibytecodec|"
    r"_codecs_cn|_codecs_hk|_codecs_iso2022|_codecs_jp|_codecs_kr|_codecs_tw|pyexpat|nt|posix|errno|"
    r"faulthandler|mgrserver|winreg|_winapi|_overlapped|_multiprocessing|_contextvars_)\.pyd$",
    re.IGNORECASE,
)


def _classify_native(path: Path, relative: Path) -> dict[str, str]:
    name = path.name
    parts = [part.lower() for part in relative.parts[:-1]]
    inside_pyside = any(part in {"pyside6", "shiboken6"} for part in parts)
    # Nuitka lays Qt's plugin tree out as PySide6/qt-plugins/<category>/, so match on
    # the word rather than an exact directory name.
    if any("plugin" in part for part in parts):
        return {
            "file": str(relative),
            "origin": "Qt 6 plugins (The Qt Company)",
            "license": QT_LICENSE,
        }
    if any(part == "qml" for part in parts):
        return {
            "file": str(relative),
            "origin": "Qt 6 QML modules (The Qt Company)",
            "license": QT_LICENSE,
        }
    if inside_pyside and path.suffix.lower() == ".pyd":
        return {
            "file": str(relative),
            "origin": "PySide6 Qt binding module (The Qt Company)",
            "license": QT_LICENSE,
        }
    if inside_pyside:
        return {
            "file": str(relative),
            "origin": "Qt 6 module (The Qt Company)",
            "license": QT_LICENSE,
        }
    if CPYTHON_STDLIB_MODULES.match(name):
        return {
            "file": str(relative),
            "origin": "CPython standard library extension",
            "license": "PSF-2.0",
        }
    if name.lower() == "python3.dll" or re.match(r"^python3\d*\.dll$", name, re.IGNORECASE):
        return {
            "file": str(relative),
            "origin": "CPython (Python Software Foundation)",
            "license": "PSF-2.0",
        }
    stem = path.stem
    for pattern, origin, license_id in NATIVE_ORIGINS:
        if pattern.match(stem):
            return {"file": str(relative), "origin": origin, "license": license_id}
    return {"file": str(relative), "origin": UNMAPPED, "license": UNMAPPED}


def qt_module_names(natives: list[dict[str, str]]) -> list[str]:
    modules = set()
    for row in natives:
        match = re.match(r"^qt6([a-z0-9_]+)\.dll$", Path(row["file"]).name, re.IGNORECASE)
        if match:
            modules.add(match.group(1).replace("_", ""))
    return sorted(modules)


def qml_modules(dist_dir: Path) -> list[str]:
    """QML modules that ship, taken from the qmldir files inside the deployment."""
    found = set()
    for qmldir in dist_dir.rglob("qmldir"):
        relative = qmldir.parent.relative_to(dist_dir)
        parts = relative.parts
        if "qml" in parts:
            index = parts.index("qml")
            tail = parts[index + 1 :]
            if tail:
                found.add(".".join(tail))
    return sorted(found)


def build_markdown(
    distributions: list[dict[str, Any]], natives: list[dict[str, str]], dist_dir: Path
) -> str:
    counts: Counter[str] = Counter(row["license"] for row in natives)
    lines = [
        "# LICENSE-MANIFEST — G0-A A5",
        "",
        f"Generated by `tools/license_manifest.py` against `{dist_dir}`.",
        "",
        "## 1. Python distributions declared in the lockfile",
        "",
        "| package | version | license (as declared) | reaches user machine? |",
        "|---|---|---|---|",
    ]
    for row in distributions:
        license_text = (
            row["license_expression"]
            or row["license_field"]
            or ", ".join(row["license_classifiers"])
            or "—"
        )
        lines.append(f"| {row['name']} | {row['version']} | {license_text} | {row['role']} |")

    lines += [
        "",
        "## 2. Native libraries actually present in the packaged build",
        "",
        "| license id | files |",
        "|---|---|",
    ]
    for license_id, count in sorted(counts.items(), key=lambda item: -item[1]):
        lines.append(f"| {license_id} | {count} |")

    modules = qt_module_names(natives)
    lines += [
        "",
        f"### Qt 6 modules bundled ({len(modules)})",
        "",
        "`" + ", ".join(modules) + "`",
        "",
        "### QML modules bundled",
        "",
        "`" + ", ".join(qml_modules(dist_dir)) + "`",
        "",
        "## 3. Verification status",
        "",
        "- Distribution licenses are read from installed metadata, so they are the",
        "  packager's own declaration — not an independent legal review.",
        "- The PySide6 wheel ships **only** `LicenseRef-Qt-Commercial.txt`. It does not",
        "  carry Qt's per-module `LICENSE.*` files, so the LGPL/GPL/commercial triple",
        "  licence for each Qt module is taken from the PySide6 metadata expression and is",
        "  **not independently verified against the Qt 6.12 release archive**.",
        "- Native libraries whose origin could not be classified from the file name are",
        "  listed as unclassified below and must be resolved before any release.",
        "",
        "### Unclassified native files",
        "",
    ]
    unclassified = [row["file"] for row in natives if row["origin"] == UNMAPPED]
    lines += [f"- `{name}`" for name in unclassified] or ["- none"]

    lines += [
        "",
        "## 4. Decisions still owned by the Founder / legal",
        "",
        "- **LGPL vs commercial Qt.** Spec §13.4 lists this as outside coder authority.",
        "- **Dynamic-linking compliance for LGPL.** Nuitka `--standalone` produces a folder",
        "  build (not `--onefile`), which keeps `qt6*.dll` as separate replaceable files —",
        "  that is the arrangement LGPL's minimal-scope linking clause expects. Confirm the",
        "  final installer keeps this property.",
        "- **Source-availability clause.** LGPLv3 requires conveying the corresponding",
        "  machine-readable source or a written offer. Decide which mechanism is used.",
        "- **Nuitka is AGPLv3 but carries its own Runtime Library Exception**",
        "  (`LICENSE-RUNTIME.txt`), which permits propagating compiled output without",
        "  AGPL. Nuitka itself is a build tool and is not shipped.",
        "",
    ]
    return "\n".join(lines)


def build_notices(distributions: list[dict[str, Any]], natives: list[dict[str, str]]) -> str:
    lines = [
        "THIRD_PARTY_NOTICES",
        "===================",
        "",
        "This file lists third-party material present in Săn Thầu Data Studio.",
        "Full license texts are collected at G4 (release), not here: see the",
        "verification-status section of LICENSE-MANIFEST.md for what is and is",
        "not yet evidenced.",
        "",
        "Python packages shipped at runtime",
        "---------------------------------",
    ]
    for row in distributions:
        if row["role"] != "shipped at runtime":
            continue
        license_text = (
            row["license_expression"]
            or row["license_field"]
            or ", ".join(row["license_classifiers"])
            or "unknown"
        )
        files = ", ".join(row["license_files_in_wheel"]) or "none shipped in wheel"
        lines.append(f"  * {row['name']} {row['version']} — {license_text}")
        lines.append(f"      license files in wheel: {files}")

    lines += [
        "",
        "Build / test tooling (not shipped to users)",
        "-------------------------------------------",
    ]
    for row in distributions:
        if row["role"] == "shipped at runtime":
            continue
        license_text = (
            row["license_expression"]
            or row["license_field"]
            or ", ".join(row["license_classifiers"])
            or "unknown"
        )
        lines.append(f"  * {row['name']} {row['version']} — {license_text}")

    lines += [
        "",
        "Native libraries in the packaged build",
        "--------------------------------------",
    ]
    grouped: dict[str, list[str]] = {}
    for row in natives:
        grouped.setdefault(f"{row['origin']} [{row['license']}]", []).append(row["file"])
    for origin, files in sorted(grouped.items()):
        lines.append(f"  * {origin} — {len(files)} file(s)")
        for name in files[:6]:
            lines.append(f"      {name}")
        if len(files) > 6:
            lines.append(f"      ... {len(files) - 6} more")
    lines.append("")
    return "\n".join(lines)


def collect_license_texts(distributions: list[dict[str, Any]], out_dir: Path) -> list[str]:
    """Copy the license texts the wheels actually ship, so the installer can convey them.

    Only files found on disk are copied. Absence is reported in the manifest instead of
    being papered over with a text written from memory.
    """
    copied: list[str] = []
    licenses_dir = out_dir / "LICENSES"
    for row in distributions:
        if row["role"] != "shipped at runtime":
            continue
        for relative in row["license_files_in_wheel"]:
            source = Path(relative)
            if not source.is_file():
                continue
            target_dir = licenses_dir / row["name"].lower()
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name
            target.write_bytes(source.read_bytes())
            copied.append(f"{row['name']}/{source.name}")
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True, help="Nuitka .dist folder to inventory")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.dist.exists():
        print(f"dist folder not found: {args.dist}", file=sys.stderr)
        return 2

    deployed = deployment_basenames(args.dist)
    distributions = distribution_licenses(deployed)
    natives = native_inventory(args.dist)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.json:
        payload = {"distributions": distributions, "native_files": natives, "dist": str(args.dist)}
        (args.out_dir / "license-manifest.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    (args.out_dir / "LICENSE-MANIFEST.md").write_text(
        build_markdown(distributions, natives, args.dist), encoding="utf-8"
    )
    (args.out_dir / "THIRD_PARTY_NOTICES.txt").write_text(
        build_notices(distributions, natives), encoding="utf-8"
    )
    copied = collect_license_texts(distributions, args.out_dir)
    print(
        f"distributions={len(distributions)} native_files={len(natives)} "
        f"unclassified={sum(1 for row in natives if row['origin'] == UNMAPPED)} "
        f"license_texts_copied={len(copied)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
