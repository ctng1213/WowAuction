<#
.SYNOPSIS
  Runs every test: regenerates the Lua scenario fixtures, then pytest (with 100% engine
  branch coverage once ahsync/engine has modules) and the Lua 5.1 runner. See CLAUDE.md.
.PARAMETER NoLua
  Skip the Lua runner. Only for machines without lua5.1; a change is not done until it passes with Lua.
#>
param([switch]$NoLua)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

# Python: prefer `python` on PATH, else the py launcher's 3.12.
# (On Windows, `python` may be the Microsoft Store stub, which prints nothing to stdout.)
$py = @('python')
try { $version = & python --version 2>$null } catch { $version = $null }
if (-not $version) { $py = @('py', '-3.12') }
function Invoke-Py { & $py[0] @($py | Select-Object -Skip 1) @args }

$failed = @()

Invoke-Py tools/gen_lua_fixtures.py
if ($LASTEXITCODE) { $failed += 'gen_lua_fixtures' }

$engineModules = @(Get-ChildItem ahsync/engine -Filter *.py -ErrorAction SilentlyContinue |
                   Where-Object Name -ne '__init__.py')
if ($engineModules.Count -gt 0) {
    Invoke-Py -m coverage run -m pytest
    if ($LASTEXITCODE) { $failed += 'pytest' }
    Invoke-Py -m coverage report
    if ($LASTEXITCODE) { $failed += 'engine coverage < 100%' }
} else {
    Invoke-Py -m pytest
    if ($LASTEXITCODE) { $failed += 'pytest' }
}

if ($NoLua) {
    Write-Warning 'Lua tests skipped (-NoLua).'
} elseif (-not (Get-Command lua5.1 -ErrorAction SilentlyContinue)) {
    Write-Error 'lua5.1 not found on PATH (CLAUDE.md). Install Lua 5.1, or pass -NoLua to skip.' -ErrorAction Continue
    $failed += 'lua5.1 missing'
} else {
    & lua5.1 tests/support/lua/runner.lua tests/addon tests/visual
    if ($LASTEXITCODE) { $failed += 'lua' }
}

if ($failed.Count) {
    Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'All tests passed.' -ForegroundColor Green
