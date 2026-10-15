# 微信群讲座捕获、分析与邮箱提醒

指定群：**2026级计算机研究生通知群**。本项目已实现 Python 本地框架：Windows 微信消息采集、合并转发子消息标准化、讲座信息提取、SQLite 去重与版本、邮件任务队列、到场前提醒、中文邮件预览和 ICS 导出。

## 先看方案和复用结论

- [方案与模块契约](docs/architecture.md)
- [GitHub 项目源码核查](docs/github-reuse.md)：先检查现有模块，再按本机微信 4.1.15.13 的实际结构选择适配方式。
- [Windows 采集与实测边界](docs/capture.md)
- [邮箱配置](docs/email.md)
- [验证记录](docs/verification.md)

个人微信 4.x 的控件可访问性会随版本和账号变化。当前机器已验证独立群窗口正文可读；合并转发窗口也已通过实际界面读取到内部正文、文件与图片卡片。自动轮询只读目标群的已加载消息，不能保证关机、锁屏、未加载历史或瞬间大量消息的完整接收。

## 快速运行

在本目录使用 Python **3.11+**，核心无第三方依赖。Windows 采集使用系统 PowerShell 和 .NET UIAutomation，无需安装完整机器人框架。

```powershell
python -m wechat_tracker demo
python -m wechat_tracker probe
python -m wechat_tracker watch --once --include-existing
python -m wechat_tracker events
python -m wechat_tracker tick
```

`demo` 使用人工转写的第一张截图和独立 `state/demo.sqlite3`；生成 `state/demo-email-preview.txt`、`state/demo.ics`。截图只有 `12:55`，样例中的消息日期是为回放设定的 2026-10-15；真实采集不会这样补日期。`tick` 只预览到期邮件，**不会把任务标为已发**。重复预览相同内容是预期行为。

实时采集需要在微信中将目标群打开为独立窗口并保持非最小化。首次 `watch` 默认只建立现有可见消息的基线；`--include-existing` 用于第一次也分析这些消息。之后捕获到的新内容进入分析流程。

持续预览运行：

```powershell
python -m wechat_tracker watch --interval 15
```

## 合并转发记录检查

转发卡片的几行摘要不能代表完整内容。当前实现能读取**已经打开的聊天记录详情窗口**，把内部消息拆成 `children` 后逐条分析；未展开的嵌套卡片、未下载文件和图片正文保留待补全状态。命令和配置见 [采集文档](docs/capture.md)。不会把转发时间套到原始子消息上。

已打开详情后的只读检查（`--parent-id` 表示你明确绑定到目标群的本地来源 ID）：

```powershell
python -m wechat_tracker forward --parent-id my-forward-card-001 --save state/forward.jsonl
```

本次真实读取的数据也可重复回放：

```powershell
python -m wechat_tracker --db state/forward-live.sqlite3 forward --parent-id manual-forward-card-20261015-001 --snapshot state/forward-live-snapshot.json
```

检查结果见 [真实转发记录报告](docs/forward-inspection.md)，邮箱摘要在 `state/forward-email-preview.txt`。原生读取器的新增详情模式已做语法和模拟测试；本次真实窗口通过 computer-use 读取，再验证相同标准化与分析流程。

标准 JSONL 可直接导入，不依赖桌面控件：

```powershell
python -m wechat_tracker --db state/forward-demo.sqlite3 ingest examples/forward.jsonl --now 2026-10-15T13:00:00+08:00
python -m wechat_tracker --db state/forward-demo.sqlite3 events
```

`examples/forward.jsonl` 是合成测试样例。实际读取结果保存在 `state/`，不混入源码示例。

## 开启邮箱发送

复制配置后修改 SMTP 主机、端口和加密方式；运行前按 [邮箱文档](docs/email.md) 在本机设置发件账号、SMTP 专用授权码、发件人和收件人环境变量。不要把授权码粘贴到聊天或写入源码。

```powershell
Copy-Item -LiteralPath config.example.toml -Destination config.local.toml
python -m wechat_tracker --config config.local.toml tick
python -m wechat_tracker --config config.local.toml tick --send
python -m wechat_tracker --config config.local.toml watch --interval 15 --send
```

仅带 `--send` 的命令会实际连接 SMTP。默认在发现时生成摘要，再于**要求到场时间**前 24 小时、60 分钟提醒；没有到场要求时以开始时间为准，可通过 `reminder_minutes` 修改。已错过的节点不在新建任务时补发；恢复运行时同一活动的多条积压提醒合并，超过到场时间的临近提醒失效。

```powershell
python -m wechat_tracker outbox
python -m wechat_tracker calendar state/lectures.ics
python -m unittest discover -s tests -v
```

取消/改期关联唯一原事件后提升版本，废弃旧任务；SMTP 失败采用退避重试，超过 `max_attempts` 进入 `dead`。队列状态可通过 `outbox` 查看，故障后修复配置可重新导入/建立新的通知；目前未提供失败任务管理界面。

## 当前边界

- 已实现规则分析；模型分析是架构扩展点，目前不调用外部模型、不上传整群原文。
- 时间歧义、缺地点、未展开转发只生成待核实线索。**个人报名资格未知**与**日程时间未知**分开：前者仍可提醒确定的讲座安排，但邮件会要求核对名单。
- 目前不自动下载 PDF/XLSX、不识别图片正文、不证明本人是否报名成功；附件名只提供线索。
- 实际合并转发窗口读取属于“用户打开详情后检查”；自动定位并逐卡展开、嵌套详情遍历与全历史翻页尚未实现。
- SQLite 和 `state/` 含本地消息内容，请保存在你自己的设备。邮件仅发送讲座摘要与最多1000字符来源摘录，不附带群文件。
- 建议单个进程运行 `watch --send`。SMTP 已接受邮件但进程来不及记账时，恢复可能重复发送；这是至少一次投递的边界。
- ICS 是手动导出的文件，不是自动同步服务；取消事件保留同 UID 和更新版本，但再次导入的具体更新行为取决于日历应用。
