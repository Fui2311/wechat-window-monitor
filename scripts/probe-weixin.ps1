[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Group,
    [ValidateSet('probe', 'capture', 'forward')][string]$Mode = 'probe',
    [int]$TargetProcessId = 0,
    [string]$ProcessName = 'Weixin',
    [string]$ForwardWindowTitle = '',
    [long]$TargetWindowHandle = 0,
    [ValidateRange(1, 1000)][int]$MaxItems = 200
)
# Compatibility launcher; the packaged resource is the authoritative script.
$readerPath = Join-Path $PSScriptRoot '../wechat_tracker/resources/probe-weixin.ps1'
& $readerPath @PSBoundParameters
