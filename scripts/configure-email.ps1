[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '../config.local.toml'),
    [string]$PythonPath = 'python'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot '..')
& $PythonPath -m wechat_tracker --config $ConfigPath email setup
if ($LASTEXITCODE -eq 0) {
    Write-Host 'SMTP configuration and test email completed. The service reloads credentials automatically.'
} else {
    Write-Host 'Configuration was not saved. Check that QQ SMTP is enabled and use its app authorization code.'
}
[void](Read-Host 'Press Enter to close this local setup window')
