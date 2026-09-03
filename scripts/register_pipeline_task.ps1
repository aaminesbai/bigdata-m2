[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = "CHU Big Data Pipeline",
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyAt = "02:00",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}

$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$pythonPath = Join-Path $resolvedProjectRoot ".venv\Scripts\python.exe"
$pipelinePath = Join-Path $resolvedProjectRoot "scripts\run_pipeline.py"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python executable not found: $pythonPath"
}

if (-not (Test-Path -LiteralPath $pipelinePath -PathType Leaf)) {
    throw "Pipeline script not found: $pipelinePath"
}

$scheduleTime = [datetime]::ParseExact(
    $DailyAt,
    "HH:mm",
    [Globalization.CultureInfo]::InvariantCulture
)
$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$arguments = '"{0}"' -f $pipelinePath

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument $arguments `
    -WorkingDirectory $resolvedProjectRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $scheduleTime

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, "Register daily task at $DailyAt")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Collecte et transformation CHU vers Bronze, Silver et Gold." `
        -Force | Out-Null

    Write-Host "Scheduled task '$TaskName' registered for $DailyAt."
    Write-Host "The task runs only while $currentUser has an interactive session."
}
