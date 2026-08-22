param(
    [string]$DatabaseUrl = $env:TEST_CANONICAL_DATABASE_URL
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    throw "TEST_CANONICAL_DATABASE_URL is required"
}
if ($DatabaseUrl -notmatch '^postgresql(?:\+psycopg)?://') {
    throw "Only PostgreSQL test URLs are accepted"
}
$databaseName = ($DatabaseUrl -split '[/?]')[-1]
if ($databaseName -notmatch '_test$') {
    throw "Refusing to reset a database whose name does not end in _test"
}

$env:CANONICAL_DATABASE_URL = $DatabaseUrl
$python = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}
& $python -m alembic downgrade base
& $python -m alembic upgrade head
