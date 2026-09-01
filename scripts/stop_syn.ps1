$ErrorActionPreference = "Stop"
$RUNTIME_DIR = Join-Path $PSScriptRoot "..\runtime"
$RUNTIME_DIR = (Resolve-Path $RUNTIME_DIR).ToString()
. (Join-Path $PSScriptRoot "runtime.local.ps1")

function Test-PidAlive {
    param([int]$Pid)
    $null = Get-Process -Id $Pid -ErrorAction SilentlyContinue
    return ($LASTEXITCODE -eq 0)
}

function Invoke-GracefulStop {
    param([int]$Pid, [string]$Name, [int]$GraceSec = 5)
    $proc = Get-Process -Id $Pid -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    try { $proc.Close() | Out-Null } catch {}
    $deadline = (Get-Date).AddSeconds($GraceSec)
    while ((Get-Date) -lt $deadline) {
        $still = Get-Process -Id $Pid -ErrorAction SilentlyContinue
        if (-not $still) { return $true }
        Start-Sleep -Milliseconds 200
    }
    try { $proc.Kill() -ErrorAction SilentlyContinue } catch {}
    return $true
}

# Load ownership
$owned = @{llamaOwned=$false; synOwned=$false; cfOwned=$false}
$ownPath = Join-Path $RUNTIME_DIR "ownership.json"
if (Test-Path $ownPath) {
    $owned = Get-Content $ownPath -Raw | ConvertFrom-Json
}

# 1. Cloudflare
$cfPath = Join-Path $RUNTIME_DIR "cloudflared.pid"
if (Test-Path $cfPath) {
    $content = (Get-Content $cfPath -Raw).Trim()
    if ($content -match "^\d+$") {
        $pid = [int]$content
        if (Test-PidAlive $pid) {
            Invoke-GracefulStop -Pid $pid -Name "cloudflared" | Out-Null
            Write-Host "Cloudflare     STOPPED" -ForegroundColor Yellow
        }
    }
    Remove-Item $cfPath -Force -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path $RUNTIME_DIR "cloudflare-url.txt") -Force -ErrorAction SilentlyContinue

# 2. Syn
$synPath = Join-Path $RUNTIME_DIR "syn.pid"
if (Test-Path $synPath) {
    $content = (Get-Content $synPath -Raw).Trim()
    if ($content -match "^\d+$") {
        $pid = [int]$content
        if (Test-PidAlive $pid) {
            if ($owned.synOwned) {
                Invoke-GracefulStop -Pid $pid -Name "Syn" | Out-Null
                Write-Host "Syn            STOPPED" -ForegroundColor Yellow
            } else {
                Write-Host "Syn            LEFT RUNNING (not owned by this launcher)" -ForegroundColor Yellow
            }
        } else {
            Write-Host "Syn            STOPPED (stale PID removed)" -ForegroundColor DarkGray
        }
    }
    Remove-Item $synPath -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "Syn            LEFT RUNNING (no PID file)" -ForegroundColor DarkGray
}

# 3. llama.cpp
$llamaPath = Join-Path $RUNTIME_DIR "llama.pid"
if (Test-Path $llamaPath) {
    $content = (Get-Content $llamaPath -Raw).Trim()
    if ($content -match "^\d+$") {
        $pid = [int]$content
        if (Test-PidAlive $pid) {
            if ($owned.llamaOwned) {
                Invoke-GracefulStop -Pid $pid -Name "llama.cpp" | Out-Null
                Write-Host "llama.cpp      STOPPED" -ForegroundColor Yellow
            } else {
                Write-Host "llama.cpp      LEFT RUNNING (not owned by this launcher)" -ForegroundColor Yellow
            }
        } else {
            Write-Host "llama.cpp      STOPPED (stale PID removed)" -ForegroundColor DarkGray
        }
    }
    Remove-Item $llamaPath -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "llama.cpp      LEFT RUNNING (no PID file)" -ForegroundColor DarkGray
}

# Cleanup
Remove-Item (Join-Path $RUNTIME_DIR "ownership.json") -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "Runtime metadata cleaned." -ForegroundColor DarkGray
