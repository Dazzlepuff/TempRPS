$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$settings = Get-Content -Raw -LiteralPath (Join-Path $projectPath 'config\database-runtime.local.json') | ConvertFrom-Json
& (Join-Path $settings.bin 'mariadb-admin.exe') "--defaults-file=$($settings.client_config)" shutdown
if ($LASTEXITCODE -ne 0) { throw 'Database shutdown failed.' }
Write-Output 'Account database stopped.'
