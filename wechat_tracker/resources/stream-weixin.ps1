[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Group,
    [string]$ProcessName = 'Weixin',
    [ValidateRange(1, 1000)][int]$MaxItems = 200,
    [ValidateRange(2, 60)][int]$ResyncSeconds = 15,
    [switch]$FollowTail
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -ReferencedAssemblies @('UIAutomationClient','UIAutomationTypes','WindowsBase') -TypeDefinition @'
using System;
using System.Threading;
using System.Windows.Automation;
public static class WeixinChangePulse {
    static readonly AutoResetEvent pulse = new AutoResetEvent(false);
    static readonly StructureChangedEventHandler structure = (s,e) => pulse.Set();
    static readonly AutomationPropertyChangedEventHandler property = (s,e) => pulse.Set();
    public static bool Wait(int milliseconds) { return pulse.WaitOne(milliseconds); }
    public static void Bind(AutomationElement list) {
        Automation.RemoveAllEventHandlers();
        Automation.AddStructureChangedEventHandler(list, TreeScope.Subtree, structure);
        Automation.AddAutomationPropertyChangedEventHandler(list, TreeScope.Subtree, property,
            AutomationElement.NameProperty, AutomationElement.IsOffscreenProperty);
    }
    public static void Clear() { Automation.RemoveAllEventHandlers(); }
}
'@
function Emit-Frame($value) {
    [Console]::Out.WriteLine((ConvertTo-Json -InputObject $value -Depth 16 -Compress))
    [Console]::Out.Flush()
}
$reader = Join-Path $PSScriptRoot 'probe-weixin.ps1'
$boundHandle = 0
$lastContent = ''
$failures = 0
try {
    while ($true) {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        try {
            $tailSteps = $(if ($boundHandle -eq 0) { 20 } else { 1 })
            $raw = & $reader -Group $Group -ProcessName $ProcessName -Mode capture -MaxItems $MaxItems -KeepAlive -FollowTail:$FollowTail -TailSteps $tailSteps
            $snapshot = $raw | ConvertFrom-Json
            if (-not $snapshot.ok) {
                $failures++
                [WeixinChangePulse]::Clear()
                $boundHandle = 0
                $lastContent = ''
                Emit-Frame @{kind='state'; result=$snapshot; capture_ms=$timer.ElapsedMilliseconds}
                $delay = [int][Math]::Min(60, 3 * [Math]::Pow(2, [Math]::Min(5, $failures - 1)))
                Start-Sleep -Seconds $delay
                continue
            }
            $failures = 0
            $eventBound = $false
            if ($boundHandle -ne $snapshot.window_handle) {
                try {
                    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$snapshot.window_handle)
                    $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'chat_message_list')
                    $lists = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
                    if ($lists.Count -eq 1) { [WeixinChangePulse]::Bind($lists[0]); $boundHandle=$snapshot.window_handle }
                } catch { $boundHandle=0 }
            }
            $eventBound = $boundHandle -eq $snapshot.window_handle
            $content = ConvertTo-Json -InputObject @{messages=@($snapshot.messages); window_handle=$snapshot.window_handle; process_id=$snapshot.process_id} -Depth 10 -Compress
            if ($content -cne $lastContent) {
                Emit-Frame @{kind='snapshot'; result=$snapshot; capture_ms=$timer.ElapsedMilliseconds; event_bound=$eventBound}
                $lastContent = $content
            } else {
                # A fresh read proves liveness without repeatedly copying message bodies.
                Emit-Frame @{kind='heartbeat'; group=$Group; observed_at=$snapshot.observed_at; message_count=$snapshot.message_count; diagnostics=$snapshot.diagnostics; capture_ms=$timer.ElapsedMilliseconds; event_bound=$eventBound}
            }
        } catch {
            Emit-Frame @{kind='state'; result=@{ok=$false; group=$Group; status='worker_read_error'; error='The verified capture worker failed; selectors or window state need rechecking.'}}
            $boundHandle=0
            [WeixinChangePulse]::Clear()
            Start-Sleep -Seconds 15
            continue
        }
        # The C# callback only sets an event; no PowerShell runs on UIA threads.
        # Periodic resync covers providers that omit accessibility change events.
        [void][WeixinChangePulse]::Wait($ResyncSeconds * 1000)
        Start-Sleep -Milliseconds 300
    }
} finally { [WeixinChangePulse]::Clear() }
