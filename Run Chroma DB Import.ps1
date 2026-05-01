param(
    [string]$Config,
    [string]$CondaEnvName = "chroma-db-import",
    [string]$ProcessedDataDir,
    [string]$PersistDir,
    [string]$CollectionName,
    [switch]$OneFile,
    [switch]$CreateCondaEnv,
    [switch]$InstallCudaTorch,
    [string]$TorchCudaIndexUrl = "https://download.pytorch.org/whl/cu128",
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
    if (($LASTEXITCODE -eq 0) -and $InstallCudaTorch) {
        & conda run --no-capture-output -n $CondaEnvName python -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url $TorchCudaIndexUrl
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Install-CudaTorch {
    & conda run --no-capture-output -n $CondaEnvName python -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url $TorchCudaIndexUrl
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

function Test-TorchCuda {
    $cudaCheckPath = Join-Path ([System.IO.Path]::GetTempPath()) ("chroma_import_cuda_check_{0}.py" -f [guid]::NewGuid().ToString("N"))
    @"
import subprocess
import sys

print("CUDA environment diagnosis")
try:
    result = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"], capture_output=True, text=True)
    if result.returncode == 0:
        print("nvidia-smi: PASS")
        print(result.stdout.strip())
    else:
        print("nvidia-smi: FAIL")
        print(result.stderr.strip())
except Exception as exc:
    print(f"nvidia-smi: FAIL ({type(exc).__name__}: {exc})")

try:
    import torch
except Exception as exc:
    print(f"torch import: FAIL ({type(exc).__name__}: {exc})")
    sys.exit(0)

print(f"torch version: {getattr(torch, '__version__', 'unknown')}")
print(f"torch CUDA runtime: {getattr(getattr(torch, 'version', None), 'cuda', None) or 'not included'}")
try:
    available = torch.cuda.is_available()
    print(f"torch.cuda.is_available: {available}")
    if available:
        print(f"CUDA device count: {torch.cuda.device_count()}")
        for index in range(torch.cuda.device_count()):
            print(f"CUDA device {index}: {torch.cuda.get_device_name(index)}")
    else:
        print("Diagnosis: PyTorch cannot use CUDA. If nvidia-smi passed, install a CUDA-enabled PyTorch wheel.")
except Exception as exc:
    print(f"CUDA query: FAIL ({type(exc).__name__}: {exc})")
"@ | Set-Content -LiteralPath $cudaCheckPath -Encoding UTF8

    try {
        Invoke-ProjectPython -Arguments @($cudaCheckPath)
    } finally {
        Remove-Item -LiteralPath $cudaCheckPath -Force -ErrorAction SilentlyContinue
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

if ($InstallCudaTorch) {
    Install-CudaTorch
}

if (-not $SkipDependencyCheck) {
    $dependencyExitCode = Test-PythonDependencies
    if ($dependencyExitCode -ne 0) {
        Write-Host ""
        Write-Host "Python dependencies are missing. Install them with:"
        Write-Host "  conda run -n $CondaEnvName python -m pip install -r `"$RequirementsPath`""
        exit $dependencyExitCode
    }
    Test-TorchCuda
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
