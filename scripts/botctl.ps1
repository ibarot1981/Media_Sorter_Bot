param(
    [ValidateSet("help", "start", "start-background", "stop", "restart", "status", "logout")]
    [string]$Action = "help",
    [string]$Config = "config.yaml"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Write-Usage {
    Write-Host "Media Sorter Bot control script"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\botctl.bat help"
    Write-Host "  .\botctl.bat start"
    Write-Host "  .\botctl.bat start-background"
    Write-Host "  .\botctl.bat stop"
    Write-Host "  .\botctl.bat restart"
    Write-Host "  .\botctl.bat status"
    Write-Host "  .\botctl.bat logout"
    Write-Host ""
    Write-Host "Actions:"
    Write-Host "  help               Show this help text."
    Write-Host "  start              Start the local Bot API and Python app in the foreground."
    Write-Host "  start-background   Start the same stack in a hidden background PowerShell process."
    Write-Host "  stop               Stop this repo's Python app, Bot API server, and launcher."
    Write-Host "  restart            Stop any running instance for this repo, then start again."
    Write-Host "  status             Show whether the repo's bot processes are running."
    Write-Host "  logout             Call Telegram Bot API logOut on the cloud endpoint."
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -Config <path>     Use a specific config file. Default: config.yaml"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  .\botctl.bat start"
    Write-Host "  .\botctl.bat start-background"
    Write-Host "  .\botctl.bat restart -Config config.yaml"
}

function Get-PythonExe {
    param(
        [string]$RepoRootPath
    )

    $venvPython = Join-Path $RepoRootPath ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return (Resolve-Path -LiteralPath $venvPython).Path
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        return $pythonCommand.Source
    }

    throw "Python was not found. Create the virtual environment first or install Python."
}

function Get-AppConfig {
    param(
        [string]$PythonExe,
        [string]$RepoRootPath,
        [string]$ConfigPath
    )

    $resolvedConfigPath = $ConfigPath
    if (-not [System.IO.Path]::IsPathRooted($resolvedConfigPath)) {
        $resolvedConfigPath = Join-Path $RepoRootPath $resolvedConfigPath
    }

    $helperScript = Join-Path $RepoRootPath "scripts\get_runtime_config.py"
    $rawJson = & $PythonExe $helperScript $resolvedConfigPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to load config from $ConfigPath."
    }

    return $rawJson | ConvertFrom-Json
}

function Resolve-RepoPath {
    param(
        [string]$RepoRootPath,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }

    if ([System.IO.Path]::IsPathRooted($Value)) {
        return $Value
    }

    return Join-Path $RepoRootPath $Value
}

function Resolve-BotApiBinary {
    param(
        [string]$RepoRootPath,
        [string]$BinaryPath
    )

    if ([System.IO.Path]::IsPathRooted($BinaryPath)) {
        if (Test-Path -LiteralPath $BinaryPath) {
            return (Resolve-Path -LiteralPath $BinaryPath).Path
        }
        throw "Local Bot API binary was not found at '$BinaryPath'."
    }

    $repoCandidate = Join-Path $RepoRootPath $BinaryPath
    if (Test-Path -LiteralPath $repoCandidate) {
        return (Resolve-Path -LiteralPath $repoCandidate).Path
    }

    $command = Get-Command $BinaryPath -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command) {
        return $command.Source
    }

    throw "Local Bot API binary '$BinaryPath' was not found in the repo or on PATH."
}

function Ensure-Directory {
    param(
        [string]$PathValue
    )

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return
    }

    New-Item -ItemType Directory -Force -Path $PathValue | Out-Null
}

function Get-EnvironmentValue {
    param(
        [string]$Name
    )

    foreach ($scope in @("Process", "User", "Machine")) {
        $value = [Environment]::GetEnvironmentVariable($Name, $scope)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value
        }
    }

    return $null
}

function Wait-ForLocalBotApi {
    param(
        [string]$ListenHost,
        [int]$Port,
        [string]$BotToken,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $uri = "http://$ListenHost`:$Port/bot$BotToken/getMe"
    $lastError = $null

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 5
            if ($response.ok) {
                return
            }
            $lastError = $response.description
        }
        catch {
            $lastError = $_.Exception.Message
        }

        Start-Sleep -Seconds 1
    }

    throw "Local Bot API server did not become ready at $uri within $TimeoutSeconds seconds. Last error: $lastError"
}

function Get-RepoProcesses {
    param(
        [string]$RepoRootPath
    )

    $escapedRepoRoot = [Regex]::Escape($RepoRootPath)
    return @(Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -ieq "powershell.exe" -and $_.CommandLine -match "botctl\.ps1" -and $_.CommandLine -match "-Action\s+start(\s|$)") -or
        ($_.Name -ieq "telegram-bot-api.exe" -and $_.CommandLine -match $escapedRepoRoot) -or
        ($_.Name -ieq "python.exe" -and $_.CommandLine -match $escapedRepoRoot -and $_.CommandLine -match "src\.main")
    })
}

function Test-RepoRunning {
    param(
        [string]$RepoRootPath
    )

    return (Get-RepoProcesses -RepoRootPath $RepoRootPath).Count -gt 0
}

function Stop-RepoProcesses {
    param(
        [string]$RepoRootPath
    )

    $matchingProcesses = Get-RepoProcesses -RepoRootPath $RepoRootPath
    $stoppedCount = 0

    foreach ($process in $matchingProcesses) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
            $stoppedCount += 1
            Write-Host ("Stopped {0}: PID {1}" -f $process.Name, $process.ProcessId)
        }
        catch {
            Write-Host ("Could not stop {0}: PID {1} ({2})" -f $process.Name, $process.ProcessId, $_.Exception.Message)
        }
    }

    Start-Sleep -Seconds 2
    $remaining = Get-RepoProcesses -RepoRootPath $RepoRootPath
    if ($remaining.Count -gt 0) {
        Write-Host "Some matching processes still appear to be running:"
        $remaining | Select-Object Name, ProcessId | Format-Table -AutoSize | Out-String | Write-Host
    }
    elseif ($stoppedCount -eq 0) {
        Write-Host "No matching Media Sorter Bot processes were found."
    }
}

function Show-RepoStatus {
    param(
        [string]$RepoRootPath
    )

    $processes = Get-RepoProcesses -RepoRootPath $RepoRootPath
    if ($processes.Count -eq 0) {
        Write-Host "Media Sorter Bot is not running for this repo."
        return
    }

    Write-Host "Media Sorter Bot is running for this repo:"
    $processes |
        Select-Object Name, ProcessId, ParentProcessId, CommandLine |
        Format-List
}

function Invoke-CloudLogout {
    param(
        [string]$PythonExe,
        [string]$RepoRootPath,
        [string]$ConfigPath
    )

    $tokenReader = @'
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
config_path = Path(sys.argv[2])
if not config_path.is_absolute():
    config_path = (repo_root / config_path).resolve()

sys.path.insert(0, str(repo_root))

from src.config import load_config

print(load_config(config_path).telegram_bot_token)
'@

    $botToken = (& $PythonExe -c $tokenReader $RepoRootPath $ConfigPath).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($botToken)) {
        throw "Failed to read telegram_bot_token from $ConfigPath."
    }

    $logoutUri = "https://api.telegram.org/bot$botToken/logOut"
    Write-Host "Calling Bot API logOut so the bot can move from cloud Bot API to your local server..."
    $maxAttempts = 3
    $response = $null

    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            $response = Invoke-RestMethod -Method Post -Uri $logoutUri -TimeoutSec 30
            break
        }
        catch {
            if ($attempt -ge $maxAttempts) {
                throw
            }

            Write-Host "logOut attempt $attempt failed. Retrying in 3 seconds..."
            Start-Sleep -Seconds 3
        }
    }

    if (-not $response.ok) {
        $description = if ($response.description) { $response.description } else { "Unknown error" }
        throw "logOut failed: $description"
    }

    Write-Host "Cloud Bot API logOut succeeded."
    Write-Host "You can now start the local Bot API server."
    Write-Host "Telegram prevents moving back to the cloud Bot API for about 10 minutes after a successful logOut."
}

function Invoke-ForegroundStart {
    param(
        [string]$RepoRootPath,
        [string]$PythonExe,
        [object]$AppConfig
    )

    $resolvedConfigPath = $AppConfig.config_path
    $localBotApi = $AppConfig.local_bot_api
    $startedProcess = $null
    $exitCode = 0
    $telegramApiId = Get-EnvironmentValue -Name "TELEGRAM_API_ID"
    $telegramApiHash = Get-EnvironmentValue -Name "TELEGRAM_API_HASH"

    Set-Location $RepoRootPath

    try {
        if ($localBotApi.enabled) {
            if ($localBotApi.auto_start) {
                if ([string]::IsNullOrWhiteSpace($telegramApiId) -or [string]::IsNullOrWhiteSpace($telegramApiHash)) {
                    throw "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in your environment before starting local Bot API mode."
                }

                $botApiBinary = Resolve-BotApiBinary -RepoRootPath $RepoRootPath -BinaryPath $localBotApi.binary_path
                $workingDir = Resolve-RepoPath -RepoRootPath $RepoRootPath -Value $localBotApi.working_dir
                $tempDir = Resolve-RepoPath -RepoRootPath $RepoRootPath -Value $localBotApi.temp_dir
                $logFile = Resolve-RepoPath -RepoRootPath $RepoRootPath -Value $localBotApi.log_file

                Ensure-Directory -PathValue $workingDir
                Ensure-Directory -PathValue $tempDir
                Ensure-Directory -PathValue ([System.IO.Path]::GetDirectoryName($logFile))

                $arguments = @(
                    "--local",
                    "--api-id=$telegramApiId",
                    "--api-hash=$telegramApiHash",
                    "--http-port=$($localBotApi.http_port)",
                    "--dir=$workingDir",
                    "--temp-dir=$tempDir",
                    "--log=$logFile"
                )

                Write-Host "Starting telegram-bot-api on $($localBotApi.http_host):$($localBotApi.http_port)..."
                $startedProcess = Start-Process -FilePath $botApiBinary -ArgumentList $arguments -WorkingDirectory $RepoRootPath -PassThru -WindowStyle Hidden
            }

            Write-Host "Waiting for local Bot API server to become ready..."
            Wait-ForLocalBotApi -ListenHost $localBotApi.http_host -Port ([int]$localBotApi.http_port) -BotToken $AppConfig.telegram_bot_token
            Write-Host "Local Bot API server is ready."
        }

        Write-Host "Starting Media Sorter Bot..."
        & $PythonExe -m src.main --config $resolvedConfigPath --mode all
        $exitCode = $LASTEXITCODE
    }
    finally {
        if ($null -ne $startedProcess -and -not $startedProcess.HasExited) {
            Write-Host "Stopping local Bot API server..."
            Stop-Process -Id $startedProcess.Id -Force
        }
    }

    return $exitCode
}

function Invoke-BackgroundStart {
    param(
        [string]$RepoRootPath,
        [string]$ConfigPath
    )

    if (Test-RepoRunning -RepoRootPath $RepoRootPath) {
        Write-Host "Media Sorter Bot appears to already be running for this repo."
        Write-Host "Use .\botctl.bat restart if you want to restart it, or .\botctl.bat stop first."
        return 1
    }

    $scriptPath = Join-Path $RepoRootPath "scripts\botctl.ps1"
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $scriptPath,
        "-Action",
        "start",
        "-Config",
        $ConfigPath
    )

    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WorkingDirectory $RepoRootPath -WindowStyle Hidden -PassThru
    Write-Host ("Started Media Sorter Bot in background. Launcher PID: {0}" -f $process.Id)
    Write-Host "Use .\botctl.bat stop to stop it or .\botctl.bat restart to restart it."
    return 0
}

$pythonExe = Get-PythonExe -RepoRootPath $repoRoot

switch ($Action) {
    "help" {
        Write-Usage
        exit 0
    }
    "stop" {
        Stop-RepoProcesses -RepoRootPath $repoRoot
        exit 0
    }
    "status" {
        Show-RepoStatus -RepoRootPath $repoRoot
        exit 0
    }
    "logout" {
        Invoke-CloudLogout -PythonExe $pythonExe -RepoRootPath $repoRoot -ConfigPath $Config
        exit 0
    }
    "start-background" {
        $exitCode = Invoke-BackgroundStart -RepoRootPath $repoRoot -ConfigPath $Config
        exit $exitCode
    }
    "restart" {
        Write-Host "Stopping any existing Media Sorter Bot processes..."
        Stop-RepoProcesses -RepoRootPath $repoRoot
        Start-Sleep -Seconds 2
        $appConfig = Get-AppConfig -PythonExe $pythonExe -RepoRootPath $repoRoot -ConfigPath $Config
        $exitCode = Invoke-ForegroundStart -RepoRootPath $repoRoot -PythonExe $pythonExe -AppConfig $appConfig
        exit $exitCode
    }
    "start" {
        $appConfig = Get-AppConfig -PythonExe $pythonExe -RepoRootPath $repoRoot -ConfigPath $Config
        $exitCode = Invoke-ForegroundStart -RepoRootPath $repoRoot -PythonExe $pythonExe -AppConfig $appConfig
        exit $exitCode
    }
}
