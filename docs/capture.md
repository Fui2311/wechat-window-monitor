# 目标群只读采集

适配器针对 Windows 个人微信 4.x 的独立群聊天窗口。默认群名是项目配置中的精确名称；适配器不会自行切群、置前、滚动、点击、下载附件或发送微信消息。采集失败会停止该次采集，不会退回到扫描整个桌面、联系人栏或输入框。

## 当前实际验证

2026-10-15 13:38（Asia/Shanghai），在本机微信 `4.1.15.13`、`D:\Weixin\Weixin.exe`、进程 `22940` 上完成了只读 UIA 探测和一次 Python `capture_once` 调用：

- 精确独立窗口标题为 `2026级计算机研究生通知群`，类名为 `mmui::ChatSingleWindow`。
- 窗口内部 `current_chat_name_label` 再次精确匹配群名。
- 群消息页面内唯一 `AutomationId=chat_message_list`、类名 `mmui::RecyclerListView`、控件类型 `List`。
- 消息行 `Name` 可读取讲座全文和 PDF 文件卡片；取得 3 条与视口相交的行，其中 1 条讲座文本、2 条文件卡片。前一条文件行仅有一小部分位于视口，`fully_visible=false`。
- UIA 的正文为 178 字符，包含 `2026年10月17日(周六) 14:30-16:30` 等文字。
- 消息行的 ControlView 和 RawView 都没有发送者子节点，因此 `sender=null`。独立 `12:55` 标签没有可信日期，因此所有 UIA 消息的 `sent_at=null`。不得把采集时间填作发送时间。

上述记录证明当前窗口可读及一次采集可用，不证明后台、锁屏、微信升级或连续运行 24 小时可用。没有进行微信操作或邮件发送。

## 接口及配置

```python
from wechat_tracker.capture import capture_once, probe

config = {
    "group": "2026级计算机研究生通知群",
    "capture": {
        "source": "windows_uia",
        "process_name": "Weixin",
        "timeout_seconds": 8,
        "max_items": 200,
        # "process_id": 22940,  # 可选；微信重启后 PID 会变化，通常应省略
    },
}
status = probe(config)           # 只有状态、计数和选择器诊断，不含消息正文
messages = capture_once(config) # list[dict]；失败抛出 CaptureError
```

消息字段包括 `id/group/sender/sent_at/text/kind/observed_at`；`source_meta` 附带 UIA 类名、临时 runtime id、完整可见标记和待展开标记。`observed_at` 为真实 UTC 观测时间。正文中的明确活动日期由分析层解释。

PowerShell 独立诊断命令：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\probe-weixin.ps1 -Group '2026级计算机研究生通知群' -Mode probe
```

脚本使用 Windows 自带 .NET UIAutomation，不需要 `pywinauto` 或 `uiautomation`，也不更改系统执行策略。Python 子进程有超时；UIA 卡住会形成明确错误。调用 `probe` 不会返回消息正文；`-Mode capture` 才返回正文，应避免把其原始输出写入公开日志。

## 选择器与覆盖边界

原生窗口必须属于配置的 `Weixin` 进程且标题逐字相同；若不存在或重名则失败。然后依次确认独立窗口类名、内部群名、消息页面和唯一消息列表。仅读取该列表的直接 `ListItem` 子项，并过滤不与列表视口相交的行。不会读取输入框的 TextPattern、联系人列表、其他群消息或聊天侧栏提示。

当前明确支持 `mmui::ChatTextItemView` 和 `mmui::ChatBubbleItemView`。已识别时间分隔行只留作诊断，不作为消息。陌生行类型、空白不可读消息、窗口最小化、群名不一致和选择器变化都会明确失败；需重新验证后才扩展选择器。

微信只暴露当前加载的局部列表。该实现不滚动历史，不承诺捕获窗口未展示期间的所有新消息；锁屏、休眠、进程退出或消息快速滑出视口都可能造成遗漏。持续部署应保持目标独立窗口打开，并把连续采集失败接入单独健康告警。首次启动的历史快照由上层 watcher 建立基线，默认不把现有消息当作新通知。

`id` 是 `群名 + 类型 + 正文 + 同一快照中的相同内容序号` 的 SHA-256 观察指纹，便于重启和重复轮询去重；它不是微信真实消息 ID。Qt `runtime_id` 会回收，不能充当持久 ID。完全相同的消息在不同时刻再次发送，或者相同内容行滚出视口，可能发生身份混淆；需要可靠完整审计时，应接入带真实 ID 的授权导出源。

文件、图片、音视频和合并转发等卡片仅保留可访问的摘要，`pending_expansion=true`。不会自动点击或声称读取到了 PDF 正文、报名名单或嵌套聊天记录。后续可让用户提供已下载附件或结构化转发记录，再交给分析层。

## JSONL 替代入口

UIA 不可读时，使用用户明确提供的目标群导出数据。每行一个 JSON 对象，必须有精确 `group`、稳定 `id` 和非空 `text`；缺失发送者/时间保持 `null`。混入其他群或重复 ID 会拒绝整次导入。

```json
{"id":"export-message-001","group":"2026级计算机研究生通知群","sender":null,"sent_at":null,"text":"这里放目标群通知原文","kind":"text"}
```

```toml
[capture]
source = "jsonl"
jsonl_path = "data/target-group.jsonl"
```

该入口读取文件，不替用户从微信导出或解密数据库。下一步若需要 OCR，应先验证精确目标窗口的固定消息区域、遮挡检测和文字置信度，并保留人工复核；若需要更完整来源，应选能够提供稳定消息 ID、发送者、真实时间和撤回/编辑语义的已授权导出或插件。二者均为后续路线，本版本没有将其当作已经完成的实时采集能力。

## 已打开的合并转发窗口

代码参考核查见 [github-reuse.md](github-reuse.md)。新接口只读取已经打开并由调用者显式绑定的合并转发窗口，不负责点击卡片：

```python
from wechat_tracker.capture import read_forward

config["capture"].update({
    "forward_window_title": "群聊的聊天记录",
    "forward_parent_id": "manual-forward-card-20261015-001",
    # "forward_window_handle": 8915640,  # 可选；每次实际绑定，不能长期硬编码
})
parent = read_forward(config, "manual-forward-card-20261015-001", snapshot=verified_ax_snapshot)
```

传入 `snapshot` 时仅运行纯解析，不访问 UI；省略时由 PowerShell `-Mode forward` 读取同名现有窗口。快照字段为 `window_title`、`process_name`、`window_id` 或 `window_handle`、`list_count=1`、真实 `observed_at` 和 `items=[{"text": "...", "automation_id": "qt_scrollarea_viewport.chat_bubble_item_view"}]`。computer-use 快照注明 `source="computer_use_accessibility"`，允许没有 PID。

`群聊的聊天记录` 这样的泛标题无法独立证明源群。返回父记录 `kind="forward"`，通过显式本地父 ID 绑定源群，并保留 `source_group_verified=false`。子记录含原始可访问文本和末尾完整时间的解析证据；不会把当前观察时间当作原始发送时间。空哨兵、未观察的部分和未下载附件都继续标记不完整，不能声称完整抓取了整份合并记录。
