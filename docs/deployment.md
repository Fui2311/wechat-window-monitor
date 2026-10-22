# 部署、升级与发布（1.1.2）

## 部署选择

| 场景 | 安装方式 | 需要 |
| --- | --- | --- |
| macOS 日常采集 | Release 的 Mac 源码安装 ZIP | Python 3.11+、Apple Command Line Tools、微信登录及辅助功能授权 |
| Windows 日常采集 | 仓库源码或 wheel + 当前用户登录任务 | Python 3.11+、PowerShell/.NET UIAutomation、Windows 微信交互式桌面 |
| 开发/离线回放 | 源码 `pip install -e '.[dev]'` 或 wheel | Python 3.11+；无需微信、Swift、SMTP 或模型密钥 |

Mac 安装 ZIP、Python wheel 和源码 sdist 都不是预装聊天数据的运行环境。wheel 包含所有跨平台资源，但不代替 Mac 原生辅助程序的本机编译与权限授权。

## 显式配置与数据目录

使用 `wechat-track init config.local.toml --platform windows --group "精确群名"` 创建 Windows 配置；`--platform macos` 生成指向默认安装辅助程序的 Mac 配置，`--platform jsonl` 用包内样例离线输入。已有文件拒绝覆盖。

`data_dir` 相对于配置文件目录解析。不同测试/群应使用独立配置与数据目录；只改群名而复用原库会混淆来源。GUI 切群流程会建立群目录和标识并保留旧历史。

所有日常命令建议显式传 `--config`。不传时依次寻找：Mac Application Support 已安装配置、当前目录 `config.local.toml`、当前目录 `config.toml`、当前目录示例、仓库示例。故在 Mac 源码目录运行时，默认配置也可能来自已安装版本。

## macOS 安装和使用

1. 下载并核对 Release 提供的 ZIP 外部 SHA256，再解压。安装原生 Python 3.11+；Apple Silicon 使用 arm64 Python，Intel 使用 x86_64。运行 `xcode-select --install` 安装编译工具。
2. 在解压目录执行 `python3 mac/verify-package.py .`，检查所有文件的完整性，再运行 `bash mac/install.command`。安装器会重复检查，编译 Swift、进行本机 ad-hoc 签名并复制 Python 模块。
3. 编辑 `~/Library/Application Support/WeChatLectureTracker/config.toml` 的 `group`。执行下列命令打开采集 App；在“系统设置 → 隐私与安全性 → 辅助功能”允许该 App。按实际系统提示授权启动终端。

```bash
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" authorize
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" verify --offline
```

4. 登录微信，将目标群打开为独立、非最小化窗口；探针必须核对精确群名、进程和唯一消息列表。就绪后再安装当前用户登录预览服务：

```bash
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" probe
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" install
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" status
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" web --open
```

Python、配置、状态、管理脚本位于 `~/Library/Application Support/WeChatLectureTracker/`。采集 App 位于 `~/Applications/WeChat Lecture Tracker Capture.app`；凭据 App 独立存放，避免只更新采集器就重建 Keychain 身份。后台任务位于 `~/Library/LaunchAgents/local.wechat-lecture-tracker.<群名摘要>.plist`，随当前用户登录会话运行。

停止用 `manage.command stop`，恢复用 `start`，重启用 `restart`；`uninstall` 移除登录任务并停止监控，保留配置、数据库和凭据。关闭浏览器或 HTTP 控制台不停止独立监控。

源码开发者使用 `python scripts/build-mac-package.py` 生成带清单 ZIP，再解压安装；不要在无 `SHA256SUMS.txt` 的开发目录直接运行安装器。编译器/SDK 不匹配时先核对 `xcrun swiftc --version`、`xcrun --show-sdk-path` 和已选 Command Line Tools；更正工具链后重试。安装器编译失败不会停止已有采集服务。

## Windows 安装和使用

在 PowerShell 中：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m wechat_tracker init config.local.toml --platform windows --group "你的精确群名"
.\.venv\Scripts\python.exe -m wechat_tracker --config config.local.toml probe
.\.venv\Scripts\python.exe -m wechat_tracker --config config.local.toml watch --once --include-existing
.\.venv\Scripts\python.exe -m wechat_tracker --config config.local.toml service install
.\.venv\Scripts\python.exe -m wechat_tracker --config config.local.toml service status
.\.venv\Scripts\python.exe -m wechat_tracker --config config.local.toml web --open
```

从 wheel 安装时将 `pip install .` 换为 `pip install 路径\wechat_lecture_tracker-1.1.2-py3-none-any.whl`。当前用户登录任务要使用创建时的 Python 路径；不要安装后移动或删除虚拟环境。需保持微信在线及目标独立群窗口可读，注销/锁屏不能视作可靠采集环境。更多细节见 [服务说明](service.md)。

## 邮箱和 LLM

默认只预览。`email setup` 实际发送一封测试邮件，成功后保存本机凭据。日常服务用 `service install --send` 启用真实投递；恢复预览可用控制台或重新 `service install`，不加 `--send`。`tick --send` 是显式的一次真实投递，不应与后台发信服务并发操作同一库。

LLM 通过控制台设置 `processing_mode=llm`、接口地址和模型；密钥保存在配置旁独立的 `.llm-key` 文件或所指定环境变量中，不进入 TOML/Git。远程接口必须 HTTPS，本机兼容服务可用 HTTP。模型测试会向该接口发送输入的测试文字，结果不入库、不发信；常驻模式仅处理新入库消息，不因切换模型自动重算所有历史。接口失败使用本地规则，原文保留。详见 [模型处理](llm-processing.md)。

## 从 0.4.0 升级与回滚

1. 记录当前配置、数据目录、Python/辅助程序路径、服务运行状态及发送模式。用原入口执行 `service stop` 或 Mac `manage.command stop`，确认实例已停止。
2. 保留当前程序副本；在服务停止时复制整个数据目录和配置到只有当前用户可读的备份目录。凭据仍留在原本机安全存储；不得上传备份。
3. Mac 运行新 ZIP 安装器；它会保留旧 App 和旧 Python 包的时间戳备份，并保留已有配置。Windows 在既有虚拟环境安装新 wheel，或保留旧环境并为新环境重新安装登录任务。
4. 先执行离线验证，再用探针检查新程序的当前窗口读取；用预览模式启动并核对原文、通知和队列，然后恢复此前明确选择的发送模式。

本次模块拆分不改变 SQLite 表格式。已有 `lecture-notice-v1` 数据库无需重算。更早策略的未发信库若需要重算，须停止服务后运行：

```bash
wechat-track --config config.local.toml rebuild-analysis
```

命令创建并校验备份；有已发送或发送中任务时拒绝自动重算，避免不受控重复通知。重算使用当前本地规则，保持原始消息、指纹和旧任务审计；不按当前模型对历史重新调用接口。

回滚时先停止新服务，恢复先前程序/环境和对应启动入口。保留升级后的数据库副本；若仅回退代码，数据库格式仍兼容，但处理策略和新功能可能不同，应先以预览检查。不要将升级前旧数据库直接覆盖到已发生真实投递的库，否则可能再次发送旧任务。损坏库处理见 [数据库恢复](database-recovery.md)。

## 验收和排错

| 现象 | 检查 |
| --- | --- |
| 有心跳但没有新消息 | 同时核对 `last_capture_at`、`status`、`last_error` 和窗口可读性；循环心跳不代表采集成功 |
| `target_window_not_ready` | 微信登录、精确群名、独立窗口、最小化/锁屏状态 |
| `message_list_not_unique` | 按当前微信版本检查 AX/UIA 结构，显式配置列表标识；不要放宽到任意列表 |
| 控制台仍显示旧版本行为 | 核对进程使用的 Python、模块路径和配置；源码修改不会自动替换已安装副本 |
| 通知存在但没收到邮件 | 核对预览/发信模式、凭据授权、队列状态和退避时间；SMTP 接受不等于进入收件箱 |
| 端口被占用 | 保留已有控制台或使用 `web --port 8089`；不要杀死所有 Python 进程 |
| 转发只有摘要 | 查看是否实际展开、父卡片来源绑定和 `children`；摘要/附件名不能当正文 |

开发发布请使用完整 Git 仓库。修改 `pyproject.toml` 的稳定版本号后，运行 `python scripts/sync-version.py --write`，审查差异，并先执行 `python scripts/sync-version.py --check`；该工具同步当前组件和文档标签，不改历史变更记录。Mac ZIP 的文件清单按安装用途提供，开发脚本需从完整仓库取得。

发布验收由 `ruff`、全量测试、wheel 安装后冒烟验证、源码包重建和 Mac ZIP 清单检查组成。原生编译与实际桌面采集分开记录；目标机器还需实测新消息、重复轮询、窗口恢复、重启和所选邮箱投递。只在测试群进行主动转发和恢复实验，不用测试结果承诺全历史完整。
