param(
    [switch]$Local
)

$ErrorActionPreference = "Stop"
$script:RUNTIME_DIR = Join-Path $PSScriptRoot "..\runtime"
$script:RUNTIME_DIR = (Resolve-Path $script:RUNTIME_DIR).ToString()
if (-not (Test-Path $script:RUNTIME_DIR)) { New-Item -ItemType Directory -Path $script:RUNTIME_DIR -Force | Out-Null }

. (Join-Path $PSScriptRoot "runtime.local.ps1")

function Test-HttpOk {
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Write-StalePid {
    param([string]$Path)
    if (Test-Path $Path) {
        $content = Get-Content $Path -Raw
        if ($content -match "^\d+$") {
            $pidVal = [int]$content.Trim()
            $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
            if (-not $proc) { Remove-Item $Path -Force }
        }
    }
}

function Invoke-GracefulStop {
    param([int]$Pid, [string]$Name, [int]$GraceSec = 5)
    $proc = Get-Process -Id $Pid -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    try {
        $proc.Close() | Out-Null
    } catch {}
    $deadline = (Get-Date).AddSeconds($GraceSec)
    while ((Get-Date) -lt $deadline) {
        $still = Get-Process -Id $Pid -ErrorAction SilentlyContinue
        if (-not $still) { return $true }
        Start-Sleep -Milliseconds 200
    }
    try { $proc.Kill() -ErrorAction SilentlyContinue } catch {}
    return $true
}

# ---- 1. llama.cpp ----
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "            SYN LOCAL DEMO LAUNCHER" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

if (Test-HttpOk "$script:LLAMA_BASE_URL/health") {
    Write-Host "LLAMA.CPP - ALREADY RUNNING" -ForegroundColor Yellow
    $llamaOwned = $false
} else {
    if (-not (Test-Path $script:LLAMA_SERVER)) {
        Write-Host "ERROR: llama-server not found at $script:LLAMA_SERVER" -ForegroundColor Red
        Write-Host "Set SYN_LLAMA_SERVER env var or edit scripts/runtime.local.ps1" -ForegroundColor Red
        exit 1
    }
    Write-Host "Starting llama.cpp on $script:LLAMA_BASE_URL ..." -ForegroundColor Green
    $llamaArgs = @(
        "-m", "models/model-a.gguf",
        "--host", "127.0.0.1",
        "--port", "$script:LLAMA_PORT",
        "--ctx-size", "4096",
        "--threads", "4",
        "--np", "1"
    )
    $llamaProc = Start-Process -FilePath $script:LLAMA_SERVER -ArgumentList $llamaArgs -PassThru -WindowStyle Hidden
    $llamaProc.Id | Out-File -FilePath (Join-Path $script:RUNTIME_DIR "llama.pid") -Force
    $llamaOwned = $true
    Write-Host "  PID $($llamaProc.Id)" -ForegroundColor DarkGray

    $deadline = (Get-Date).AddSeconds(30)
    $ok = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk "$script:LLAMA_BASE_URL/health" -TimeoutSec 2) { $ok = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ok) {
        Write-Host "ERROR: llama.cpp failed to become healthy" -ForegroundColor Red
        exit 1
    }
    Write-Host "LLAMA.CPP - REACHABLE" -ForegroundColor Green
}

# ---- 2. Syn ----
if (Test-HttpOk "$script:SYN_BASE_URL/health") {
    Write-Host "SYN - ALREADY RUNNING" -ForegroundColor Yellow
    $synOwned = $false
} else {
    if (-not $script:ADMIN_SECRET) {
        $secret = Read-Host "Enter Syn admin secret"
        if (-not $secret) { Write-Host "Admin secret required" -ForegroundColor Red; exit 1 }
        $script:ADMIN_SECRET = $secret
    }
    $env:SYN_ADMIN_SECRET = $script:ADMIN_SECRET
    Write-Host "Starting Syn gateway on $script:SYN_BASE_URL ..." -ForegroundColor Green
    $synProc = Start-Process -FilePath (Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe") `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $script:SYN_HOST, "--port", "$script:SYN_PORT", "--workers", "1" `
        -WorkingDirectory (Join-Path $PSScriptRoot "..") -PassThru -WindowStyle Hidden
    $synProc.Id | Out-File -FilePath (Join-Path $script:RUNTIME_DIR "syn.pid") -Force
    $synOwned = $true
    Write-Host "  PID $($synProc.Id)" -ForegroundColor DarkGray

    $deadline = (Get-Date).AddSeconds(15)
    $ok = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk "$script:SYN_BASE_URL/health" -TimeoutSec 2) { $ok = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ok) {
        Write-Host "ERROR: Syn failed to become healthy" -ForegroundColor Red
        exit 1
    }
    Write-Host "SYN - HEALTHY" -ForegroundColor Green
}

# ---- 3. Backend check ----
$health = Invoke-WebRequest -Uri "$script:SYN_BASE_URL/health" -UseBasicParsing -ErrorAction Stop | ConvertFrom-Json
$reachable = $health.backends | Where-Object { $_.reachable -eq $true }
if ($reachable.Count -eq 0) {
    Write-Host ""
    Write-Host "SYN RUNNING - BUT NO INFERENCE BACKEND IS REACHABLE" -ForegroundColor Red
    foreach ($b in $health.backends) {
        Write-Host "  $($b.id): $($b.state) - $($b.reason)" -ForegroundColor Yellow
    }
    Write-Host "Start llama.cpp on port 8080 before using the stack." -ForegroundColor Yellow
    $cloudflareUrl = $null
} else {
    Write-Host "BACKEND - REACHABLE ($($reachable.Count))" -ForegroundColor Green
    $cloudflareUrl = $null
}

# ---- 4. Cloudflare ----
if (-not $Local) {
    if (-not $script:CLOUDFLARED) {
        Write-Host "CLOUDFLARE - SKIPPED (cloudflared not found)" -ForegroundColor Yellow
    } else {
        Write-Host "Starting Cloudflare Quick Tunnel ..." -ForegroundColor Green
        $cfProc = Start-Process -FilePath $script:CLOUDFLARED `
            -ArgumentList "tunnel", "--url", "http://127.0.0.1:$script:SYN_PORT" `
            -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $script:RUNTIME_DIR "cloudflared.out.log") `
            -RedirectStandardError (Join-Path $script:RUNTIME_DIR "cloudflared.err.log")
        $cfProc.Id | Out-File -FilePath (Join-Path $script:RUNTIME_DIR "cloudflared.pid") -Force
        $cfOwned = $true

        $url = $null
        $deadline = (Get-Date).AddSeconds(25)
        while ((Get-Date) -lt $deadline) {
            $log = Get-Content (Join-Path $script:RUNTIME_DIR "cloudflared.out.log") -ErrorAction SilentlyContinue
            if ($log -match "https://[a-z0-9-]+\.trycloudflare\.com") {
                $url = $matches[0]
                break
            }
            Start-Sleep -Milliseconds 500
        }
        if ($url) {
            $url | Out-File -FilePath (Join-Path $script:RUNTIME_DIR "cloudflare-url.txt") -Force
            $deadline2 = (Get-Date).AddSeconds(15)
            $pubOk = $false
            while ((Get-Date) -lt $deadline2) {
                if (Test-HttpOk "$url/health" -TimeoutSec 3) { $pubOk = $true; break }
                Start-Sleep -Milliseconds 500
            }
            if ($pubOk) {
                Write-Host "CLOUDFLARE - CONNECTED" -ForegroundColor Green
                $cloudflareUrl = $url
            } else {
                Write-Host "CLOUDFLARE - TUNNEL STARTED BUT PUBLIC HEALTH FAILED" -ForegroundColor Yellow
                $cloudflareUrl = $url
            }
        } else {
            Write-Host "CLOUDFLARE - FAILED TO EXTRACT URL" -ForegroundColor Red
            $cfOwned = $false
        }
    }
} else {
    Write-Host "CLOUDFLARE - SKIPPED (--local)" -ForegroundColor Yellow
}

# ---- 5. Summary ----
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "                SYN IS READY" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "llama.cpp" -ForegroundColor White
Write-Host "  status: $(if (Test-HttpOk $script:LLAMA_BASE_URL/health) { 'REACHABLE' } else { 'UNREACHABLE' })"
Write-Host "  local:  $script:LLAMA_BASE_URL"
Write-Host ""
Write-Host "Syn Gateway" -ForegroundColor White
Write-Host "  status: HEALTHY"
Write-Host "  local:  $script:SYN_BASE_URL"
Write-Host ""
Write-Host "Admin UI" -ForegroundColor White
Write-Host "  http://127.0.0.1:$script:SYN_PORT/admin/ui"
Write-Host ""
if ($cloudflareUrl) {
    Write-Host "Cloudflare" -ForegroundColor White
    Write-Host "  status: CONNECTED"
    Write-Host "  public: $cloudflareUrl"
    Write-Host ""
    Write-Host "Remote OpenAI Base URL" -ForegroundColor White
    Write-Host "  $cloudflareUrl/v1"
    Write-Host ""
}

# Save ownership metadata
@{
    llamaOwned = $llamaOwned
    synOwned = $synOwned
    cfOwned = $cfOwned
    startedAt = (Get-Date).ToString("o")
} | ConvertTo-Json | Out-File -FilePath (Join-Path $script:RUNTIME_DIR "ownership.json") -Force

if (-not $Local -and $cloudflareUrl) {
    $choice = Read-Host "Open Syn Admin UI? [Y/N]"
    if ($choice -match '^[Yy]') {
        Start-Process "http://127.0.0.1:$script:SYN_PORT/admin/ui"
    }
}

Write-Host "Press Enter to close this launcher window." -ForegroundColor DarkGray
Read-Host
