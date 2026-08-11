# build-desktop.ps1 - bundle Infinity Code into a Windows desktop app.
#
# Pipeline:
#   1. PyInstaller freezes the FastAPI backend into src-tauri\binaries\infinity-backend.exe
#   2. Smoke test: the frozen exe must actually open port 8000
#   3. `tauri build` compiles the Rust shell + React frontend into an .msi installer
#
# Prerequisites: the project venv (venv\), npm deps installed, and the Rust
# toolchain (https://rustup.rs) for step 3.
#
# NOTE: keep this file pure ASCII. Windows PowerShell 5.1 reads BOM-less
# UTF-8 as ANSI, and smart punctuation decodes into curly quotes that
# the parser treats as string delimiters.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    # Some checkouts keep the venv at .venv (both names are gitignored).
    $AltPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $AltPython) { $Python = $AltPython }
}
if (-not (Test-Path $Python)) {
    Write-Error "venv not found - create it first (see README.md)."
}

# ---------------------------------------------------------------- #
# 1. Freeze the backend
# ---------------------------------------------------------------- #
Write-Host "=== [1/3] PyInstaller: freezing backend ===" -ForegroundColor Cyan

# pip writes an upgrade/notice banner to stderr; under ErrorActionPreference=Stop
# PS5.1 wraps ANY native stderr as a terminating NativeCommandError regardless of
# redirection. Lower the preference around the call and discard stderr outright,
# then gate on the real exit code.
$ErrorActionPreference = "Continue"
& $Python -m pip install --quiet pyinstaller 2>$null
$pipCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pipCode -ne 0) { Write-Error "pip install pyinstaller failed (exit $pipCode)." }

$Binaries = Join-Path $Root "src-tauri\binaries"
New-Item -ItemType Directory -Force $Binaries | Out-Null

# PyInstaller streams progress to stderr; same PS5.1 Stop-preference hazard as pip.
$ErrorActionPreference = "Continue"
& $Python -m PyInstaller --noconfirm --clean --onefile `
    --name infinity-backend `
    --distpath $Binaries `
    --workpath (Join-Path $Root "build\pyinstaller") `
    --specpath (Join-Path $Root "build") `
    --paths (Join-Path $Root "backend") `
    --add-data ((Join-Path $Root "backend\config.yaml") + ";.") `
    --add-data ((Join-Path $Root "backend\data\agents.json") + ";data") `
    --add-data ((Join-Path $Root "backend\data\seed_skills") + ";data\seed_skills") `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.lifespan.on `
    --hidden-import uvicorn.protocols.http.h11_impl `
    --hidden-import uvicorn.protocols.websockets.websockets_impl `
    --hidden-import PIL.ImageGrab `
    --hidden-import PIL.Image `
    --hidden-import mcp `
    --hidden-import mcp.types `
    --hidden-import mcp.client.stdio `
    --hidden-import mcp.client.streamable_http `
    --hidden-import mcp.client.session `
    --collect-submodules mcp.client `
    --collect-submodules mcp.shared `
    --collect-submodules anyio `
    --hidden-import numpy `
    --collect-submodules numpy `
    (Join-Path $Root "backend\serve.py")
$pyiCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pyiCode -ne 0) { Write-Error "PyInstaller build failed (exit $pyiCode)." }

$BackendExe = Join-Path $Binaries "infinity-backend.exe"
if (-not (Test-Path $BackendExe)) { Write-Error "Expected $BackendExe was not produced." }
$SizeMB = [math]::Round((Get-Item $BackendExe).Length / 1MB, 1)
Write-Host "Backend frozen: $BackendExe ($SizeMB MB)"

# ---------------------------------------------------------------- #
# 2. Smoke test the frozen exe
# ---------------------------------------------------------------- #
Write-Host "=== [2/3] Smoke test: frozen backend must open port 8000 ===" -ForegroundColor Cyan

$portInUse = Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($portInUse) {
    Write-Host "Port 8000 already in use (dev server running?) - skipping smoke test." -ForegroundColor Yellow
} else {
    $env:INFINITY_DATA_DIR = Join-Path $env:TEMP "infinity-smoke"
    $proc = Start-Process -FilePath $BackendExe -PassThru -WindowStyle Hidden
    try {
        $ready = $false
        foreach ($i in 1..60) {
            Start-Sleep -Milliseconds 1000
            if (Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -InformationLevel Quiet -WarningAction SilentlyContinue) {
                $ready = $true
                break
            }
            if ($proc.HasExited) {
                Write-Error ("Frozen backend exited early (code {0}) - likely a missing bundled module or import error. Aborting." -f $proc.ExitCode)
                break
            }
        }
        if (-not $ready) { Write-Error "Frozen backend never opened port 8000 - aborting." }

        # The backend issues a per-session bearer token at /api/v1/auth/token;
        # the desktop shell does the same on startup.
        $tokenResp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/auth/token" -TimeoutSec 10
        if (-not $tokenResp.token) { Write-Error "Auth token endpoint returned no token - auth broken in frozen exe." }
        $headers = @{ Authorization = "Bearer $($tokenResp.token)" }

        $report = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/cost" -Headers $headers -TimeoutSec 10
        Write-Host ("Smoke test OK - /api/v1/cost responded (budget: {0} AUD)." -f $report.daily_budget_aud)
        $agents = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/agents" -Headers $headers -TimeoutSec 10
        if ($agents.count -lt 1) { Write-Error "Agent Library did not load in the frozen exe (count 0) - agents.json not bundled." }
        Write-Host ("Agent Library OK - {0} agents bundled." -f $agents.count)
        $sys = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/system/info" -Headers $headers -TimeoutSec 10

        # CORS preflight guard: the auth middleware must NOT 401 OPTIONS,
        # or every authenticated fetch from the Tauri WebView dies with
        # 'Failed to fetch' (no Access-Control-Allow-Origin on the 401).
        $preflight = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/cost" -Method Options -Headers @{
            Origin = "http://localhost:4175"
            "Access-Control-Request-Method" = "GET"
            "Access-Control-Request-Headers" = "authorization"
        } -UseBasicParsing -TimeoutSec 10
        if ($preflight.StatusCode -ne 200) { Write-Error "CORS preflight failed (status $($preflight.StatusCode)) - auth middleware 401s OPTIONS." }
        if (-not $preflight.Headers["Access-Control-Allow-Origin"]) { Write-Error "CORS preflight returned no Access-Control-Allow-Origin header." }
        Write-Host "CORS preflight OK - OPTIONS passes the auth middleware."

        Write-Host ("Knowledge/numpy OK - system/info responded (v{0})." -f $sys.version)
    } finally {
        # Tree-kill: the PyInstaller onefile bootstrap spawns the real server
        # as a child, and Stop-Process on the parent alone orphans it.
        if (-not $proc.HasExited) { taskkill /PID $proc.Id /T /F | Out-Null }
        Remove-Item Env:\INFINITY_DATA_DIR -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------- #
# 3. Tauri build (frontend + Rust shell -> .msi)
# ---------------------------------------------------------------- #
Write-Host "=== [3/3] Tauri build ===" -ForegroundColor Cyan

$cargoHome = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path $cargoHome) { $env:Path = "$cargoHome;$env:Path" }
$cargo = Get-Command cargo -ErrorAction SilentlyContinue
if ($null -eq $cargo) {
    Write-Error "Rust toolchain not found. Install from https://rustup.rs then re-run."
}

# Cargo/tauri write build progress to stderr; same PS5.1 Stop-preference hazard.
$ErrorActionPreference = "Continue"
npx tauri build
$tauriCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($tauriCode -ne 0) { Write-Error "tauri build failed (exit $tauriCode)." }

$msi = Get-ChildItem -Path (Join-Path $Root "src-tauri\target\release\bundle\msi") -Filter *.msi -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -ne $msi) {
    Write-Host ""
    Write-Host "=== DONE ===" -ForegroundColor Green
    Write-Host "Installer: $($msi.FullName)"
    Write-Host "Double-click to install; the app auto-starts its own backend."
} else {
    Write-Error "tauri build finished but no .msi found under src-tauri\target\release\bundle\msi."
}
