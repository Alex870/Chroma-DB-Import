param(
    [switch]$InstallCudaTorch,
    [string]$TorchCudaIndexUrl = "https://download.pytorch.org/whl/cu128",
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
$RequirementsPath = Join-Path $ProjectRoot "chroma_db_import_requirements.txt"
$UiScript = Join-Path $ProjectRoot "chroma_db_import_ui.py"

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
    $diagnostic | & $VenvPython -
}

function Install-UiDependencies {
    if (-not (Test-Path $VenvPython)) {
        python -m venv (Join-Path $ProjectRoot ".venv")
    }

    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $VenvPython -m pip install -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if ($InstallCudaTorch) {
        & $VenvPython -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url $TorchCudaIndexUrl
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
}

Install-UiDependencies
Test-TorchCuda
if (-not $NoLaunch) {
    & $VenvPython $UiScript
    exit $LASTEXITCODE
}
