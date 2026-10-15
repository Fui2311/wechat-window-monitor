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

# Read-only UI Automation. This script never invokes, focuses, scrolls, types,
# downloads files, or reads another conversation's control tree.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$observedAt = [DateTimeOffset]::UtcNow.ToString('o')

function Emit-Result($Result) {
    ConvertTo-Json -InputObject $Result -Depth 12 -Compress | Write-Output
}

function Fail-Capture([string]$Code, [string]$Message) {
    throw ($Code + ': ' + $Message)
}

try {
    if ([string]::IsNullOrWhiteSpace($Group)) {
        Fail-Capture 'invalid_group' 'An exact group name is required.'
    }
    $targetTitle = $Group
    if ($Mode -eq 'forward') {
        if ([string]::IsNullOrWhiteSpace($ForwardWindowTitle)) {
            Fail-Capture 'invalid_forward_title' 'An exact already-open merged-history title is required.'
        }
        $targetTitle = $ForwardWindowTitle
    }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class WeixinReadOnlyWindows {
    public delegate bool EnumWindowsProc(IntPtr handle, IntPtr parameter);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr handle, StringBuilder text, int count);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr handle, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr handle);
}
'@
    $processes = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
    if ($TargetProcessId -gt 0) {
        $processes = @($processes | Where-Object { $_.Id -eq $TargetProcessId })
    }
    if ($processes.Count -eq 0) {
        Fail-Capture 'process_not_found' 'The configured Weixin process is not running.'
    }
    $allowedIds = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($process in $processes) { [void]$allowedIds.Add($process.Id) }
    $candidates = New-Object 'System.Collections.Generic.List[System.IntPtr]'
    $callback = [WeixinReadOnlyWindows+EnumWindowsProc] {
        param([IntPtr]$handle, [IntPtr]$unused)
        [uint32]$windowProcessId = 0
        [void][WeixinReadOnlyWindows]::GetWindowThreadProcessId($handle, [ref]$windowProcessId)
        if ($allowedIds.Contains([int]$windowProcessId)) {
            $title = New-Object System.Text.StringBuilder 1024
            [void][WeixinReadOnlyWindows]::GetWindowText($handle, $title, $title.Capacity)
            if ($title.ToString() -ceq $targetTitle -and ($TargetWindowHandle -eq 0 -or $handle.ToInt64() -eq $TargetWindowHandle)) { $candidates.Add($handle) }
        }
        return $true
    }
    [void][WeixinReadOnlyWindows]::EnumWindows($callback, [IntPtr]::Zero)
    if ($candidates.Count -eq 0) {
        Fail-Capture 'window_not_found' 'Open the exact target group in its own Weixin window.'
    }
    if ($candidates.Count -ne 1) {
        Fail-Capture 'ambiguous_window' 'More than one window has the exact target title.'
    }
    $windowHandle = $candidates[0]
    if (-not [WeixinReadOnlyWindows]::IsWindowVisible($windowHandle) -or [WeixinReadOnlyWindows]::IsIconic($windowHandle)) {
        Fail-Capture 'window_not_visible' 'The target window is hidden or minimized.'
    }
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($windowHandle)
    if ($Mode -eq 'forward') {
        if ($null -eq $root -or $root.Current.Name -cne $targetTitle -or $root.Current.ControlType -ne [System.Windows.Automation.ControlType]::Window) {
            Fail-Capture 'forward_window_mismatch' 'The existing merged-history window identity no longer matches.'
        }
        $listCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::List)
        $forwardLists = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $listCondition)
        if ($forwardLists.Count -ne 1 -or $forwardLists[0].Current.IsOffscreen) {
            Fail-Capture 'forward_list_mismatch' 'Expected one visible List in the explicitly bound merged-history window.'
        }
        $forwardChildren = $forwardLists[0].FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
        if ($forwardChildren.Count -gt $MaxItems) { Fail-Capture 'too_many_items' 'Merged history exceeds the configured safe snapshot size.' }
        $forwardItems = New-Object 'System.Collections.Generic.List[object]'
        foreach ($child in $forwardChildren) {
            $current = $child.Current
            if ($current.ControlType -ne [System.Windows.Automation.ControlType]::ListItem) {
                Fail-Capture 'forward_row_mismatch' 'A direct merged-history child is not a ListItem.'
            }
            $rowText = $current.Name.Replace('\n', "`n").Trim()
            if ($rowText -and $current.AutomationId -cne 'qt_scrollarea_viewport.chat_bubble_item_view') {
                Fail-Capture 'forward_row_mismatch' 'An unverified merged-history row AutomationId was encountered.'
            }
            $forwardItems.Add([pscustomobject]@{ text = $rowText; automation_id = $current.AutomationId; is_offscreen = $current.IsOffscreen })
        }
        if ($root.Current.Name -cne $targetTitle) { Fail-Capture 'forward_window_changed' 'The merged-history title changed during collection.' }
        Emit-Result ([ordered]@{
            ok = $true; status = 'ready'; source = 'windows_uia'; group = $Group
            window_title = $targetTitle; window_handle = $windowHandle.ToInt64()
            process_name = $ProcessName; process_id = $root.Current.ProcessId
            list_count = 1; observed_at = $observedAt; items = $forwardItems.ToArray()
            source_group_verified = $false; coverage = 'loaded merged-history rows only; no expansion or scrolling'
        })
        exit 0
    }
    if ($null -eq $root -or $root.Current.Name -cne $Group -or $root.Current.ClassName -cne 'mmui::ChatSingleWindow') {
        Fail-Capture 'unsupported_window' 'The exact independent group window selector no longer matches.'
    }
    $scope = [System.Windows.Automation.TreeScope]::Descendants
    $titleId = 'content_view.top_content_view.title_h_view.left_v_view.left_content_v_view.left_ui_.big_title_line_h_view.current_chat_name_label'
    $titleCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, $titleId)
    $titles = $root.FindAll($scope, $titleCondition)
    if ($titles.Count -ne 1 -or $titles[0].Current.Name -cne $Group -or $titles[0].Current.ClassName -cne 'mmui::XTextView') {
        Fail-Capture 'group_identity_mismatch' 'The internal chat title cannot independently confirm the configured group.'
    }
    $pageCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'chat_message_page')
    $pages = $root.FindAll($scope, $pageCondition)
    if ($pages.Count -ne 1 -or $pages[0].Current.ClassName -cne 'mmui::ChatMessagePage') {
        Fail-Capture 'message_page_mismatch' 'The exact message page selector is unavailable.'
    }
    $listCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'chat_message_list')
    $lists = $pages[0].FindAll($scope, $listCondition)
    if ($lists.Count -ne 1) {
        Fail-Capture 'message_list_mismatch' 'Expected exactly one chat_message_list inside the confirmed message page.'
    }
    $list = $lists[0]
    if ($list.Current.ClassName -cne 'mmui::RecyclerListView' -or $list.Current.ControlType -ne [System.Windows.Automation.ControlType]::List -or $list.Current.IsOffscreen) {
        Fail-Capture 'message_list_mismatch' 'The message list class, type, or visibility is unsupported.'
    }
    $listRect = $list.Current.BoundingRectangle
    if ($listRect.IsEmpty -or $listRect.Width -le 0 -or $listRect.Height -le 0) {
        Fail-Capture 'message_list_not_visible' 'The message list has no visible rectangle.'
    }
    $children = $list.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    if ($children.Count -gt $MaxItems) {
        Fail-Capture 'too_many_items' 'The message list exceeds the configured safe snapshot size.'
    }
    $messages = New-Object 'System.Collections.Generic.List[object]'
    $markers = New-Object 'System.Collections.Generic.List[string]'
    $classes = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($item in $children) {
        $current = $item.Current
        if ($current.IsOffscreen) { continue }
        $rect = $current.BoundingRectangle
        if ($rect.IsEmpty -or $rect.Width -le 0 -or $rect.Height -le 0 -or $rect.Right -le $listRect.Left -or $rect.Left -ge $listRect.Right -or $rect.Bottom -le $listRect.Top -or $rect.Top -ge $listRect.Bottom) { continue }
        if ($current.ControlType -ne [System.Windows.Automation.ControlType]::ListItem) {
            Fail-Capture 'unsupported_row' 'A visible message-list child is not a ListItem.'
        }
        [void]$classes.Add($current.ClassName)
        $text = $current.Name.Replace('\n', "`n").Trim()
        if ($current.ClassName -ceq 'mmui::ChatItemView') {
            # Timestamp separators lack a complete, trustworthy message timestamp.
            if ($text -match '^(?:(?:今天|昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天])\s*)?(?:上午|下午|晚上|凌晨|早上|中午)?\s*\d{1,2}:\d{2}$' -or $text -match '^\d{4}年\d{1,2}月\d{1,2}日(?:\s+.*)?$' -or $text -match '^\d{1,2}月\d{1,2}日(?:\s+.*)?$') {
                $markers.Add($text)
                continue
            }
            Fail-Capture 'unsupported_row' 'An unrecognized structural/system row requires selector review.'
        }
        if ($current.ClassName -cnotin @('mmui::ChatTextItemView', 'mmui::ChatBubbleItemView')) {
            Fail-Capture 'unsupported_row' ('Unverified message row class: ' + $current.ClassName)
        }
        if ([string]::IsNullOrWhiteSpace($text)) {
            Fail-Capture 'unreadable_row' 'A visible message row exposes no accessible text.'
        }
        $messages.Add([pscustomobject]@{
            text = $text
            class_name = $current.ClassName
            runtime_id = @($item.GetRuntimeId())
            fully_visible = ($rect.Left -ge $listRect.Left -and $rect.Right -le $listRect.Right -and $rect.Top -ge $listRect.Top -and $rect.Bottom -le $listRect.Bottom)
        })
    }
    # Check identity again after collection to reject a concurrent navigation.
    if ($root.Current.Name -cne $Group -or $titles[0].Current.Name -cne $Group) {
        Fail-Capture 'group_changed_during_read' 'The group identity changed during the snapshot.'
    }
    $result = [ordered]@{
        ok = $true
        status = 'ready'
        source = 'windows_uia'
        group = $Group
        observed_at = $observedAt
        message_count = $messages.Count
        diagnostics = [ordered]@{
            window_class = $root.Current.ClassName
            message_list_id = 'chat_message_list'
            message_list_class = $list.Current.ClassName
            row_classes = @($classes)
            time_markers = @($markers)
            sender_available = $false
            sent_at_available = $false
            scope = 'loaded viewport-intersecting rows only'
        }
    }
    if ($Mode -eq 'capture') { $result['messages'] = $messages.ToArray() }
    Emit-Result $result
} catch {
    $reason = $_.Exception.Message
    $code = 'uia_error'
    if ($reason -match '^([a-z_]+):\s*(.*)$') {
        $code = $Matches[1]
        $reason = $Matches[2]
    }
    Emit-Result ([ordered]@{ ok = $false; status = $code; source = 'windows_uia'; group = $Group; observed_at = $observedAt; error = $reason })
    exit 1
}
