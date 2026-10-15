[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Group,
    [ValidateSet('expand')][string]$Mode = 'expand',
    [Parameter(Mandatory = $true)][long]$GroupWindowHandle,
    [Parameter(Mandatory = $true)][string]$TargetRuntimeId,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedTextSha256,
    [Parameter(Mandatory = $true)][string]$ForwardWindowTitle,
    [string]$ProcessName = 'Weixin',
    [Parameter(Mandatory = $true)][int]$TargetProcessId,
    [ValidateRange(1, 1000)][int]$MaxItems = 200,
    [ValidateRange(1, 20)][int]$MaxPages = 5,
    [ValidateRange(1, 60)][int]$IdleSeconds = 3
)

# Explicitly opt-in mutation driver. The normal probe script stays read-only.
# No key input, attachment download, link navigation or chat submission.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$openedHandle = [IntPtr]::Zero
$inputMark = $null
$groupRoot = $null
$result = $null

function Fail([string]$Code, [string]$Message) { throw ($Code + ': ' + $Message) }

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class WeixinForwardNative {
    public delegate bool EnumWindowsProc(IntPtr handle, IntPtr unused);
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
    [StructLayout(LayoutKind.Sequential)] struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr unused);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint processId);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr handle, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr handle);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr handle, uint flags);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT point);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
    [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO value);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr handle, uint msg, IntPtr wParam, IntPtr lParam);
    public static uint LastInputTick() {
        var value = new LASTINPUTINFO(); value.cbSize = (uint)Marshal.SizeOf(value);
        if (!GetLastInputInfo(ref value)) throw new InvalidOperationException("Input state unavailable");
        return value.dwTime;
    }
    public static uint IdleMilliseconds() { return unchecked((uint)Environment.TickCount - LastInputTick()); }
    public static void Click() {
        mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero);
        mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero);
    }
}
'@

    function Exact-Windows([string]$Title) {
        $found = New-Object 'System.Collections.Generic.List[System.IntPtr]'
        $callback = [WeixinForwardNative+EnumWindowsProc] {
            param([IntPtr]$handle, [IntPtr]$unused)
            [uint32]$candidateProcessId = 0
            [void][WeixinForwardNative]::GetWindowThreadProcessId($handle, [ref]$candidateProcessId)
            if ($candidateProcessId -eq $TargetProcessId) {
                $text = New-Object System.Text.StringBuilder 1024
                [void][WeixinForwardNative]::GetWindowText($handle, $text, $text.Capacity)
                if ($text.ToString() -ceq $Title) { $found.Add($handle) }
            }
            return $true
        }
        [void][WeixinForwardNative]::EnumWindows($callback, [IntPtr]::Zero)
        return $found.ToArray()
    }

    function Assert-Interaction([IntPtr]$ExpectedForeground) {
        if ([WeixinForwardNative]::GetForegroundWindow() -ne $ExpectedForeground) {
            Fail 'foreground_changed' 'Keep the bound target window in front during automatic expansion.'
        }
        if ($null -ne $script:inputMark -and [WeixinForwardNative]::LastInputTick() -ne $script:inputMark) {
            Fail 'user_interrupted' 'User input changed; remaining UI actions were stopped.'
        }
        if ($null -ne $script:groupRoot -and ($script:groupRoot.Current.Name -cne $Group -or $script:groupTitle.Current.Name -cne $Group)) {
            Fail 'group_changed' 'The independently verified group title changed.'
        }
    }

    if (-not ($ForwardWindowTitle -ceq '群聊的聊天记录' -or $ForwardWindowTitle -match '^.+和.+的聊天记录$')) {
        Fail 'invalid_forward_title' 'The exact detail title is unsupported.'
    }
    $process = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process -or $process.ProcessName -cne $ProcessName) { Fail 'process_changed' 'The captured Weixin process no longer matches.' }
    $groupHandle = [IntPtr]$GroupWindowHandle
    $groupWindows = @(Exact-Windows $Group)
    if ($groupWindows.Count -ne 1 -or $groupWindows[0] -ne $groupHandle -or
        -not [WeixinForwardNative]::IsWindowVisible($groupHandle) -or [WeixinForwardNative]::IsIconic($groupHandle)) {
        Fail 'group_window_changed' 'The captured exact independent group window is unavailable or ambiguous.'
    }
    $groupRoot = [System.Windows.Automation.AutomationElement]::FromHandle($groupHandle)
    if ($groupRoot.Current.ClassName -cne 'mmui::ChatSingleWindow') { Fail 'unsupported_window' 'The independent group class changed.' }
    $scope = [System.Windows.Automation.TreeScope]::Descendants
    $titleId = 'content_view.top_content_view.title_h_view.left_v_view.left_content_v_view.left_ui_.big_title_line_h_view.current_chat_name_label'
    $titleCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, $titleId)
    $titles = $groupRoot.FindAll($scope, $titleCondition)
    if ($titles.Count -ne 1 -or $titles[0].Current.Name -cne $Group) { Fail 'group_identity_mismatch' 'The internal group name cannot be independently verified.' }
    $groupTitle = $titles[0]
    $pageCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'chat_message_page')
    $pages = $groupRoot.FindAll($scope, $pageCondition)
    if ($pages.Count -ne 1 -or $pages[0].Current.ClassName -cne 'mmui::ChatMessagePage') { Fail 'message_page_mismatch' 'Expected one verified message page.' }
    $listCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'chat_message_list')
    $lists = $pages[0].FindAll($scope, $listCondition)
    if ($lists.Count -ne 1 -or $lists[0].Current.ClassName -cne 'mmui::RecyclerListView' -or
        $lists[0].Current.ControlType -ne [System.Windows.Automation.ControlType]::List -or $lists[0].Current.IsOffscreen) {
        Fail 'message_list_mismatch' 'Expected one verified visible group message list.'
    }
    $list = $lists[0]
    $rows = $list.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    if ($rows.Count -gt $MaxItems) { Fail 'too_many_items' 'The group snapshot exceeds the configured bound.' }
    $parentMatches = @($rows | Where-Object { (@($_.GetRuntimeId()) -join ',') -ceq $TargetRuntimeId })
    if ($parentMatches.Count -ne 1) { Fail 'parent_changed' 'The captured parent runtime id is no longer unique.' }
    $row = $parentMatches[0]
    $text = $row.Current.Name.Replace('\n', "`n").Trim()
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $actualHash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($actualHash -cne $ExpectedTextSha256 -or $row.Current.ControlType -ne [System.Windows.Automation.ControlType]::ListItem -or
        $row.Current.ClassName -cne 'mmui::ChatBubbleItemView' -or
        $text -notmatch '^(\[聊天记录\]|聊天记录|合并转发|转发的聊天记录|群聊的聊天记录|.+和.+的聊天记录)') {
        Fail 'parent_changed' 'The bound row content or type no longer matches a forward card.'
    }
    $rect = $row.Current.BoundingRectangle
    $listRect = $list.Current.BoundingRectangle
    if ($row.Current.IsOffscreen -or $rect.IsEmpty -or $rect.Left -lt $listRect.Left -or $rect.Right -gt $listRect.Right -or
        $rect.Top -lt $listRect.Top -or $rect.Bottom -gt $listRect.Bottom) {
        Fail 'forward_card_clipped' 'The whole forward card must be visible.'
    }
    # Refuse every pre-existing merged window, including differently titled ones.
    $existing = New-Object 'System.Collections.Generic.List[System.IntPtr]'
    $callback = [WeixinForwardNative+EnumWindowsProc] {
        param([IntPtr]$handle, [IntPtr]$unused)
        [uint32]$candidateProcessId = 0
        [void][WeixinForwardNative]::GetWindowThreadProcessId($handle, [ref]$candidateProcessId)
        if ($candidateProcessId -eq $TargetProcessId) {
            $title = New-Object System.Text.StringBuilder 1024
            [void][WeixinForwardNative]::GetWindowText($handle, $title, $title.Capacity)
            if ($title.ToString() -ceq '群聊的聊天记录' -or $title.ToString() -match '^.+和.+的聊天记录$') { $existing.Add($handle) }
        }
        return $true
    }
    [void][WeixinForwardNative]::EnumWindows($callback, [IntPtr]::Zero)
    if ($existing.Count -gt 0) { Fail 'detail_already_open' 'Close existing merged-history views before automatic expansion.' }
    if ([WeixinForwardNative]::IdleMilliseconds() -lt $IdleSeconds * 1000) { Fail 'user_active' 'Wait for the configured input idle interval.' }
    $inputMark = [WeixinForwardNative]::LastInputTick()
    Assert-Interaction $groupHandle
    $point = $row.GetClickablePoint()
    $nativePoint = New-Object WeixinForwardNative+POINT
    $nativePoint.X = [int]$point.X; $nativePoint.Y = [int]$point.Y
    if ($point.X -lt $rect.Left -or $point.X -ge $rect.Right -or $point.Y -lt $rect.Top -or $point.Y -ge $rect.Bottom -or
        [WeixinForwardNative]::GetAncestor([WeixinForwardNative]::WindowFromPoint($nativePoint), 2) -ne $groupHandle) {
        Fail 'card_obscured' 'The verified click point is not inside the unobscured parent window.'
    }
    if (-not [WeixinForwardNative]::SetCursorPos($nativePoint.X, $nativePoint.Y)) { Fail 'input_unavailable' 'Could not position the pointer over the verified card.' }
    # The target and point are rechecked immediately before the only click.
    $actualPoint = New-Object WeixinForwardNative+POINT
    if ([WeixinForwardNative]::GetForegroundWindow() -ne $groupHandle -or
        -not [WeixinForwardNative]::GetCursorPos([ref]$actualPoint) -or
        $actualPoint.X -ne $nativePoint.X -or $actualPoint.Y -ne $nativePoint.Y -or
        [WeixinForwardNative]::GetAncestor([WeixinForwardNative]::WindowFromPoint($nativePoint), 2) -ne $groupHandle) {
        Fail 'foreground_changed' 'The parent was covered before the click.'
    }
    [WeixinForwardNative]::Click()
    $inputMark = [WeixinForwardNative]::LastInputTick()
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(4)
    do {
        if ([WeixinForwardNative]::LastInputTick() -ne $inputMark) { Fail 'user_interrupted' 'User input changed while the detail was opening.' }
        $details = @(Exact-Windows $ForwardWindowTitle)
        if ($details.Count -gt 1) { Fail 'ambiguous_detail' 'More than one new detail window appeared.' }
        if ($details.Count -eq 1) { $openedHandle = $details[0]; break }
        Start-Sleep -Milliseconds 100
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    if ($openedHandle -eq [IntPtr]::Zero) { Fail 'detail_open_timeout' 'The verified card did not open a new matching detail window.' }
    Assert-Interaction $openedHandle
    $detailRoot = [System.Windows.Automation.AutomationElement]::FromHandle($openedHandle)
    if ($detailRoot.Current.Name -cne $ForwardWindowTitle -or $detailRoot.Current.ProcessId -ne $TargetProcessId) {
        Fail 'detail_identity_changed' 'The newly opened detail identity changed.'
    }
    $detailListCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::List)
    $detailLists = $detailRoot.FindAll($scope, $detailListCondition)
    if ($detailLists.Count -ne 1 -or $detailLists[0].Current.IsOffscreen) { Fail 'forward_list_mismatch' 'The new detail must expose exactly one visible list.' }
    $detailList = $detailLists[0]
    $scroll = $null
    $canScroll = $detailList.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$scroll)
    $reasons = New-Object 'System.Collections.Generic.List[string]'
    $items = New-Object 'System.Collections.Generic.List[object]'
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    $complete = $false
    if ($canScroll -and $scroll.Current.VerticallyScrollable) {
        Assert-Interaction $openedHandle
        $scroll.SetScrollPercent([System.Windows.Automation.ScrollPattern]::NoScroll, 0)
        Start-Sleep -Milliseconds 150
    } elseif (-not $canScroll) { $reasons.Add('scroll_pattern_unavailable') }
    for ($page = 0; $page -lt $MaxPages; $page++) {
        Assert-Interaction $openedHandle
        $children = $detailList.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
        if ($children.Count -gt $MaxItems) { Fail 'too_many_items' 'The detail page exceeds the configured bound.' }
        $ordinals = @{}
        foreach ($child in $children) {
            $current = $child.Current
            if ($current.ControlType -ne [System.Windows.Automation.ControlType]::ListItem) { Fail 'forward_row_mismatch' 'A detail child is not a ListItem.' }
            $raw = $current.Name.Replace('\n', "`n").Trim()
            if (-not $raw) { $reasons.Add('empty_or_virtualized_item'); continue }
            if ($current.AutomationId -cne 'qt_scrollarea_viewport.chat_bubble_item_view') { Fail 'forward_row_mismatch' 'An unfamiliar detail row was encountered.' }
            if ($current.IsOffscreen) { continue }
            $ordinal = 0
            if ($ordinals.ContainsKey($raw)) { $ordinal = $ordinals[$raw] }
            $ordinals[$raw] = $ordinal + 1
            $key = [string]$ordinal + ':' + $raw
            if ($seen.Add($key)) {
                $items.Add([pscustomobject]@{text=$raw; automation_id=$current.AutomationId; is_offscreen=$false})
                if ($items.Count -gt $MaxItems) { Fail 'too_many_items' 'Collected detail rows exceed the configured total bound.' }
            }
        }
        if (-not $canScroll) { break }
        if (-not $scroll.Current.VerticallyScrollable -or $scroll.Current.VerticalScrollPercent -ge 99.9) { $complete = $true; break }
        if ($page + 1 -ge $MaxPages) { $reasons.Add('page_limit_reached'); break }
        Assert-Interaction $openedHandle
        $before = $scroll.Current.VerticalScrollPercent
        # Overlap pages so a row crossing a viewport edge remains represented.
        $advance = [Math]::Max(0.1, $scroll.Current.VerticalViewSize * 0.75)
        $scroll.SetScrollPercent([System.Windows.Automation.ScrollPattern]::NoScroll, [Math]::Min(100, $before + $advance))
        Start-Sleep -Milliseconds 150
        if ($scroll.Current.VerticalScrollPercent -le $before) { $reasons.Add('scroll_stalled'); break }
    }
    Assert-Interaction $openedHandle
    if ($items.Count -eq 0) { Fail 'forward_empty' 'No readable detail rows were observed.' }
    $result = [ordered]@{
        ok=$true; status='ready'; source='windows_uia'; group=$Group
        process_name=$ProcessName; process_id=$TargetProcessId
        window_title=$ForwardWindowTitle; window_handle=$openedHandle.ToInt64()
        observed_at=[DateTimeOffset]::UtcNow.ToString('o'); list_count=1; items=$items.ToArray()
        history_complete=$complete; incomplete_reasons=$reasons.ToArray()
        coverage=('bounded detail ScrollPattern traversal; max_pages=' + $MaxPages)
        binding=[ordered]@{group_window_handle=$GroupWindowHandle; parent_runtime_id=@($TargetRuntimeId.Split(',') | ForEach-Object { [int]$_ }); parent_text_sha256=$ExpectedTextSha256; detail_window_handle=$openedHandle.ToInt64(); new_window_observed=$true; restored=$false}
    }
} catch {
    $code = 'forward_driver_error'
    $message = 'Automatic forward expansion failed; recheck the exact window and selectors.'
    if ($_.Exception.Message -match '^([a-z_]+):\s*(.*)$') { $code=$Matches[1]; $message=$Matches[2] }
    $result = [ordered]@{ok=$false; status=$code; group=$Group; error=$message}
} finally {
    # Close only the view opened by this operation, and only if the user did not
    # take over. A hard subprocess timeout may leave the view open for inspection.
    if ($openedHandle -ne [IntPtr]::Zero) {
        try {
            Assert-Interaction $openedHandle
            [void][WeixinForwardNative]::PostMessage($openedHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
            $closeDeadline = [DateTimeOffset]::UtcNow.AddSeconds(2)
            while ([WeixinForwardNative]::IsWindow($openedHandle) -and [DateTimeOffset]::UtcNow -lt $closeDeadline) { Start-Sleep -Milliseconds 100 }
            if ([WeixinForwardNative]::IsWindow($openedHandle)) { Fail 'detail_close_timeout' 'The newly opened detail did not close.' }
            Assert-Interaction ([IntPtr]$GroupWindowHandle)
            if ($result.ok) { $result.binding.restored=$true }
        } catch {
            if ($result.ok) {
                $result = [ordered]@{ok=$false; status='return_not_verified'; group=$Group; error='The user took over or return to the original group could not be verified; inspect any open detail manually.'}
            } else { $result['return_verified']=$false }
        }
    }
}
ConvertTo-Json -InputObject $result -Depth 12 -Compress | Write-Output
if (-not $result.ok) { exit 1 }
