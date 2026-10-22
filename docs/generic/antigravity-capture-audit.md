# 有界静态核查：微信会话窗口采集泛化能力审计

**审计范围**（只读，不可写，不可执行）

| 文件 | 行数 |
|------|------|
| `wechat_tracker/capture.py` | 383 |
| `wechat_tracker/macos.py` | 213 |
| `wechat_tracker/resources/mac-capture.swift` | 1085 |
| `wechat_tracker/resources/probe-weixin.ps1` | 293 |
| `wechat_tracker/resources/stream-weixin.ps1` | 87 |

> **说明**：本报告仅基于逐行阅读以上五个文件，不依赖变量名语义推断，不执行任何代码，不读取配置/凭据/数据库/状态，不修改任何文件（唯一写入：本报告本身）。

---

## 1. 原生绑定是否真正限制"群类型"？

### 结论：**不限制会话类型，限制的是精确窗口标题字符串**

---

### 1.1 Windows UIA（`probe-weixin.ps1`）

**窗口查找逻辑**（第 77–95 行）：

```powershell
# probe-weixin.ps1 第 82–84 行
$title = New-Object System.Text.StringBuilder 1024
[void][WeixinReadOnlyWindows]::GetWindowText($handle, $title, $title.Capacity)
if ($title.ToString() -ceq $targetTitle …) { $candidates.Add($handle) }
```

`$targetTitle` 对 capture/probe 模式等于 `$Group`（第 33 行），对 forward 模式等于 `$ForwardWindowTitle`（第 38 行）。  
没有任何逻辑检查窗口是否是"群"；`GetWindowText` 返回什么，就与之做区分大小写的精确字符串比较（`-ceq`）。

**身份二次验证**（第 133–141 行）：

```powershell
# probe-weixin.ps1 第 133 行
if ($null -eq $root -or $root.Current.Name -cne $Group -or
    $root.Current.ClassName -cne 'mmui::ChatSingleWindow') {
    Fail-Capture 'unsupported_window' ...
}
# 第 140 行
if ($titles.Count -ne 1 -or $titles[0].Current.Name -cne $Group -or
    $titles[0].Current.ClassName -cne 'mmui::XTextView') {
    Fail-Capture 'group_identity_mismatch' ...
}
```

`ClassName -cne 'mmui::ChatSingleWindow'` 是 Weixin Windows 版中**所有对话窗口**（群聊、私聊、文件传输助手等）的通用 Qt 渲染层类名，不是群专属类名。

**采集后 re-check**（第 243 行）：

```powershell
if ($root.Current.Name -cne $Group -or $titles[0].Current.Name -cne $Group) {
    Fail-Capture 'group_changed_during_read' ...
}
```

仍只检查精确标题，不判断会话类型。

---

### 1.2 macOS AX（`mac-capture.swift`）

**窗口候选筛选**（第 147–155 行）：

```swift
// mac-capture.swift 第 147–154 行
for window in windows {
    if text(window, kAXTitleAttribute) == group {
        candidates.append((window, ["method": "exact_window_title"]))
    } else if !headerID.isEmpty {
        let headers = walk(window).filter { text($0, kAXIdentifierAttribute) == headerID }
        if headers.count == 1 && body(headers[0]) == group {
            candidates.append((window, ["method": "explicit_header_identifier", ...]))
        }
    }
}
```

条件为 `kAXTitleAttribute == group`（精确字符串），或显式 header identifier 指向同一值。无群/私聊类型判断。

**macos.py 规范化验证**（第 56–60 行）：

```python
# macos.py 第 56–60 行
exact_window = snapshot.get("window_title") == group and binding.get("method") == "exact_window_title"
exact_header = (bool(options.get("mac_header_identifier")) and
                binding.get("header_identifier") == options["mac_header_identifier"] and
                binding.get("header_value") == group and binding.get("method") == "explicit_header_identifier")
if not (exact_window or exact_header):
    raise CaptureError("The macOS window does not independently prove the target group")
```

只验证标题或 header 值 == `group` 字符串，不涉及会话类型。

---

### 1.3 capture.py 通用层

**`_settings` 函数**（第 25–32 行）：

```python
# capture.py 第 25–32 行
def _settings(config: dict) -> tuple[str, dict]:
    group = config.get("group")
    if not isinstance(group, str) or not group.strip():
        raise CaptureError("A nonempty, exact top-level group name is required")
    options = config.get("capture", {})
    …
    return group, options
```

字段名为 `"group"`，错误消息中有 "group name"，但验证逻辑只要求"非空字符串"，不检查会话类型。  
**变量名 `group` 是语义命名遗产，不是类型过滤器**。

---

## 2. 精确窗口名匹配的已实现能力

| 能力 | Windows UIA | macOS AX |
|------|-------------|----------|
| 精确标题匹配（区分大小写） | ✅ `GetWindowText` + `-ceq`（probe-weixin.ps1:84） | ✅ `kAXTitleAttribute == group`（swift:148） |
| 采集前后双重验证标题一致性 | ✅（第 133 行 + 第 243 行） | ✅（macos.py `normalize_snapshot` 第 50 行） |
| 拒绝标题不唯一的歧义窗口 | ✅ `ambiguous_window`（第 92–94 行） | ✅ `candidates.count != 1`（swift:237） |
| 显式 process 绑定可选配 | ✅ `-TargetProcessId` + `-ProcessName`（第 5–6 行） | ✅ `bundle_id`（swift:16） |
| 隐式 header 二次验证 | ✅ `AutomationId=…title…` + `Name -cne $Group`（第 137–141 行） | ✅ `headerID`（swift:150–154）|
| 采集期间并发导航保护 | ✅ re-check（第 243 行） | ✅ `bindGroup()` 在快照前后 |

---

## 3. 安全拒绝歧义的已实现机制

| 场景 | 错误代码 | 来源文件 + 行 |
|------|----------|---------------|
| 目标进程不存在 | `process_not_found` | probe-weixin.ps1:71–73 |
| 匹配标题的窗口不唯一 | `ambiguous_window` | probe-weixin.ps1:92–94 / swift:237 |
| 窗口不可见或已最小化 | `window_not_visible` | probe-weixin.ps1:96–98 |
| macOS 进程 bundle 不唯一 | `ambiguous_process` | swift:135 |
| 窗口类名不匹配（Windows） | `unsupported_window` | probe-weixin.ps1:133–135 |
| 内部标题 label 不匹配 | `group_identity_mismatch` | probe-weixin.ps1:139–142 |
| 采集过程中标题发生变化 | `group_changed_during_read` | probe-weixin.ps1:243–245 |
| macOS AX 未 prove 目标窗口 | `target_window_not_ready` | swift:237 |
| macOS AX header 值不匹配 | `CaptureError(…not independently prove…)` | macos.py:60–61 |
| UIA 回包 group 字段不一致 | `CaptureError(…different group…)` | capture.py:108–109 |
| JSONL 行的 group 字段不匹配 | `CaptureError(…does not match…)` | capture.py:131 |

歧义拒绝是 **fail-closed** 的：任何一个验证步骤失败都会抛出异常并中止采集，不会静默降级。

---

## 4. 可读消息类型列表（已实现）

### 4.1 分类器 `_classify`（capture.py 第 44–58 行）

```python
def _classify(text: str, class_name: str) -> str:
    if class_name == "mmui::ChatTextItemView":                return "text"
    if first == "文件" or first.startswith("文件 "):          return "file"
    if first.startswith(("[聊天记录]", "聊天记录", …)):       return "forward"
    if first in {"图片", "[图片]"}:                           return "image"
    if first in {"视频", "[视频]"}:                           return "video"
    if first.startswith(("[语音]", "语音")):                  return "audio"
    return "card"
```

| kind | 含义 | 触发条件 |
|------|------|----------|
| `text` | 普通文字消息 | `ClassName == mmui::ChatTextItemView` 或无特征前缀 |
| `file` | 文件（仅元数据） | 开头为 `"文件"` |
| `forward` | 合并转发/聊天记录 | 开头为 `"聊天记录"` / `"群聊的聊天记录"` / `"[聊天记录]"` 等 |
| `image` | 图片（无内容） | `"图片"` 或 `"[图片]"` |
| `video` | 视频（无内容） | `"视频"` 或 `"[视频]"` |
| `audio` | 语音（无内容） | `"[语音]"` 或 `"语音"` 开头 |
| `card` | 其他卡片（小程序、位置等） | 默认回退 |

### 4.2 Windows UIA 接受的行类名（probe-weixin.ps1 第 229–231 行）

```powershell
if ($current.ClassName -cnotin @('mmui::ChatTextItemView', 'mmui::ChatBubbleItemView')) {
    Fail-Capture 'unsupported_row' ('Unverified message row class: ' + $current.ClassName)
}
```

只接受两种类；其他类名 fail-closed。

### 4.3 macOS AX 接受的行角色（mac-capture.swift 第 83–87 行）

```swift
let allowed = Set([kAXRowRole, kAXGroupRole, kAXStaticTextRole, kAXTextAreaRole])
if !allowed.contains(role) { try fail("unverified_row_role") }
```

---

## 5. 不可保证的会话类型

| 类型 | 原因（源码证据） |
|------|----------------|
| **服务号 / 公众号** | Windows 端窗口类名可能不是 `mmui::ChatSingleWindow`（probe-weixin.ps1:133 硬判断此类名）；代码中无例外处理路径 |
| **文件传输助手** | 未见对应 UIA/AX 树结构（`chat_message_page` / `chat_message_list`）的测试证据；probe-weixin.ps1:144–156 的选择器路径假定标准聊天窗口布局 |
| **多开实例下任意会话** | macOS 要求 bundle ID 对应恰好一个 `NSRunningApplication`（swift:135 `ambiguous_process`）；Windows 需显式 `-TargetProcessId` |
| **WeChat International / 企业微信** | bundle ID 写死为 `com.tencent.xinWeChat`（swift:16 / macos.py `bundle_id` 默认值 / capture.py 同） |
| **合并转发的完整历史** | `history_complete` 在所有路径中均为 `false`（swift:458 / probe-weixin.ps1:129 / macos.py:213） |
| **sender / sent_at 字段** | Windows UIA 路径的 diagnostics 明确报告 `sender_available: false`, `sent_at_available: false`（probe-weixin.ps1:268–270）；macOS AX 路径 `sender=None, sent_at=None`（macos.py:92） |

---

## 6. 新适配器（泛化为任意精确名称会话）所需设置

以下配置项已在代码中有读取入口，新目标会话只需正确填写，**无需修改实现代码**（除非遭遇第 5 节所列边界情形）。

### 6.1 通用必填项（capture.py `_settings` 第 25–32 行）

```jsonc
{
  "group": "<精确窗口标题，区分大小写>",
  "capture": { "source": "windows_uia" }  // 或 "macos_ax" | "jsonl"
}
```

`"group"` 的值即为目标会话在微信中显示的精确窗口标题（群名、联系人昵称、文件传输助手等）。

### 6.2 Windows UIA 额外设置（probe-weixin.ps1 参数声明第 1–13 行）

| 配置项 | 必要性 | 说明 |
|--------|--------|------|
| `capture.process_name` | 可选（默认 `"Weixin"`） | 非标准安装路径时覆盖 |
| `capture.process_id` | 可选 | 多开时锁定具体 PID（probe-weixin.ps1:68–70） |
| `capture.max_items` | 可选（默认 200） | 1–1000，超出则 fail |
| `capture.timeout_seconds` | 可选（默认 8） | UIA 子进程超时（capture.py:90） |

> **关键边界**：`mmui::ChatSingleWindow` 类名检查（probe-weixin.ps1 第 133 行）是当前代码中**唯一一处可能排除非标准窗口**的硬判断。如果目标会话的窗口类名不同，Codex 实现者需要评估是否扩展此检查。

### 6.3 macOS AX 额外设置（mac-capture.swift 参数第 11–22 行 / macos.py `_helper` 第 21–29 行）

| 配置项 | 必要性 | 说明 |
|--------|--------|------|
| `capture.bundle_id` | 可选（默认 `com.tencent.xinWeChat`） | 其他客户端必须覆盖 |
| `capture.mac_header_identifier` | 可选 | 窗口标题被系统遮挡时的备用绑定（macos.py:57–59） |
| `capture.mac_list_identifier` | 可选 | 消息列表 AX identifier（swift `--list-id`）|
| `capture.mac_message_identity` | 可选（默认 `"content"`） | `"ax_row"` 时锁定 AX element token（macos.py:85） |
| `capture.forward_window_title` | forward 模式必填 | 合并转发详情窗口标题 |

### 6.4 合并转发详情窗口标题的接受规则

**capture.py `normalize_forward_snapshot`（第 185 行）和 `expand_forward`（第 292 行）**：

```python
if not isinstance(expected_title, str) or not (
    expected_title == "群聊的聊天记录" or
    re.fullmatch(r".+和.+的聊天记录", expected_title)
):
    raise CaptureError("An exact supported forward_window_title is required")
```

**swift `readOpenForward`（第 656 行）和 `expand`（第 687 行）**（相同规则）：

```swift
if title != "群聊的聊天记录" && title.range(of: "^.+和.+的聊天记录$", options: .regularExpression) == nil {
    try fail("invalid_detail_title")
}
```

| 模式 | 适用场景 |
|------|----------|
| `"群聊的聊天记录"` | 群聊转发 |
| `"<甲>和<乙>的聊天记录"` | 私聊转发（正则 `.+和.+的聊天记录`） |

不在上述两种模式之外的标题将被拒绝（公众号转发等）。

---

## 7. 审计验证边界总结

| 已证明 | 未证明 / 超出范围 |
|--------|-----------------|
| 绑定机制不依赖会话类型字段，仅使用精确标题字符串 | 非 `mmui::ChatSingleWindow` 类名的窗口（如公众号）在 Windows 端的实际兼容性 |
| 精确字符串比较区分大小写（`-ceq` / Swift `==` / Python `!=`） | `sender` 和 `sent_at` 字段的可用性（现为 `None`/`false`） |
| 采集前后双重 identity 验证，歧义时 fail-closed | 多 Weixin 进程实例下 macOS AX 的行为 |
| 私聊转发正则 `.+和.+的聊天记录` 已实现 | 企业微信 / WeChat International 的 AX 树结构 |
| 消息种类 text/file/image/video/audio/forward/card 均有分类逻辑 | 合并转发的完整历史读取（`history_complete` 恒为 `false`）|
| `group` 配置项接受任意非空字符串，不内含类型过滤 | 超过 200 条消息的虚拟列表完整覆盖 |

---

*报告路径：`docs/generic/antigravity-capture-audit.md`*  
*生成时间：2026-10-23T02:05:43+08:00（本地时间）*  
*审计方法：只读静态代码分析，无运行时执行，无网络/配置/凭据访问*  
*唯一写入操作：本文件本身*
