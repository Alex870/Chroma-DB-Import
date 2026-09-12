param(
    [ValidateSet("Prompt", "RunUi", "ManagedContexts", "Debug", "Migrate", "CreateCondaEnv")]
    [string]$Action = "Prompt",
    [ValidateSet("Legacy", "Modern")]
    [string]$Ui = "Modern"
)

function Wait-ForExitPrompt {
    if (-not $env:CHROMA_IMPORT_SUPPRESS_PAUSE -and $Host.Name -eq "ConsoleHost") {
        [void](Read-Host "Press Enter to continue")
    }
}

function Exit-Script {
    param([int]$Code = 0)
    Wait-ForExitPrompt
    exit $Code
}

trap {
    Write-Error $_
    Exit-Script 1
}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$CliScript = Join-Path $ScriptRoot "scripts\Run-ChromaDbImport.ps1"
$UiScript = Join-Path $ScriptRoot "scripts\Run-ChromaDbImportUi.ps1"
$DebugScript = Join-Path $ScriptRoot "scripts\Test-ChromaDbImportEnvironment.ps1"
$MigrationScript = Join-Path $ScriptRoot "scripts\Migrate-LegacyChromaDbImportState.ps1"

function Invoke-LauncherScript {
    param(
        [string]$Path,
        [hashtable]$Parameters = @{}
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing launcher script: $Path"
    }

    $previousSuppressPause = $env:CHROMA_IMPORT_SUPPRESS_PAUSE
    $env:CHROMA_IMPORT_SUPPRESS_PAUSE = "1"
    try {
        & $Path @Parameters
        $childExitCode = $LASTEXITCODE
    } finally {
        if ($null -eq $previousSuppressPause) {
            Remove-Item Env:CHROMA_IMPORT_SUPPRESS_PAUSE -ErrorAction SilentlyContinue
        } else {
            $env:CHROMA_IMPORT_SUPPRESS_PAUSE = $previousSuppressPause
        }
    }

    Exit-Script $childExitCode
}

function Invoke-LauncherScriptAndReturn {
    param(
        [string]$Path,
        [hashtable]$Parameters = @{}
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing launcher script: $Path"
    }

    $previousSuppressPause = $env:CHROMA_IMPORT_SUPPRESS_PAUSE
    $env:CHROMA_IMPORT_SUPPRESS_PAUSE = "1"
    try {
        & $Path @Parameters | Out-Host
        $childExitCode = $LASTEXITCODE
        return [int]$childExitCode
    } finally {
        if ($null -eq $previousSuppressPause) {
            Remove-Item Env:CHROMA_IMPORT_SUPPRESS_PAUSE -ErrorAction SilentlyContinue
        } else {
            $env:CHROMA_IMPORT_SUPPRESS_PAUSE = $previousSuppressPause
        }
    }
}

if ($Action -eq "Prompt") {
    Write-Host ""
    Write-Host "Chroma DB Import"
    Write-Host "Choose what to run:"
    Write-Host "  1. Run environment validation"
    Write-Host "  2. Run the desktop UI"
    Write-Host "  3. Migrate settings and state from a legacy directory"
    Write-Host "  4. Create or refresh the CLI and UI environments"
    Write-Host "  5. Open the managed context import workspace"
    Write-Host "  Q. Quit"
    $selection = (Read-Host "Enter 1, 2, 3, 4, 5, or Q").Trim()

    switch ($selection.ToUpperInvariant()) {
        "1" { $Action = "Debug" }
        "2" { $Action = "RunUi" }
        "3" { $Action = "Migrate" }
        "4" { $Action = "CreateCondaEnv" }
        "5" { $Action = "ManagedContexts" }
        "Q" { Exit-Script 0 }
        default {
            Write-Host "Unrecognized selection. Exiting."
            Exit-Script 1
        }
    }
}

switch ($Action) {
    "Debug" {
        Invoke-LauncherScript -Path $DebugScript
    }
    "RunUi" {
        Invoke-LauncherScript -Path $UiScript -Parameters @{ Ui = $Ui }
    }
    "ManagedContexts" {
        Invoke-LauncherScript -Path $UiScript -Parameters @{ Workspace = "Contexts"; Ui = $Ui }
    }
    "Migrate" {
        Invoke-LauncherScript -Path $MigrationScript
    }
    "CreateCondaEnv" {
        $cliExitCode = Invoke-LauncherScriptAndReturn -Path $CliScript -Parameters @{ CreateCondaEnv = $true; InstallCudaTorch = $true }
        if ($cliExitCode -ne 0) {
            Exit-Script $cliExitCode
        }
        $uiExitCode = Invoke-LauncherScriptAndReturn -Path $UiScript -Parameters @{ NoLaunch = $true; Ui = $Ui }
        Exit-Script $uiExitCode
    }
}

Exit-Script 0
