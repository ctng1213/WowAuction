<#
.SYNOPSIS
  The milestone verification routine (CLAUDE.md "How work is run"): tools/test.ps1, then the
  whole-plan snapshot check, then (with -Bench) the refresh benchmark. Fixes the environment
  first: puts lua5.1 on PATH from ~/tools/lua51 when it is not already there, and uses the py
  launcher's 3.12 when `python` is the Microsoft Store stub.
.PARAMETER Bench
  Also run python tools/bench.py (about two minutes). Required when a change could affect a budget.
.PARAMETER NoLua
  Passed through to tools/test.ps1. A change is not done until it passes with Lua.
#>
param([switch]$Bench, [switch]$NoLua)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

# lua5.1: the standalone interpreter lives in ~/tools/lua51 on this machine.
if (-not (Get-Command lua5.1 -ErrorAction SilentlyContinue)) {
    $lua = Join-Path $env:USERPROFILE 'tools\lua51'
    if (Test-Path (Join-Path $lua 'lua5.1.exe')) { $env:Path += ";$lua" }
}

# Python: prefer `python` on PATH, else the py launcher's 3.12 (same rule as test.ps1).
$py = @('python')
try { $version = & python --version 2>$null } catch { $version = $null }
if (-not $version) { $py = @('py', '-3.12') }
function Invoke-Py { & $py[0] @($py | Select-Object -Skip 1) @args }

$failed = @()

Write-Host '== tools/test.ps1' -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'test.ps1') -NoLua:$NoLua
if ($LASTEXITCODE) { $failed += 'tests' }

Write-Host '== tools/snapshot.py' -ForegroundColor Cyan
Invoke-Py tools/snapshot.py
if ($LASTEXITCODE) { $failed += 'snapshot (run `python tools/snapshot.py --update` only on purpose, then review the diff)' }

if ($Bench) {
    Write-Host '== tools/bench.py' -ForegroundColor Cyan
    Invoke-Py tools/bench.py
    if ($LASTEXITCODE) { $failed += 'bench' }
} else {
    Write-Host 'bench skipped (pass -Bench when a change could affect a budget).'
}

if ($failed.Count) {
    Write-Host "VERIFY FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'Verify passed.' -ForegroundColor Green
