param(
    [string]$Config,
    [string]$CondaEnvName = "chroma-db-import",
    [string]$ProcessedDataDir,
    [string]$PersistDir,
    [string]$CollectionName,
    [switch]$OneFile,
    [switch]$CreateCondaEnv,
    [switch]$SkipDependencyCheck
)

$PythonScript = Join-Path $PSScriptRoot "chroma_db_import.py"
$ConfigPath = Join-Path $PSScriptRoot "chroma_db_import_config.json"
$ConfigExamplePath = Join-Path $PSScriptRoot "chroma_db_import_config.example.json"
$RequirementsPath = Join-Path $PSScriptRoot "chroma_db_import_requirements.txt"

if (-not $Config) {
    $Config = $ConfigPath
}

function Invoke-ProjectPython {
    param([string[]]$Arguments)
    & conda run --no-capture-output -n $CondaEnvName python @Arguments
}

function Test-CondaEnv {
    $envListJson = & conda env list --json | ConvertFrom-Json
    foreach ($envPath in $envListJson.envs) {
        if ((Split-Path -Leaf $envPath) -eq $CondaEnvName) {
            return $true
        }
    }
    return $false
}

function New-ProjectCondaEnv {
    if (Test-CondaEnv) {
        Write-Host "Conda environment already exists: $CondaEnvName"
        return
    }
    & conda create -y -n $CondaEnvName python=3.11 pip
    if ($LASTEXITCODE -eq 0) {
        & conda run --no-capture-output -n $CondaEnvName python -m pip install --upgrade pip
    }
    if ($LASTEXITCODE -eq 0) {
        & conda run --no-capture-output -n $CondaEnvName python -m pip install -r $RequirementsPath
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Test-PythonDependencies {
    $dependencyCheckPath = Join-Path ([System.IO.Path]::GetTempPath()) ("chroma_import_dependency_check_{0}.py" -f [guid]::NewGuid().ToString("N"))
    @"
import importlib
import sys

required = [
    'chromadb',
    'langchain_chroma',
    'langchain_core',
    'langchain_huggingface',
    'sentence_transformers',
]
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
        Invoke-ProjectPython -Arguments @($dependencyCheckPath)
        return $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $dependencyCheckPath -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found on PATH. Open a Miniconda/Anaconda PowerShell prompt, or add Conda to PATH."
}

if (-not (Test-Path -LiteralPath $Config)) {
    Copy-Item -LiteralPath $ConfigExamplePath -Destination $Config
    Write-Host "Created config: $Config"
}

if ($CreateCondaEnv) {
    New-ProjectCondaEnv
    exit 0
}

if (-not (Test-CondaEnv)) {
    Write-Host "Conda environment '$CondaEnvName' was not found."
    Write-Host "Create it with:"
    Write-Host "  .\Run Chroma DB Import.ps1 -CreateCondaEnv"
    exit 1
}

if (-not $SkipDependencyCheck) {
    $dependencyExitCode = Test-PythonDependencies
    if ($dependencyExitCode -ne 0) {
        Write-Host ""
        Write-Host "Python dependencies are missing. Install them with:"
        Write-Host "  conda run -n $CondaEnvName python -m pip install -r `"$RequirementsPath`""
        exit $dependencyExitCode
    }
}

$argsList = @("--config", $Config)
if ($ProcessedDataDir) {
    $argsList += @("--processed-data-dir", $ProcessedDataDir)
}
if ($PersistDir) {
    $argsList += @("--persist-dir", $PersistDir)
}
if ($CollectionName) {
    $argsList += @("--collection-name", $CollectionName)
}
if ($OneFile) {
    $argsList += "--one-file"
}

$pythonArgs = @($PythonScript) + $argsList
Invoke-ProjectPython -Arguments $pythonArgs
exit $LASTEXITCODE
