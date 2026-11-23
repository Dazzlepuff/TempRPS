$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $projectPath 'config\database-runtime.local.json'
if (-not (Test-Path -LiteralPath $runtimePath)) {
    throw 'Database setup is missing. See README.md.'
}
$settings = Get-Content -Raw -LiteralPath $runtimePath | ConvertFrom-Json
$admin = Join-Path $settings.bin 'mariadb-admin.exe'
function Test-AccountsDatabase {
    $ErrorActionPreference = 'Continue'
    & $admin "--defaults-file=$($settings.client_config)" ping --silent *> $null
    return $LASTEXITCODE -eq 0
}
if (Test-AccountsDatabase) {
    Write-Output 'Account database is already running on 127.0.0.1:3307.'
    exit 0
}
$server = Join-Path $settings.bin 'mariadbd.exe'
Start-Process -FilePath $server -ArgumentList ('--defaults-file="' + $settings.server_config + '"') -WindowStyle Hidden -WorkingDirectory $projectPath | Out-Null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-AccountsDatabase) {
        Write-Output 'Account database started on 127.0.0.1:3307.'
        exit 0
    }
}
throw 'Database did not start. Check data/accounts-db.error.log.'
