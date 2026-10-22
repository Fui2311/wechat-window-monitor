# Windows 全天服务与恢复

1.0.0 使用一个常驻 Python 服务和一个常驻 PowerShell UIA 采集器。UIA 事件唤醒读取，默认每 15 秒重新核对目标群；微信登录、精确群独立窗口出现后自动接入，窗口失联则低频等待。原有 `watch` 保留为手动诊断入口。

## 本机已部署

- 配置：仓库根目录 `config.local.toml`（Git 忽略）。
- 数据：`%LOCALAPPDATA%\WeChatLectureTracker`。
- 用户登录任务：`WeChatLectureTracker-08a34879`，以当前交互会话、普通权限运行。
- 已启用发送模式，QQ 凭据按用户要求稍后配置；当前 `email_state=not_configured`，保留邮件预览和待发队列。

微信须在线，精确群名为 `2026级计算机研究生通知群`，打开独立群窗口且不最小化。服务无需随每次微信启动重新操作；群窗口就绪后自动重绑。用户注销或系统关机时无法采集。默认阻止系统空闲休眠，不阻止手动休眠。

## 管理

在项目根目录使用 Python 3.11+：

```powershell
python -m wechat_tracker --config config.local.toml service status
python -m wechat_tracker --config config.local.toml service restart
python -m wechat_tracker --config config.local.toml service stop
python -m wechat_tracker --config config.local.toml service start
```

双击 `scripts/restart-monitor.cmd` 可一键重启。首次安装或更改发送模式：

```powershell
python -m wechat_tracker --config config.local.toml service install --send
# 仅预览则重新安装为不带 --send 的任务
python -m wechat_tracker --config config.local.toml service install
python -m wechat_tracker --config config.local.toml service uninstall
```

安装器会停止旧实例、更新任务再启动。已安装任务的 `start/restart` 沿用安装时的发送模式；命令末尾补 `--send` 不会改写既有任务。stop 禁用自动启动并等待单实例锁释放，start 重新启用；uninstall 删除启动项并停止，保留数据库和加密凭据。

## 恢复与状态

Windows 计划任务同时使用用户登录触发和每分钟检查。`IgnoreNew` 加 OS 单实例锁防止重复运行。异常退出靠后续触发恢复；普通 RestartOnFailure 在本机强制终止场景未生效，因此不能仅凭配置判断恢复有效。

已实测强制结束监控服务：旧采集子进程退出，41.11 秒后新的服务与采集器恢复到 monitoring。显式停止后跨过 66 秒观察仍未重启，随后 start 恢复，活动/队列数量不变。此验证不同于24小时持续可靠性证明。

`service status` 通过本地令牌控制连接核对进程实例，避免复用旧 PID；状态输出隐藏控制令牌。常见字段：

| 字段 | 含义 |
| --- | --- |
| `running` | 当前控制实例可响应 |
| `status=monitoring` | 最近能读取已核对的群列表 |
| `waiting_for_weixin` / `last_error` | 进程、窗口或选择器未就绪 |
| `last_capture_at` | 最近实际采集时刻，UTC |
| `event_bound` | UIA 事件绑定状态；即使未绑定仍定期核对 |
| `tail_position_verified` | 是否有证据确认尾部；本机接口目前返回 false |
| `send_ready` / `email_state` | 是否已经加载可用发送配置 |
| `capture_gaps` | 已记录的断档总数，重启后保留 |

健康文件 `service.json` 和日志 `service.log` 位于数据目录。日志最多 1 MiB × 4，不写群消息正文或邮箱授权码；数据库仍保存本地采集内容。`capture_gaps` 表保留开始、原因和恢复时间。首次等待但此前没有采集基线时不会伪造已知中断时间。

数据库采用 DELETE 回滚日志与 FULL 同步，启动前检查完整性；每小时生成 `tracker.last-good.sqlite3`，校验成功才替换，并保留 `tracker.last-good.previous.sqlite3`。损坏时停止并保留原文件。早期 WAL 数据库曾在本轮检验中出现损坏，已保存证据、恢复源消息并重新验证；修复后再次实际终止服务，19.11秒后恢复，数据完整性与消息/活动/队列数量保持。此前“活动1/队列3”的初步观察不作为数据可靠性最终证据，详见[完整记录](database-recovery.md)。

UIA 读取的是已加载视口，不保证微信未就绪时的历史补齐。尾部跟随在用户空闲时向已核对的目标群渲染后代发送有限滚动，暂存并恢复指针，不抢焦点；用户操作变化即停止。控件未提供 ScrollPattern，因此成功滚动不能证明全历史完整。需要长期占用更少或不移动指针时，可配置 `capture.follow_tail=false`，并自行保持在消息底部。

修复后的120.25秒采样中，进程树内存均值121.73 MiB、峰值145.67 MiB，单核CPU约0.169%，worker重启0次；同期24次数据库完整性检查全部为ok。该短时观测满足当前资源目标（200 MiB、单核1%），仍需更长时间验证实际消息高峰、锁屏和窗口恢复场景。此前30.11秒样本保留作历史参考，不混为全天结果。

## 窗口恢复与24小时观察

真实最小化目标群后，服务约10秒检测为 `waiting_for_weixin/window_not_visible`；恢复窗口后约5秒回到monitoring。服务PID和worker PID未变，新增缺口随后正常结束，数据库完整性ok。此项验证窗口不可见/恢复，未退出微信或操作登录认证。

可选只读验收工具使用本机已有的psutil，不属于监控核心依赖：

```powershell
python scripts/observe-monitor.py --config config.local.toml --output state/soak-new --seconds 86400 --interval 60
```

输出 `summary.json` 和逐次 `samples.jsonl`。它不启动、停止或重启服务，记录当前控制连接、进程身份、采集心跳、缺口、进程树资源和数据库完整性。存在不健康采样、实例改变或缺口增加时不会报告连续观察通过；测试仍继续收集证据直到截止。资源是采样值，新建或已退出子进程的CPU可能漏计，因此不能代替高频性能剖析或消息漏收率测量。

本机已于2026-10-15 15:43（上海时间）启动24小时观察，预计10月16日15:43结束，输出目录 `state/soak-24h-20261015/`。只有进程真实存活且采样时间持续更新才算观察进行中；尚不能写为全天验收通过。观察不检查QQ实际投递，邮箱配置按用户选择延后。
