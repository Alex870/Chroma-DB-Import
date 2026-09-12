param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$FrontendDist = Join-Path $FrontendRoot "dist"
$AssetRoot = Join-Path $ProjectRoot "src\chroma_db_import\desktop\assets"
$PythonBuildRoot = Join-Path $ProjectRoot "build"

# Setuptools copies package data into build\lib and does not reliably remove
# files deleted from the source tree on a later wheel build. Clear only this
# generated packaging directory so a subsequent wheel cannot retain an old
# frontend bundle.
if (Test-Path -LiteralPath $PythonBuildRoot) {
    Remove-Item -LiteralPath $PythonBuildRoot -Recurse -Force
}

if (-not $SkipInstall) {
    Push-Location $FrontendRoot
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally { Pop-Location }
}

Push-Location $FrontendRoot
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }

if (-not (Test-Path -LiteralPath $FrontendDist -PathType Container)) {
    throw "Frontend build did not produce $FrontendDist"
}
New-Item -ItemType Directory -Force -Path $AssetRoot | Out-Null
Get-ChildItem -LiteralPath $AssetRoot -Force | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $FrontendDist -Force | Copy-Item -Destination $AssetRoot -Recurse -Force
Write-Host "Modern desktop assets copied to $AssetRoot"
