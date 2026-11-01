param(
    [string]$PythonRoot = "",
    [switch]$NoSelfTest,
    [switch]$KeepBuild
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = Split-Path -Parent $here
$build = Join-Path $here "build"
$runtime = Join-Path $build "runtime"
$payload = Join-Path $build "payload.zip"
$out = Join-Path $build "out"
$dist = Join-Path $root "dist"
$icon = Join-Path $here "app.ico"

function Fail([string]$message) {
    Write-Host "ERROR: $message" -ForegroundColor Red
    exit 1
}

Write-Host "==> PyMC Launcher single-file packaging" -ForegroundColor Cyan

if (-not $PythonRoot) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { Fail "python not found in PATH" }
    $PythonRoot = (& python -c "import sys; print(sys.base_prefix)").Trim()
}
$pythonExe = Join-Path $PythonRoot "python.exe"
if (-not (Test-Path $pythonExe)) { Fail "python.exe not found: $pythonExe" }

$version = (& $pythonExe -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')").Trim()
$fullVersion = (& $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')").Trim()
if ($version -ne "311") { Fail "Python 3.11 is required, found $fullVersion" }

$tkinter = Join-Path $PythonRoot "DLLs\_tkinter.pyd"
$tclDir = Join-Path $PythonRoot "tcl"
if (-not (Test-Path $tkinter)) { Fail "_tkinter.pyd not found in the Python installation" }
if (-not (Test-Path $tclDir)) { Fail "tcl directory not found in the Python installation" }

Write-Host "    Python: $fullVersion ($PythonRoot)"
Write-Host "==> Assembling runtime"

Remove-Item -Recurse -Force $runtime, $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $runtime, "$runtime\DLLs" | Out-Null

Copy-Item (Join-Path $PythonRoot "python.exe") $runtime
Copy-Item (Join-Path $PythonRoot "pythonw.exe") $runtime
Get-ChildItem $PythonRoot -File -Filter "*.dll" | Copy-Item -Destination $runtime
Get-ChildItem (Join-Path $PythonRoot "DLLs") -File -Filter "*.pyd" | Copy-Item -Destination "$runtime\DLLs"
Get-ChildItem (Join-Path $PythonRoot "DLLs") -File -Filter "*.dll" | Copy-Item -Destination "$runtime\DLLs"
Copy-Item $tclDir (Join-Path $runtime "tcl") -Recurse

& $pythonExe (Join-Path $here "pack_stdlib.py") (Join-Path $PythonRoot "Lib") (Join-Path $build "python$version.zip")
if ($LASTEXITCODE -ne 0) { Fail "failed to pack the Python standard library" }
Move-Item (Join-Path $build "python$version.zip") (Join-Path $runtime "python$version.zip") -Force

$site = Join-Path $PythonRoot "Lib\site-packages"
$destSite = Join-Path $runtime "Lib\site-packages"
New-Item -ItemType Directory -Force $destSite | Out-Null
$packages = @(
    "httpx", "httpcore", "h11", "certifi", "idna", "sniffio",
    "h2", "hpack", "hyperframe", "anyio", "typing_extensions", "exceptiongroup"
)
$copied = @()
foreach ($pkg in $packages) {
    $source = Join-Path $site $pkg
    if (Test-Path $source) {
        Copy-Item $source (Join-Path $destSite $pkg) -Recurse -Force
        $copied += $pkg
    }
}
if (-not (Test-Path (Join-Path $destSite "httpx"))) {
    Fail "httpx is not installed in this Python. Run: pip install httpx[http2]"
}
Write-Host ("    site-packages: " + ($copied -join ", "))

Set-Content -LiteralPath (Join-Path $runtime "python$version._pth") -Encoding ASCII -Value @(
    "python$version.zip",
    ".",
    "DLLs",
    "Lib\site-packages"
)

Copy-Item (Join-Path $root "launcher.py") $runtime
Copy-Item (Join-Path $root "core") (Join-Path $runtime "core") -Recurse
Copy-Item (Join-Path $root "ui") (Join-Path $runtime "ui") -Recurse
Get-ChildItem $runtime -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $runtime -Recurse -File -Filter "*.pyc" |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "==> Testing runtime"
$env:TCL_LIBRARY = Join-Path $runtime "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $runtime "tcl\tk8.6"
$env:PYMCL_DATA_DIR = Join-Path $build "test-data"
& $pythonExe -c "import tkinter, httpx, core.config, ui.app; r=tkinter.Tk(); r.withdraw(); r.destroy(); print('runtime ok', httpx.__version__)"
if ($LASTEXITCODE -ne 0) { Fail "runtime test failed" }
Remove-Item -Recurse -Force (Join-Path $build "test-data") -ErrorAction SilentlyContinue

Write-Host "==> Packing payload"
& $pythonExe (Join-Path $here "pack_payload.py") $runtime $payload
if ($LASTEXITCODE -ne 0) { Fail "failed to pack payload.zip" }

if (-not (Test-Path $icon)) {
    Write-Host "==> Generating icon"
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object System.Drawing.Bitmap 64, 64
    $graphics = [System.Drawing.Graphics]::FromImage($bmp)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::FromArgb(15, 118, 110))
    $font = New-Object System.Drawing.Font("Segoe UI", 34, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $graphics.DrawString("P", $font, [System.Drawing.Brushes]::White, 16, 4)
    $graphics.Dispose()
    $iconObject = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
    $stream = [System.IO.File]::Create($icon)
    $iconObject.Save($stream)
    $stream.Close()
    $iconObject.Dispose()
    $bmp.Dispose()
}

Write-Host "==> Building C# bootstrapper"
dotnet build (Join-Path $here "PyMCLauncherBoot.csproj") -c Release -o $out --nologo
if ($LASTEXITCODE -ne 0) { Fail "dotnet build failed" }

New-Item -ItemType Directory -Force $dist | Out-Null
Copy-Item (Join-Path $out "PyMCLauncher.exe") (Join-Path $dist "PyMCLauncher.exe") -Force

if (-not $NoSelfTest) {
    Write-Host "==> Running self-test"
    $bootLog = Join-Path $env:LOCALAPPDATA "PyMCLauncher\boot.log"
    Remove-Item -LiteralPath $bootLog -Force -ErrorAction SilentlyContinue
    $process = Start-Process -FilePath (Join-Path $dist "PyMCLauncher.exe") -ArgumentList "--selftest" -PassThru -Wait
    if ($process.ExitCode -ne 0) { Fail "self-test failed (exit $($process.ExitCode)); see $bootLog" }
    Get-Content -LiteralPath $bootLog -ErrorAction SilentlyContinue | Select-Object -Last 2
}

$exe = Get-Item (Join-Path $dist "PyMCLauncher.exe")
Write-Host ("==> Done: {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB)) -ForegroundColor Green

if (-not $KeepBuild) {
    Remove-Item -Recurse -Force $runtime, $out -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $payload -Force -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $build "test-data") -ErrorAction SilentlyContinue
    Get-ChildItem $build -File -Filter "python-*.zip" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
