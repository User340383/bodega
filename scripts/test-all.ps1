<#
.SYNOPSIS
    Runs Bodega's complete automated test suite with one command.

.DESCRIPTION
    Runs the same checks as the CI workflow (.github/workflows/ci.yml), in Docker, in this order:

      1. Build the isolated test images (compose.test.yml).
      2. Backend tests: pytest against a temporary PostgreSQL database.
      3. Alembic: upgrade a clean database to the head revision.
      4. Backend lint and type check: Ruff (backend/ruff.toml) and Pyright (backend/pyrightconfig.json).
      5. Frontend: ESLint, Vitest and the Vite production build.
      6. HTTP smoke test: PostgreSQL, MinIO, two backends and the frontend proxy, end to end.

    It stops at the first failure and exits with a non-zero code. Everything runs in the separate
    "bodega-gabriel-test" Compose project, whose databases live in memory (tmpfs). The cleanup at
    the end removes only that project's containers, network and anonymous volumes. The
    application's own database and volumes from docker-compose.yml are never touched.

    Requires Docker Desktop (or Docker Engine) with Docker Compose, and network access for image
    builds. It needs no API keys, makes no billed AI calls and contacts no production service.
    CI additionally runs a secret scan, the Mermaid diagram render and production image builds.

.EXAMPLE
    .\scripts\test-all.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $root 'compose.test.yml'
$test = @('compose', '-f', $composeFile)
$smoke = $test + @('--profile', 'smoke')
$backendDir = (Join-Path $root 'backend') -replace '\\', '/'
$results = New-Object System.Collections.Generic.List[object]
$started = Get-Date

# Pinned like the CI's "pipx run ruff==0.17.4".
$pythonChecks = "pip install --quiet --disable-pip-version-check --root-user-action=ignore " +
    "ruff==0.17.4 'pyright[nodejs]==1.1.414' && ruff check --no-cache . && pyright"
$alembicCheck = 'export DATABASE_URL=$TEST_DATABASE_URL && alembic upgrade head && alembic current | grep -qF ''(head)'''

function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') exited with code $LASTEXITCODE"
    }
}

function Invoke-Step([string]$Name, [scriptblock]$Action) {
    Write-Host ''
    Write-Host "==> $Name" -ForegroundColor Cyan
    $stepStarted = Get-Date
    try {
        & $Action
        $results.Add([pscustomobject]@{ Step = $Name; Result = 'PASSED'; Seconds = [int]((Get-Date) - $stepStarted).TotalSeconds })
    } catch {
        $results.Add([pscustomobject]@{ Step = $Name; Result = 'FAILED'; Seconds = [int]((Get-Date) - $stepStarted).TotalSeconds })
        throw
    }
}

function Write-Summary {
    Write-Host ''
    Write-Host 'Summary' -ForegroundColor Cyan
    $results | Format-Table -AutoSize | Out-String | Write-Host
    Write-Host ('Total time: {0:N0} s' -f ((Get-Date) - $started).TotalSeconds)
}

Push-Location $root
$exitCode = 0
$stackUsed = $false
try {
    Invoke-Step 'Check that Docker is running' {
        & docker info --format '{{.ServerVersion}}' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The Docker engine is not running. Start Docker Desktop and try again.' }
    }
    $stackUsed = $true

    Invoke-Step 'Build test images' { Invoke-Docker ($smoke + @('build')) }
    Invoke-Step 'Backend tests (pytest, PostgreSQL)' { Invoke-Docker ($test + @('run', '--rm', 'backend-tests')) }
    Invoke-Step 'Alembic upgrade head on a clean database' {
        Invoke-Docker ($test + @('run', '--rm', 'backend-tests', 'sh', '-c', $alembicCheck))
    }
    Invoke-Step 'Backend lint and type check (Ruff, Pyright)' {
        Invoke-Docker ($test + @('run', '--rm', '--no-deps', '-v', "${backendDir}:/src:ro", '-w', '/src',
            'backend-tests', 'sh', '-c', $pythonChecks))
    }
    Invoke-Step 'Frontend lint, tests and build' { Invoke-Docker ($test + @('run', '--rm', '--no-deps', 'frontend-checks')) }
    Invoke-Step 'HTTP smoke test' {
        Invoke-Docker ($smoke + @('up', '-d', '--wait', 'backend-smoke', 'backend-smoke-ai', 'frontend-smoke'))
        try {
            Invoke-Docker ($smoke + @('run', '--rm', 'http-smoke'))
        } catch {
            Write-Host 'Backend logs from the smoke stack:' -ForegroundColor Yellow
            $logArgs = $smoke + @('logs', '--tail', '200', 'backend-smoke', 'backend-smoke-ai')
            & docker @logArgs
            throw
        }
    }
} catch {
    $exitCode = 1
    Write-Host ''
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    if ($stackUsed) {
        Write-Host ''
        Write-Host '==> Removing the temporary test containers' -ForegroundColor Cyan
        # Scoped to the bodega-gabriel-test project; the application's volumes are not affected.
        $downArgs = $smoke + @('down', '-v', '--remove-orphans')
        & docker @downArgs
        if ($LASTEXITCODE -ne 0) { Write-Host 'Warning: cleanup did not finish; run the same "down" command by hand.' -ForegroundColor Yellow }
    }
    Pop-Location
}

Write-Summary
if ($exitCode -eq 0) {
    Write-Host 'All checks passed.' -ForegroundColor Green
} else {
    Write-Host 'Checks failed. See the output above.' -ForegroundColor Red
}
exit $exitCode
