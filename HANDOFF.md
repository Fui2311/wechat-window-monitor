# Mac 接续开发交接

更新日期：2026-10-15（Asia/Shanghai）  
项目版本：0.4.0  
私密仓库：https://github.com/Fui2311/wechat_tracking  
主分支：`main`；本轮交接前的功能基线：`d234774`。

## 1. 接手目标与已经确定的需求

在 Mac 本地继续开发、编译并验证独立微信监控版本。Windows 已有运行实例，Mac 不依赖它在线。先验证现有实现，依据真实 Accessibility 结构修复适配，不需要重建整套框架。

最新需求以本节和当前代码为准；`findings.md`、`progress.md` 与部分旧验证文档包含历史方案，不能据此恢复已删除的到场/名单逻辑。

- 精确目标群：**2026级计算机研究生通知群**。
- 群内讲座需要用户手动报名。识别和通知仅三项：**讲座消息摘要、报名截止时间、讲座活动时间**。
- 摘要为本地规则抽取：主题加简介/内容的最多两句；没有正文内容时只呈现已知主题，不编造介绍。当前没有 LLM/API 或语义向量依赖。
- 不操作报名、不推断报名成功；不再提取地点、主讲、到场、签到或名单资格作为业务字段、邮件栏目或排程条件。
- 发现时生成三项通知；保留活动**开始时间前24小时、1小时**提醒。报名截止**仅展示**，当前没有截止前定时任务。
- 缺失时间明确显示“未注明”；有片段但不能确定则保留时间片段并标“待核实”。不补00:00、23:59、年份或相对日期基准。原始发送时间未知时不能使用采集时间代替。
- 邮箱服务为 QQ。用户明确选择“稍后配置，先继续开发”，不要再次索要授权码；将来由用户在 Mac 本机隐藏输入并保存至 Keychain。当前未完成真实投递验收。
- 全天运行需专用已登录会话、微信在线及目标群窗口就绪。用户希望微信登录且窗口打开时自动识别并接入。
- Mac 实机验证此前按用户要求延期；这次交接准备好后，待 Mac 就绪再实测，不把 Windows/模拟结果写成 Mac 已通过。

## 2. 当前完成度与证据边界

| 项目 | 当前结论 |
| --- | --- |
| GitHub | 已核实为 PRIVATE；所有源码、测试、文档和开发脚本通过 main 交付 |
| 共享 Python 业务逻辑 | 150项自动测试已在 Windows 通过；离线回放去重、三项通知、开始时间锚点已验证 |
| Windows 服务 | 已部署0.4.0并恢复monitoring，目标群视口可读；QQ未配置、实际发送0 |
| 旧策略迁移 | 已备份后从8条消息重算5条活动/线索；旧9任务superseded、新9任务pending，原文与采集指纹保留 |
| Mac 采集 | Swift AX helper、Python适配、安装器、Keychain接口、LaunchAgent与管理命令已实现；尚无 Mac 原生编译/微信实测结果 |
| Mac 安装包 | 独立源码安装ZIP，含SHA256完整文件清单；需要在Mac本机编译，不是已签发/公证的DMG |
| 全天验证 | 未完成。15:43开始的Windows观察因16:31计划上线发生实例变化，不能作为新版本连续24小时通过证据 |
| CI | 只有 `docs/ci/macos-validation.yml` 草案，未启用、未运行；现有GitHub OAuth此前缺workflow权限 |

可共享的本轮核验结果见 [docs/handoff-validation.json](docs/handoff-validation.json)。机器内原始抓取、日志、备份和完整故障证据仍留在 Windows 本地；本仓库没有生产聊天数据库或账户凭据。

## 3. 从哪里阅读与修改

建议顺序：本文件 → [Mac安装与验收](docs/macos.md) → [架构](docs/architecture.md) → [监控与提醒机制](docs/监控与语义识别及提醒机制说明.md) → 对应源码与测试。

| 路径 | 职责 / 接手重点 |
| --- | --- |
| `wechat_tracker/resources/mac-capture.swift` | 原生AX窗口/群绑定、消息行读取、事件唤醒、可选转发展开；Mac首先编译及核查的文件 |
| `wechat_tracker/macos.py` | helper协议、群来源校验、标准化消息、父卡片绑定及转发子消息 |
| `wechat_tracker/capture.py`、`watcher.py` | 平台路由、转发补全、缓存、退避与采集去重 |
| `wechat_tracker/analyzer.py` | 三项内容提取，内部策略标识 `manual-signup-v1`；保留来源/时间证据 |
| `wechat_tracker/engine.py` | 活动关联、版本、outbox、开始前提醒、重试及旧策略离线重算 |
| `wechat_tracker/notify.py` | 固定三项邮件正文，QQ SMTP的TLS发送 |
| `wechat_tracker/store.py` | SQLite完整性检查、DELETE/FULL、校验备份 |
| `wechat_tracker/service.py`、`autostart.py` | 单实例服务、心跳/缺口、macOS LaunchAgent与caffeinate |
| `wechat_tracker/credentials.py` | Windows DPAPI与Mac Keychain；不要跨平台复制凭据 |
| `mac/install.py`、`mac/*.command` | 本机编译、App身份/签名、安装、授权、管理与恢复入口 |
| `mac/verify.py`、`mac/verify-package.py` | 离线业务回放、安装包校验、原生probe及短时心跳观察 |
| `tests/` | 150项基线；Mac相关契约在 `test_macos.py`，其余涵盖服务/凭据/队列/采集等 |
| `scripts/build-mac-package.py` | 从白名单构建不含Windows运行时和本机数据的Mac源码安装包 |
| `docs/`、`output/pdf/` | 运行说明、历史实测边界、Word/PDF机制说明 |

## 4. Mac 首次接手：先跑离线基线

使用 Python 3.11+。Apple Silicon 必须使用原生 arm64 Python，不能复制 Windows Anaconda/虚拟环境，也不要用 Rosetta Python 安装。本项目核心无第三方Python依赖。

```bash
gh auth login
gh repo clone Fui2311/wechat_tracking
cd wechat_tracking
git status --short
git log -1 --oneline
sw_vers
uname -m
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m unittest discover -s tests -q
python mac/verify.py --config mac/config.example.toml --offline
```

已登录GitHub时跳过auth login。没有GitHub CLI时，可用已授权的Git客户端克隆私密仓库。不要把token粘贴进远端URL、命令或文档。

Mac运行测试时，Windows DPAPI等平台专属用例可跳过；记录实际通过/跳过/失败数量，不能把Windows的150项原样当作Mac结果。离线验收应得到1活动、3任务、重复导入不新增、`smtp_sent=false`；这不证明AX能读取微信。

## 5. 构建并安装 Mac 版

**不要直接从Git克隆根目录运行 `bash mac/install.command`。** 安装器要求ZIP内的 `SHA256SUMS.txt` 与完整清单；Git开发树没有该清单。

先安装 Apple Command Line Tools；已有时无需重复安装：

```bash
xcode-select -p
# 仅在缺少工具时运行：
xcode-select --install
```

在仓库根目录构建、解压、校验、安装：

```bash
python scripts/build-mac-package.py
PACKAGE_DIR=$(mktemp -d -t wechat-tracker-install)
unzip -q dist/WeChatLectureTracker-macOS-0.4.0.zip -d "$PACKAGE_DIR"
cd "$PACKAGE_DIR/WeChatLectureTracker-macOS-0.4.0"
python mac/verify-package.py .
bash mac/install.command
```

也可从私密仓库的 [0.4.0交接Release](https://github.com/Fui2311/wechat_tracking/releases/tag/v0.4.0) 下载现成源码安装ZIP及对应 `.zip.sha256`，在下载目录执行 `shasum -a 256 -c WeChatLectureTracker-macOS-0.4.0.zip.sha256`。下载全部Release资产时，可用总清单 `shasum -a 256 -c SHA256SUMS.txt` 一次校验。开发请保留Git克隆，安装包是运行交付物。

安装器会先编译Swift，再变更已有安装；保存旧App和旧Python包备份，保留已有配置/数据/凭据。编译失败应停在错误处修复，不手动覆盖可用旧安装。安装不自动启服务、不发送邮件。

安装位置：

- 本地程序/配置：`~/Library/Application Support/WeChatLectureTracker/`
- 配置文件：该目录下 `config.toml`；原生helper路径由安装器填入。
- 独立数据：该目录下 `state/`，新Mac首次安装从空库开始。
- 原生App：`~/Applications/WeChat Lecture Tracker Capture.app`。
- 登录项：`~/Library/LaunchAgents/local.wechat-lecture-tracker.<群名摘要>.plist`。

注意：安装器复制一份代码到运行环境。修改Git克隆后，应重新构建并安装，不能认为编辑源码会自动更新已安装服务。

## 6. Mac 实机开发与验收清单

### A. 原生编译、授权与精确群绑定

```bash
MANAGE="$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command"
bash "$MANAGE" verify --offline
bash "$MANAGE" authorize
```

在系统设置的辅助功能中允许 **WeChat Lecture Tracker Capture.app**，必要时允许实际启动的终端。登录微信，将精确目标群打开为独立、非最小化窗口，再执行：

```bash
bash "$MANAGE" verify
```

以 `native_probe.ok=true` 及真实群窗口正文读取作为接入证据。self-test只证明原生程序可运行，不证明授权、群绑定或消息完整性。

若失败，按实际进程Bundle ID和AX树检查：默认 `com.tencent.xinWeChat`、窗口标题、消息列表角色、行角色与可见性。`mac_header_identifier`、`mac_list_identifier` 默认空；只有人工核实真实控件含义后才填写。不要把Windows控件ID照搬到Mac，也不要放宽成“读取任意窗口/同名联系人”。

### B. 先以预览模式检查常驻服务

```bash
bash "$MANAGE" install
bash "$MANAGE" status
bash "$MANAGE" verify --observe-seconds 60
bash "$MANAGE" events
bash "$MANAGE" outbox
bash "$MANAGE" restart
```

首次用不带 `--send` 的install。验证打开/关闭目标窗口、微信退出再登录、最小化/恢复和服务异常退出后的重接入。确认重启不重复生成活动或任务，不能凭进程在线就认定消息正常采集。

### C. 转发与恢复能力

Mac自动展开默认关闭。普通捕获和群绑定可靠后，再在可控的目标卡片上测试展开、父消息校验、唯一新详情、读取、关闭及返回原群。原始发送者/发送时间没有证据时仍为未知，不继承父转发时间。先测单张，再考虑嵌套和历史补齐；不要宣称全历史覆盖。

### D. 邮箱与全天验收

只有用户准备配置时才在Mac本机执行 `bash "$MANAGE" email setup`；此步骤会发送测试邮件，成功才保存Keychain。正式发送模式通过 `bash "$MANAGE" install --send` 开启。用户此前选择稍后配置，不将凭据配置作为继续开发的前置条件。

修复/部署稳定后再开始新的24小时观察。可用 `verify --observe-seconds 86400` 检查连续实例和采集心跳；CPU/内存与缺口记录需另作观测。进程树目标为平均单核CPU低于1%、内存低于200MiB，报告实际结果；心跳检查不证明消息零漏收。原Windows已受计划上线影响的观察不能作为Mac验收。

交接后的最终验收至少需要：编译/签名、精确群可读、窗口/微信恢复、LaunchAgent恢复、重复抑制、三项通知和改期取消、资源占用、独立24小时观察；QQ实际送达在用户配置后补验。

## 7. 数据与旧库处理

本次交接迁移开发项目，Mac独立运行，不搬Windows生产库、采集缓存、服务控制文件、PID/窗口句柄、计划任务XML或DPAPI凭据。本地配置用 `mac/config.example.toml` 重建，不能复制Windows配置路径。

SQLite曾发生损坏，当前已使用DELETE回滚日志、FULL同步、启动完整性检查及小时级校验备份。不要为“优化”恢复旧WAL设置；调查记录见 [数据库恢复说明](docs/database-recovery.md)。

如果以后明确需要导入某份旧业务库，先停止其服务、做校验备份，再单独设计迁移。已有已发送或发送中的任务禁止自动重放。0.4.0的 `rebuild-analysis` 只用于尚未发信库，普通ingest会被去重跳过，不能用于重算旧事件。升级命令使用安装环境的Python和配置，先保留原发送模式；原本仅预览的实例不要擅自改为发送。

## 8. GitHub交付范围与已知限制

Git `main` 保存完整开发树：Python/Swift源码、Windows与Mac平台脚本、全部测试、样例、配置模板、交接/运行文档及Word/PDF。Release提供Mac源码安装ZIP、Python wheel、完整开发树ZIP及SHA256清单；源代码历史保留在Git。

`.gitignore` 排除的本机配置、授权码/密钥、聊天数据库/原始抓取、普通日志、备份、虚拟环境、编译缓存与旧构建中间物不上传。即使仓库私密，也不把机器身份和凭据当作项目依赖。完整开发ZIP来自Git提交，Mac安装ZIP来自白名单构建，两者用途不同。

当前已知限制：AX选择器仍待Mac实测；锁屏/休眠/未加载历史/突发大量消息有覆盖边界；附件/OCR及链接正文未实现；SMTP采用至少一次投递，中断恢复可能重复；ICS为手工导出，不是日历同步服务。

CI草案仍保存在 `docs/ci/`，此前GitHub拒绝写入工作流目录是OAuth缺workflow scope。未获得相应授权时不扩权、不绕过；可先完成Mac本地验证。

## 9. 给下一位开发助手的起始提示

```text
请在这个Mac上的wechat_tracking仓库接续开发。先读取HANDOFF.md、docs/macos.md、当前Git状态和实际Mac/Python/微信环境，再运行离线基线并编译现有Swift AX helper，依据真实Accessibility结构适配目标群。通知固定为讲座摘要、报名截止、活动时间三项；报名手动完成，提醒以活动开始前24小时/1小时为准，截止仅展示。QQ邮箱配置由用户稍后在本机完成，不索取聊天中的授权码。Windows已有独立服务，不操作其生产数据；Mac使用独立配置、Keychain和数据目录。区分源码开发树与带SHA256清单的安装包，修改后重建安装再验证。请先完成可执行的Mac编译/安装/群绑定和恢复验证，记录真实结果与剩余事项，不把模拟、静态检查或Windows结果写成Mac已通过。
```
