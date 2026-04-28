param(
    [string]$Config,
    [string]$CondaEnvName = "chroma-db-import"
)

$ConfigPath = Join-Path $PSScriptRoot "chroma_db_import_config.json"
$ConfigExamplePath = Join-Path $PSScriptRoot "chroma_db_import_config.example.json"
$RequirementsPath = Join-Path $PSScriptRoot "chroma_db_import_requirements.txt"

if (-not $Config) {
    $Config = $ConfigPath
}

function Write-Check {
    param([string]$Status, [string]$Name, [string]$Detail)
    Write-Host ("[{0}] {1}: {2}" -f $Status, $Name, $Detail)
}

function Resolve-ProjectPath {
    param([string]$Value)
    if (-not $Value) { return $null }
    if ([System.IO.Path]::IsPathRooted($Value)) { return $Value }
    return (Join-Path $PSScriptRoot $Value)
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    Write-Check -Status FAIL -Name "Conda" -Detail "conda was not found on PATH."
    exit 1
}
Write-Check -Status PASS -Name "Conda" -Detail ((& conda --version) -join " ")

$envListJson = & conda env list --json | ConvertFrom-Json
$envExists = $false
foreach ($envPath in $envListJson.envs) {
    if ((Split-Path -Leaf $envPath) -eq $CondaEnvName) {
        $envExists = $true
        Write-Check -Status PASS -Name "Conda environment" -Detail $envPath
        break
    }
}
if (-not $envExists) {
    Write-Check -Status FAIL -Name "Conda environment" -Detail "Missing '$CondaEnvName'. Create it with .\Run Chroma DB Import.ps1 -CreateCondaEnv"
}

if (-not (Test-Path -LiteralPath $Config)) {
    if (Test-Path -LiteralPath $ConfigExamplePath) {
        Write-Check -Status WARN -Name "Config" -Detail "Missing $Config; run script will create it from example."
        $Config = $ConfigExamplePath
    } else {
        Write-Check -Status FAIL -Name "Config" -Detail "Missing config and example config."
        exit 1
    }
} else {
    Write-Check -Status PASS -Name "Config" -Detail $Config
}

$configObject = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$processedDataDir = Resolve-ProjectPath ([string]$configObject.processed_data_dir)
$persistDir = Resolve-ProjectPath ([string]$configObject.persist_dir)
$fileGlob = if ($configObject.file_glob) { [string]$configObject.file_glob } else { "**/*.processed_documents.json" }

if (Test-Path -LiteralPath $processedDataDir) {
    $pattern = Join-Path $processedDataDir $fileGlob
    $matches = @(Get-ChildItem -Path $pattern -File -Recurse -ErrorAction SilentlyContinue)
    Write-Check -Status PASS -Name "Processed data" -Detail ("{0}; found {1} cache file(s)." -f $processedDataDir, $matches.Count)
} else {
    Write-Check -Status FAIL -Name "Processed data" -Detail "Missing directory: $processedDataDir"
}

if (-not (Test-Path -LiteralPath $persistDir)) {
    New-Item -ItemType Directory -Path $persistDir -Force | Out-Null
}
Write-Check -Status PASS -Name "Chroma persist directory" -Detail $persistDir

if ($envExists) {
    $dependencyCheckPath = Join-Path ([System.IO.Path]::GetTempPath()) ("chroma_import_dependency_check_{0}.py" -f [guid]::NewGuid().ToString("N"))
    @"
import importlib
import sys
required = ['chromadb', 'langchain_chroma', 'langchain_core', 'langchain_huggingface', 'sentence_transformers']
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f'{name}: {type(exc).__name__}: {exc}')
if missing:
    print('MISSING:' + '|'.join(missing))
    sys.exit(1)
"@ | Set-Content -LiteralPath $dependencyCheckPath -Encoding UTF8
    try {
        & conda run --no-capture-output -n $CondaEnvName python $dependencyCheckPath
        if ($LASTEXITCODE -eq 0) {
            Write-Check -Status PASS -Name "Python dependencies" -Detail "All required modules import."
        } else {
            Write-Check -Status FAIL -Name "Python dependencies" -Detail "Install with: conda run -n $CondaEnvName python -m pip install -r `"$RequirementsPath`""
        }
    } finally {
        Remove-Item -LiteralPath $dependencyCheckPath -Force -ErrorAction SilentlyContinue
    }
}
