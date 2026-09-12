param(
    [switch]$InstallDependencies,
    [switch]$InstallCudaTorch,
    [string]$TorchCudaIndexUrl = "https://download.pytorch.org/whl/cu128",
    [ValidateSet("Default", "Contexts")]
    [string]$Workspace = "Default",
    [ValidateSet("Legacy", "Modern")]
    [string]$Ui = "Legacy",
    [switch]$NoLaunch
)

$CondaEnvName = "chroma-db-import"
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RequirementsPath = Join-Path $ProjectRoot "chroma_db_import_requirements.txt"
$DesktopRequirementsPath = Join-Path $ProjectRoot "desktop_requirements.txt"
$BuildScript = Join-Path $ProjectRoot "scripts\Build-ChromaDbImportDesktop.ps1"
$UiScript = Join-Path $ProjectRoot "chroma_db_import_ui.py"
$env:PYTHONNOUSERSITE = "1"
$env:PIP_USER = "0"
$ProjectSourcePath = Join-Path $ProjectRoot "src"
if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $env:PYTHONPATH = $ProjectSourcePath
} else {
    $env:PYTHONPATH = "$ProjectSourcePath;$env:PYTHONPATH"
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

function Invoke-ProjectPython {
    param([string[]]$Arguments)
    & conda run --no-capture-output -n $CondaEnvName python @Arguments
}

function Assert-CondaEnvWritable {
    $probe = @"
import site
import uuid
from pathlib import Path

target = Path(site.getsitepackages()[0]) / (".chroma_import_write_probe_" + uuid.uuid4().hex)
try:
    target.write_text("probe", encoding="ascii")
    target.unlink()
except Exception as exc:
    print(f"Conda site-packages is not writable: {type(exc).__name__}: {exc}")
    raise SystemExit(1)
"@
    $probePath = Join-Path ([System.IO.Path]::GetTempPath()) ("chroma_import_ui_write_check_{0}.py" -f [guid]::NewGuid().ToString("N"))
    $probe | Set-Content -LiteralPath $probePath -Encoding UTF8
    try {
        Invoke-ProjectPython -Arguments @($probePath)
        if ($LASTEXITCODE -ne 0) {
            throw "The '$CondaEnvName' environment is not writable. Refusing to install packages into the user site. Repair or recreate the Conda environment, then retry."
        }
    } finally {
        Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
    }
}

function Test-PythonDependencies {
    $check = @"
import importlib
import sys

required = [
    "chromadb",
    "langchain_chroma",
    "langchain_core",
    "langchain_huggingface",
    "psutil",
    "PySide6",
    "sentence_transformers",
]
if ("$Ui" == "Modern"):
    required += ["webview"]
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{name}: {type(exc).__name__}: {exc}")
if missing:
    print("MISSING:" + "|".join(missing))
    raise SystemExit(1)
print("Python dependencies: PASS")
"@
    $checkPath = Join-Path ([System.IO.Path]::GetTempPath()) ("chroma_import_ui_dependency_check_{0}.py" -f [guid]::NewGuid().ToString("N"))
    $check | Set-Content -LiteralPath $checkPath -Encoding UTF8
    try {
        Invoke-ProjectPython -Arguments @($checkPath)
        if ($LASTEXITCODE -ne 0) {
            throw "Python dependencies are missing from '$CondaEnvName'. Run .\scripts\Run-ChromaDbImport.ps1 -CreateCondaEnv to install them into that environment."
        }
    } finally {
        Remove-Item -LiteralPath $checkPath -Force -ErrorAction SilentlyContinue
    }
}

function Test-TorchCuda {
    $diagnostic = @"
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
"@
    $diagnosticPath = Join-Path ([System.IO.Path]::GetTempPath()) ("chroma_import_ui_cuda_check_{0}.py" -f [guid]::NewGuid().ToString("N"))
    $diagnostic | Set-Content -LiteralPath $diagnosticPath -Encoding UTF8
    try {
        Invoke-ProjectPython -Arguments @($diagnosticPath)
    } finally {
        Remove-Item -LiteralPath $diagnosticPath -Force -ErrorAction SilentlyContinue
    }
}

function Install-UiDependencies {
    Assert-CondaEnvWritable
    Invoke-ProjectPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Invoke-ProjectPython -Arguments @("-m", "pip", "install", "-r", $RequirementsPath)
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Invoke-ProjectPython -Arguments @("-m", "pip", "install", "-r", $DesktopRequirementsPath)
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm was not found. Install Node.js once, then run the modern desktop setup again."
    }
    & $BuildScript
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

}

function Install-CudaTorch {
    Assert-CondaEnvWritable
    Invoke-ProjectPython -Arguments @(
        "-m", "pip", "install", "--upgrade", "--force-reinstall",
        "torch", "torchvision", "torchaudio", "--index-url", $TorchCudaIndexUrl
    )
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found on PATH. Open a Miniconda/Anaconda PowerShell prompt, or add Conda to PATH."
}

if (-not (Test-CondaEnv)) {
    throw "Conda environment '$CondaEnvName' was not found. Create it with .\scripts\Run-ChromaDbImport.ps1 -CreateCondaEnv"
}

if ($InstallDependencies) {
    Install-UiDependencies
}
if ($InstallCudaTorch) {
    Install-CudaTorch
}
Test-PythonDependencies
Test-TorchCuda
if (-not $NoLaunch) {
    $uiArguments = @()
    if ($Workspace -eq "Contexts") {
        $uiArguments += @("--workspace", "contexts")
    }
    if ($Ui -eq "Modern") {
        $modernArguments = @("-m", "chroma_db_import.desktop")
        if ($Workspace -eq "Contexts") {
            $modernArguments += @("--workspace", "contexts")
        }
        Invoke-ProjectPython -Arguments $modernArguments
    } else {
        Invoke-ProjectPython -Arguments (@($UiScript) + $uiArguments)
    }
    exit $LASTEXITCODE
}
