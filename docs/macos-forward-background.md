# Mac 转发卡片自动读取实验

日期：2026-10-16，Asia/Shanghai。环境：Apple Silicon、macOS 26.6、WeChat 4.1.13。

目标是用户把合并转发卡片发到“邮件测试”群后，程序自行取得子消息正文。用户无需打开卡片。已经跑通并接入项目的路线会在输入空闲时短暂切换到微信；完全不改变前台应用的读取仍未跑通。

日常操作请先看 [快速启动第4节](快速启动.md)，包含本机现成配置、单次检查、持续等待新卡片、结果查看、停止和关闭自动打开的方法。本页保留实验依据、接入协议和其他 Mac 的配置示例。

## 实测结果

| 路线 | 本机观察 | 结论 |
| --- | --- | --- |
| 被动读取群消息 Accessibility | 新卡片可检测；只提供80字预览 | 能发现卡片，不能把预览当作正文 |
| 后台 AXPress | 动作列表有 AXPress，调用返回成功，但没有新详情窗口 | 不通过；动作返回码不能作为读取成功证据 |
| 后台定向进程事件 | CGEvent / NSEvent 定向投递后未观察到详情；鼠标位置未变 | 在本机测试条件下未跑通 |
| 普通 activate | 后台调用没有把微信切到前台 | 不通过 |
| LaunchServices 激活后自动点击 | 自动打开本次父卡片的新详情，读到1条正文，关闭详情，恢复应用与鼠标 | 已跑通；会使用前台，属于无人值守 UI 自动化 |
| 本地数据库 | 消息库不是明文 SQLite；限定 Message 缓存的标题检索无命中 | 未读取数据库正文，不宣称此路线已成功 |

测试卡片为“经典计算机与量子计算机上的多体谱学”。原生读取文本722字，去掉末尾原始消息时间后正文704字；不是80字卡片预览的重述。识别到活动时间 `2026-10-16 10:30 +08:00`、报名截止 `2026-10-15 21:00 +08:00`。测试时活动已开始，识别结果不代表仍可报名，也不代表用户入选或参加。

原生实验和接入后的 `watch --once` 使用实际窗口，不是截图转写或模拟 UI 返回值。接入测试库先用转发前4条消息建立基线，再处理新卡片：1条消息、1个活动、1个 new 待发任务；重复执行没有新导入，也没有再次打开详情。SQLite 完整性为 `ok`，没有连接 SMTP。

接入版另做了一轮微信起初在后台的完整验收：原前台应用 PID 为15909，微信 PID 为706；程序自行激活、读到正文、关闭详情后，前台 PID 恢复为15909，鼠标位置恢复。该轮原生交互总耗时约1.02秒，只代表本次样本。无需用户打开卡片，仍存在一次短暂的应用切换。

## 接入方式

原生 `expand-activated` 已由 `wechat_tracker.macos.expand_forward()` 接入既有 `watcher.enrich_forwards()`，因此 `watch` 与常驻服务共用来源校验、部分正文缓存、重试退避、分析器、SQLite 去重与通知预览。无需另建一套解析规则。

在专用测试配置中启用：

```toml
group = "邮件测试"
timezone = "Asia/Shanghai"
reminder_minutes = []

[capture]
source = "macos_ax"
# mac_helper 必须指向从当前源码重新编译的原生程序。
mac_helper = "/absolute/path/to/wechat-capture"
mac_header_identifier = ""
mac_list_identifier = "chat_message_list"
auto_expand_forward = true
mac_forward_interaction = "activate_click"
mac_forward_card_side = "right"
forward_idle_seconds = 3
forward_notice_seconds = 5
forward_postpone_seconds = 300
forward_max_cards = 1
forward_partial_retry_seconds = 600
forward_timeout_seconds = 25
```

`mac_forward_interaction` 默认仍为 `ax_press`，`auto_expand_forward` 默认仍为 `false`。本轮只在独立测试配置启用新模式。正式通知群配置和已安装服务未自动切换。

`mac_forward_card_side = "right"` 对应本轮自发转发卡片。`left` 提供另一侧气泡的定位选项，但本轮没有左侧真实卡片验收。它不是自动推断发送者的功能。只在当前已验证的布局中启用；窗口位置由实时几何获取，不固定桌面坐标。

```bash
.venv/bin/python -m wechat_tracker --config /absolute/path/to/test.local.toml watch --once
```

首次运行默认只建立已有消息基线；以后新出现的卡片才自动处理。需要主动检查基线中的旧卡片时，显式增加 `--include-existing`。上面的命令不带 `--send`，只产生本地预览。

`--include-existing` 不会跳过已经保存的正文缓存或失败退避。Mac 已取得子正文后复用缓存，不因 `history_complete=false` 定时重开；`partial` 与不完整原因继续保留。Windows 的部分读取仍按 `forward_partial_retry_seconds` 补读。重复执行没有新动作并不代表采集停止。持续运行使用 `watch --interval 15`，按 Control+C 停止。修改配置后需停止并重新启动该命令。

## 每次操作的证据与退出条件

1. 重新核对精确独立群窗口、微信 PID、父卡片行号、卡片标识和全文 SHA256。主窗口同名联系人不作为自动点击的来源证明。
2. 全局输入至少空闲3秒才显示不抢焦点的5秒预告；点“稍后”或预告中出现键鼠输入，会暂停整个群的自动展开5分钟，重启仍保留。预告完成且无人接管才使用 LaunchServices 激活微信，重新核对群窗口与卡片。
3. 点击点必须位于卡片行与消息视口的交集，且系统窗口列表证明该点上层确实是本次群窗口。只发出一次鼠标按下和抬起，不输入、粘贴或发送消息。
4. 只有本次点击后新增且标题精确匹配的唯一详情窗口，才允许读取。既有详情、群/父卡片变化、遮挡、用户切换应用或输入变化会中止。
5. 读取唯一消息列表后，只关闭本次新开的详情，确认其消失并重新绑定原群。随后恢复原前台应用并核对 PID；鼠标位置也需实测恢复。
6. Python 层只有在父卡片绑定、新详情、返回群窗口、前台和鼠标恢复的证据全部通过后，才接受正文为 `source_group_verified=true`。

返回绑定明确记录 `interaction_method="activate_click"`、`activation_requested=true`、`global_input_used=true`、`notice_completed=true`、`notice_seconds`、`foreground_restored` 和 `cursor_restored`。Python 层会验证预告完成证据，不会把这条路径标记为无界面操作的后台读取。用户接管时优先停止并延后整个群5分钟；可能留下详情窗口供检查，不强行关闭用户正在使用的窗口。

## 覆盖范围

当前读取的是详情内已加载的 Accessibility 子消息。`history_complete=false`、`incomplete=true` 继续保留，缓存状态为 `partial`。本轮未自动遍历长记录、嵌套卡片或读取未下载附件、图片正文；没有长时稳定性或零漏收证明。

原卡片的转发时间不替代子消息时间。只有子消息末尾完整可访问日期时间才作为原始发送时间的证据。手工打开详情的旧接口继续使用显式本地父 ID，并保留来源未独立验证的标记。

## 代码参考与其他方案

本次没有安装或导入第三方微信框架。原生实现使用系统 AppKit、Accessibility 和 Core Graphics，Python 业务层仍使用标准库。参考了 [Dukou 的激活与交互实现](https://github.com/qzz0518/Dukou/blob/28f38d7ebb0f107d7bc8cc6ef367753c44250bef/Sources/DukouApp/WeChat/WeChatAccessibility.swift) 中的 LaunchServices 适配事实，按本项目来源绑定、恢复和通知协议独立实现。Dukou 本身采用 MIT 许可。

完全不切前台的后续方向是本地数据库读取。[GreenBubbles 的格式说明](https://github.com/bojieli/greenbubbles/blob/69f19c7089d7ef011e9ba43d44c6fb4e6e5be98b/docs/WECHAT_DATABASE_FORMAT.md) 描述了合并记录的 XML 和显式关系；这不证明本机当前卡片的全部正文已经落库。其[密钥初始化流程](https://github.com/bojieli/greenbubbles/blob/69f19c7089d7ef011e9ba43d44c6fb4e6e5be98b/docs/PASSPHRASE_ACQUISITION.md) 需要修改微信签名、调试权限和重新登录，属于另一个需明确授权的实验。本轮没有执行这些步骤，也没有获取数据库密钥。

## 本地证据

原始快照、原型源码、原生运行结果、独立测试配置和接入验收保存在 Git 忽略目录 `state/forward-background-20261016/`，不提交实际群消息。

- `baseline.json` / `after-forward.json`：转发前后快照。
- `standalone-actions.json`：父卡片动作列表与80字预览长度。
- `standalone-axpress-result.json` / `standalone-pid-right-result.jsonl` / `standalone-nsevent-result.jsonl`：后台尝试的实际失败结果。
- `foreground-global-safe-result.jsonl`：首次自动读取的原型结果。该早期原型沿用了错误的 `global_input_used=false` 标签，且在恢复焦点前计算 `foreground_pid_after`；它只用作原始实验记录，最终行为以接入版证据为准。接入版已修正两个字段的测量和校验。
- `integrated-e2e/validation.json`：接入既有 CLI、分析、缓存和去重后的验收；该轮开始时微信本来就在前台。
- `integrated-background-e2e/validation.json`：接入版从微信后台开始的完整流程；前后应用 PID 一致，鼠标恢复，正文识别及重复导入检查通过。

自动测试：168项运行，166项通过，2项 Windows DPAPI 专用测试跳过；Swift 原生编译成功。新增用例覆盖前台/鼠标恢复证明、失效父卡片、主窗口拒绝点击、默认模式、部分正文缓存及分析去重。

原官方微信程序未修改；本机数据库、邮箱凭据和正式通知队列也未因实验改写。正式服务的实时状态应单独检查，测试通过不代表已部署到该服务。
