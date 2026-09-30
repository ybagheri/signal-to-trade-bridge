<#
.SYNOPSIS
    The full quality gate. Run this before every commit.

.DESCRIPTION
    Lint, format check, type check, tests, and the domain isolation check.

    The last one is listed separately because it is the project's most important
    architectural invariant and the only gate that fails when someone adds an
    import rather than when they add a bug. A trade-decision layer that quietly
    grew a dependency on a private repository would still pass every other check
    here while making the test suite unrunnable on any other machine.

.PARAMETER Coverage
    Also produce a coverage report. Useful before a release, noisy during
    development.
#>

[CmdletBinding()]
param(
    [switch]$Coverage
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    Write-Host ''
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# Prefer the project virtual environment when it exists, so the gate runs against
# the same interpreter the project was installed into. Falling back to whatever
# python is on PATH keeps the gate runnable on a machine that has not run setup
# yet, which is better than a gate that cannot run at all.
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
}
else {
    $python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $python) {
        Write-Host 'ERROR: no Python found. Run scripts/setup.ps1 first.' -ForegroundColor Red
        exit 1
    }
    Write-Host 'Note: no .venv found, using the system Python. Run scripts/setup.ps1 for a clean environment.' -ForegroundColor Yellow
}

Push-Location $repoRoot
try {
    Invoke-Step 'ruff check' { & $python -m ruff check . }
    Invoke-Step 'ruff format --check' { & $python -m ruff format --check . }
    Invoke-Step 'mypy' { & $python -m mypy }

    if ($Coverage) {
        Invoke-Step 'pytest with coverage' {
            & $python -m pytest --cov=signal_to_trade_bridge --cov-report=term-missing --cov-report=html
        }
    }
    else {
        Invoke-Step 'pytest' { & $python -m pytest -q }
    }

    Invoke-Step 'domain isolation' {
        & $python -m pytest tests/unit/test_domain_isolation.py -v --no-header
    }
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'All checks passed.' -ForegroundColor Green
