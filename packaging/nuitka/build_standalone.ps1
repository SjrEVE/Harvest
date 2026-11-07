# G0-A / A8 packaging spike - Nuitka standalone build.
# ASCII-only source: Windows PowerShell 5.1 mis-parses non-BOM UTF-8 with diacritics.
#
# Builds two executables into build/nuitka/<name>.dist:
#   SanThauDataStudio.exe  Qt Quick/QML viewer
#   SanThauCollector.exe   headless worker: job loop + SID-authenticated control pipe (no Qt)
#
# Usage: powershell -ExecutionPolicy Bypass -File packaging/nuitka/build_standalone.ps1

param([string]$PythonExecutable = '')

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Repo

$Py = if ($PythonExecutable) { $PythonExecutable } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path $Py)) { throw "missing venv python: $Py" }
& $Py tools/release_manifest.py capture
if ($LASTEXITCODE -ne 0) { throw 'source manifest capture failed' }

$Out = Join-Path $Repo 'build\nuitka'
New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Assert-BuildPath([string]$Candidate) {
    $full = [System.IO.Path]::GetFullPath($Candidate)
    $allowed = [System.IO.Path]::GetFullPath((Join-Path $Repo 'build')) + '\'
    if (-not $full.StartsWith($allowed, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing filesystem operation outside build: $full"
    }
    return $full
}

# Clear previous outputs before building. Doing it afterwards races with any process
# still holding the folder (a shell whose cwd is inside it, or a running exe), which
# leaves a half-deleted dist behind.
foreach ($stale in @('app_main.dist', 'service.dist', 'worker_main.dist', 'SanThauDataStudio.dist', 'SanThauCollector.dist')) {
    $path = Join-Path $Out $stale
    $path = Assert-BuildPath $path
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}

Write-Host '--- building SanThauDataStudio.exe (Qt Quick/QML) ---'
& $Py -m nuitka `
    --standalone `
    --jobs=4 `
    --assume-yes-for-downloads `
    --enable-plugin=pyside6 `
    --windows-console-mode=disable `
    --windows-company-name='Gia Su AI' `
    --windows-product-name='San Thau Data Studio' `
    --windows-file-version=0.0.1.7 `
    --windows-product-version=0.0.1.7 `
    --windows-file-description='San Thau Data Studio 0.0.1-RC3-7' `
    --include-qt-plugins=qml,platforms,imageformats,styles `
    --include-data-dir=src/santhau_studio/desktop/qml=qml `
    --output-dir=$Out `
    --output-filename=SanThauDataStudio.exe `
    --remove-output `
    src/santhau_studio/app_main.py
if ($LASTEXITCODE -ne 0) { throw "nuitka app build failed: $LASTEXITCODE" }

Write-Host '--- building SanThauCollector.exe (headless worker) ---'
& $Py -m nuitka `
    --standalone `
    --jobs=4 `
    --assume-yes-for-downloads `
    --enable-plugin=no-qt `
    --windows-console-mode=disable `
    --windows-company-name='Gia Su AI' `
    --windows-product-name='San Thau Data Studio Collector' `
    --windows-file-version=0.0.1.7 `
    --windows-product-version=0.0.1.7 `
    --windows-file-description='San Thau Data Studio Collector 0.0.1-RC3-7' `
    --include-data-files=docs/ddl/g0_b_proposed.sql=docs/ddl/g0_b_proposed.sql `
    --include-data-files=docs/ddl/002_public_deep.sql=docs/ddl/002_public_deep.sql `
    --include-data-files=docs/ddl/003_attempt_telemetry.sql=docs/ddl/003_attempt_telemetry.sql `
    --include-data-files=docs/ddl/004_deep_parser_metadata.sql=docs/ddl/004_deep_parser_metadata.sql `
    --include-data-files=docs/ddl/005_scoped_maintenance.sql=docs/ddl/005_scoped_maintenance.sql `
    --include-data-files=docs/ddl/006_missing_only_policy.sql=docs/ddl/006_missing_only_policy.sql `
    --output-dir=$Out `
    --output-filename=SanThauCollector.exe `
    --remove-output `
    src/santhau_studio/workers/service.py
if ($LASTEXITCODE -ne 0) { throw "nuitka worker build failed: $LASTEXITCODE" }

# Nuitka names each dist folder after the entry module; rename to product names so
# the Inno input matches the §2.2 topology.
$Renames = @(
    @{ From = 'app_main.dist';    To = 'SanThauDataStudio.dist' },
    @{ From = 'service.dist';     To = 'SanThauCollector.dist' }
)
foreach ($rename in $Renames) {
    $source = Join-Path $Out $rename.From
    $target = Join-Path $Out $rename.To
    $source = Assert-BuildPath $source
    $target = Assert-BuildPath $target
    if (Test-Path $source) {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $source -Destination $target
    }
    if (-not (Test-Path $target)) { throw "dist folder missing: $target" }
    $files = (Get-ChildItem -Recurse -File $target | Measure-Object).Count
    $bytes = (Get-ChildItem -Recurse -File $target | Measure-Object -Sum Length).Sum
    Write-Host ("dist {0}: files={1} bytes={2:N0}" -f $rename.To, $files, $bytes)
}
Write-Host 'BUILD_OK'

# --- assemble a single deployment folder -------------------------------------
# The product topology puts SanThauDataStudio.exe and SanThauCollector.exe in one
# directory, so the GUI can find the worker as a sibling. Two Nuitka dist folders
# are merged here; shared runtimes (python312.dll, msvcp140*.dll) are identical
# between them, so the app copy wins and the collector copy is skipped on conflict.
$Stage = Join-Path $Repo 'build\stage'
$Stage = Assert-BuildPath $Stage
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

foreach ($source in @('SanThauDataStudio.dist', 'SanThauCollector.dist')) {
    $root = Join-Path $Out $source
    Get-ChildItem -Recurse -File $root | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length).TrimStart('\')
        $target = Join-Path $Stage $relative
        if (Test-Path $target) { return }
        $parent = Split-Path -Parent $target
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Copy-Item -LiteralPath $_.FullName -Destination $target
    }
}

foreach ($required in @('SanThauDataStudio.exe', 'SanThauCollector.exe', 'qml\Main.qml')) {
    if (-not (Test-Path (Join-Path $Stage $required))) { throw "stage missing $required" }
}
$stageFiles = (Get-ChildItem -Recurse -File $Stage | Measure-Object).Count
$stageBytes = (Get-ChildItem -Recurse -File $Stage | Measure-Object -Sum Length).Sum
Write-Host ("stage {0}: files={1} bytes={2:N0}" -f $Stage, $stageFiles, $stageBytes)
Write-Host 'ASSEMBLE_OK'
& $Py tools/release_manifest.py seal
if ($LASTEXITCODE -ne 0) { throw 'build provenance verification failed' }
