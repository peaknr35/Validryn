# Copies the tracked fixture into .fta/fixtures and makes the copy its own Git
# repository. The tracked files stay plain so the main repository never
# contains a nested .git directory.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "fixtures\broken_calculator"
$repo = Join-Path $root ".fta\fixtures\broken_calculator"

if (Test-Path (Join-Path $repo ".git")) {
    Write-Output "Fixture repository already exists: $repo"
    exit 0
}

New-Item -ItemType Directory -Force (Split-Path -Parent $repo) | Out-Null
Copy-Item -Recurse $source $repo
Get-ChildItem $repo -Recurse -Force -Directory -Include "__pycache__", ".pytest_cache" |
    Remove-Item -Recurse -Force

git -C $repo init -q
git -C $repo config user.name "FTA Fixture"
git -C $repo config user.email "fta-fixture@example.invalid"
git -C $repo add .
git -C $repo commit -qm "Create broken calculator fixture"
if ($LASTEXITCODE -ne 0) { throw "Fixture commit failed." }
Write-Output "Initialized fixture repository: $repo"
