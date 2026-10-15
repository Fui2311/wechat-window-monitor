# 微信群讲座捕获、分析与邮箱提醒

Mac接续开发请先读 **[HANDOFF.md](HANDOFF.md)**。完整源码、测试和文档在本私密仓库；安装包与开发归档见 [0.4.0交接Release](https://github.com/Fui2311/wechat_tracking/releases/tag/v0.4.0)。

指定群：**2026级计算机研究生通知群**。本项目已实现 Python 本地框架：Windows 微信消息采集、合并转发子消息标准化、讲座信息提取、SQLite 去重与版本、邮件任务队列、活动前提醒、中文邮件预览和 ICS 导出。

源码已连接私有仓库 [Fui2311/wechat_tracking](https://github.com/Fui2311/wechat_tracking)。当前为 `0.4.0`：按手动报名方案，识别与通知只保留**讲座摘要、报名截止时间、讲座活动时间**。摘要从主题及简介/内容摘取；缺失时间标注未注明，部分时间保留时间片段并标待核实。地点、主讲、到场和名单资格不参与排程。原有功能包括长驻采集、窗口就绪自动接入、用户会话服务与崩溃恢复、本机加密 QQ 邮箱配置，以及独立 macOS 源码安装包。运行数据、SMTP凭据和本机配置不进入Git。

日常使用见 [Windows 服务管理](docs/service.md)，Mac 安装及后续实机验收见 [macOS 独立版本](docs/macos.md)。本机服务已部署；QQ 邮箱按用户选择稍后配置，目前只预览和排队。Mac 原生编译与运行待实机验证。

```powershell
python -m wechat_tracker --config config.local.toml service status
python -m wechat_tracker --config config.local.toml service restart
```

也可双击 `scripts/restart-monitor.cmd`。保持微信登录、目标群独立窗口非最小化；登录任务会等待窗口就绪并自动绑定。只采集已加载视口，不能保证锁屏、未加载历史与突发大量消息的完整接收。

## 先看方案和复用结论

- [当前监控、语义识别与提醒机制说明](docs/监控与语义识别及提醒机制说明.md)：包含运行快照、流程图、规则识别与提醒计算样例；另有[Word版](docs/监控与语义识别及提醒机制说明.docx)和[PDF版](output/pdf/监控与语义识别及提醒机制说明.pdf)。
- [方案与模块契约](docs/architecture.md)
- [GitHub 项目源码核查](docs/github-reuse.md)：先检查现有模块，再按本机微信 4.1.15.13 的实际结构选择适配方式。
- [Windows 采集与实测边界](docs/capture.md)
- [自动展开转发：使用方法与验证边界](docs/automatic-forward.md)
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

转发卡片的几行摘要不能代表完整内容。当前实现能读取**已经打开的聊天记录详情窗口**，把内部消息拆成 `children` 后逐条分析；也可按下述方式开启实验性的父卡片自动展开。未展开的嵌套卡片、未下载文件和图片正文保留待补全状态。不会把转发时间套到原始子消息上。

```powershell
python -m wechat_tracker watch --interval 15 --include-existing --expand-forward
```

启动后把**目标群独立窗口置于前台**，保持卡片完整可见并停止鼠标键盘操作至少3秒。程序仅对核对过的群卡片执行单击，读取自己新开的详情，然后关闭该详情并核对返回原群。已有详情窗口、遮挡或用户操作变化会停止展开；默认不抢焦点。每轮最多3张卡片、每张最多5页；没有可靠滚动接口时明确保留不完整状态。完成结果持久缓存，失败退避重试。

0.3.0已在本机微信4.1.15.13通过原生驱动和常驻服务验证自动打开、读取、关闭并返回原群；此次读取1条可访问正文，仍标记历史不完整。其他客户端版本、全历史、附件和嵌套卡片仍需分别验证。详见[自动转发文档](docs/automatic-forward.md)。

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

首次使用复制示例为 `config.local.toml`；已有本机配置时保留原文件。默认 QQ 邮箱 SSL 465，按 [邮箱文档](docs/email.md) 在本机隐藏输入授权码，测试成功后加密保存。不要把授权码粘贴到聊天或写入源码。

```powershell
Copy-Item -LiteralPath config.example.toml -Destination config.local.toml
python -m wechat_tracker --config config.local.toml email setup
python -m wechat_tracker --config config.local.toml service install --send
python -m wechat_tracker --config config.local.toml tick
python -m wechat_tracker --config config.local.toml tick --send
python -m wechat_tracker --config config.local.toml watch --interval 15 --send
```

仅带 `--send` 的命令会实际连接 SMTP。默认在发现时生成三项摘要，再于**讲座开始时间**前 24 小时、60 分钟提醒；报名截止仅展示，不单独建立截止提醒，可通过 `reminder_minutes` 修改。已错过的节点不在新建任务时补发；恢复运行时同一活动的多条积压提醒合并，超过开始时间的临近提醒失效。

```powershell
python -m wechat_tracker outbox
python -m wechat_tracker calendar state/lectures.ics
python -m unittest discover -s tests -v
```

取消/改期关联唯一原事件后提升版本，废弃旧任务；SMTP 失败采用退避重试，超过 `max_attempts` 进入 `dead`。队列状态可通过 `outbox` 查看，故障后修复配置可重新导入/建立新的通知；目前未提供失败任务管理界面。

## 从旧方案升级

0.4.0 会暂停发送旧方案的任务快照。未发信数据库需先停服务，再从保存的原始消息重算；命令会建立校验备份，以事务保留原文、采集指纹及旧任务审计。已发送或发送中记录会阻止自动重算，避免重复投递。本机生产库已完成迁移。

```powershell
python -m wechat_tracker --config config.local.toml service stop
python -m wechat_tracker --config config.local.toml rebuild-analysis
python -m wechat_tracker --config config.local.toml service start --send
```

最后一行保持原来的发送模式；仅预览的实例不加 `--send`。

## 当前边界

- 已实现规则分析；模型分析是架构扩展点，目前不调用外部模型、不上传整群原文。
- 活动时间歧义、主题缺失、未展开转发只生成待核实线索。缺地点、主讲、名单或报名截止不阻断已明确的活动排程。报名由用户手动完成，系统不推断报名成功。
- 目前不自动下载 PDF/XLSX、不识别图片正文、不证明本人是否报名成功；附件名只提供线索。
- 自动逐卡展开已通过本机单卡原生验收；嵌套详情遍历和主群全历史翻页尚未实现。
- SQLite 和 `state/` 含本地消息内容，请保存在你自己的设备。邮件正文固定三项，不另附来源栏目、原文摘录或群文件；来源与证据保留在本地用于审计。
- 全天运行使用 `service install --send`，不要另开 `watch --send` 发送同一数据库。SMTP 已接受邮件但进程来不及记账时，恢复可能重复发送；这是至少一次投递的边界。
- ICS 是手动导出的文件，不是自动同步服务；取消事件保留同 UID 和更新版本，但再次导入的具体更新行为取决于日历应用。
