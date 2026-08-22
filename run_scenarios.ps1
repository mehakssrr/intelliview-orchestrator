$ErrorActionPreference = "Stop"

if (-not $env:TARGET_HOST) {
    Write-Host "ERROR: Set TARGET_HOST to the environment you're testing, e.g.:"
    Write-Host '$env:TARGET_HOST = "http://localhost:8000"'
    exit 1
}

if (-not $env:LOAD_TEST_API_KEY) {
    Write-Host "ERROR: Set LOAD_TEST_API_KEY to the API_TOKEN configured on the target server."
    exit 1
}

New-Item -ItemType Directory -Force -Path "results" | Out-Null

function Run-Scenario {
    param (
        [int]$Users,
        [int]$SpawnRate,
        [string]$RunTime
    )

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $prefix = "results/${Users}users_$timestamp"

    Write-Host ""
    Write-Host "=================================================================="
    Write-Host " Running scenario: $Users concurrent interviews"
    Write-Host " host=$env:TARGET_HOST  spawn_rate=$SpawnRate/s  duration=$RunTime"
    Write-Host "=================================================================="

    locust `
        -f locustfile.py `
        --host "$env:TARGET_HOST" `
        --users $Users `
        --spawn-rate $SpawnRate `
        --run-time $RunTime `
        --csv="$prefix" `
        --html="${prefix}.html" `
        --headless `
        --only-summary

    Write-Host "Results written to ${prefix}_stats.csv, ${prefix}_failures.csv, ${prefix}.html"
}

$Scenario = if ($args.Count -gt 0) { $args[0] } else { "all" }

switch ($Scenario) {
    "10" {
        Run-Scenario 10 2 "3m"
    }

    "50" {
        Run-Scenario 50 5 "5m"
    }

    "100" {
        Run-Scenario 100 10 "5m"
    }

    "500" {
        Run-Scenario 500 25 "10m"
    }

    "all" {
        Run-Scenario 10 2 "3m"
        Run-Scenario 50 5 "5m"
        Run-Scenario 100 10 "5m"
        Run-Scenario 500 25 "10m"
    }

    default {
        Write-Host "Unknown scenario '$Scenario'. Use one of: 10 50 100 500 all"
        exit 1
    }
}

Write-Host ""
Write-Host "All requested scenarios complete. See ./results/ for CSV + HTML reports."