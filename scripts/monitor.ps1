param(
    [ValidateSet('start','stop','restart','status','install','uninstall')]
    [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot '..')
if (-not (Test-Path -LiteralPath 'config.local.toml')) {
    throw 'Create config.local.toml first. See docs/service.md.'
}
$arguments = @('-m','wechat_tracker','--config','config.local.toml','service',$Action)
if ($Action -eq 'install') { $arguments += '--send' }
& python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Monitor operation failed. See service.log.' }
