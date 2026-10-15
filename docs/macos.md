# macOS 独立监控安装与验收

版本 0.3.0，目标为 macOS 12+、Python 3.11+。Apple Silicon 使用原生 arm64 Python，Intel Mac 使用 x86_64。业务分析、去重、队列和 QQ 邮箱提醒在 Mac 本地独立运行，无需 Windows 在线。

交付为 `WeChatLectureTracker-macOS-0.3.0.zip` **源码安装包**，带完整 SHA256 清单；不是已在 Mac 编译、公证的二进制 DMG。包内不含 Windows 脚本、Windows Python、聊天数据库或邮箱凭据。Windows 已做可移植代码/包内容验证；Swift 编译、Accessibility 选择器、Keychain 与 LaunchAgent 实际兼容性待后续 Mac 验收。

## 安装

先安装 Python 3.11+（[官方 Python](https://www.python.org/downloads/macos/) 或 `brew install python`）和 Apple Command Line Tools（终端执行 `xcode-select --install`）。解压 ZIP，在解压后的目录执行：

```bash
python3 mac/verify-package.py .
bash mac/install.command
```

包的 SHA256 清单覆盖全部交付文件。安装器再次校验，然后在当前 Mac 原生编译 Swift 采集器并检查代码签名。核心无第三方 Python 依赖，不下载或迁移 Windows 环境。编译失败会先停止安装，不停止已有版本。

安装位置：

- Python 和配置：`~/Library/Application Support/WeChatLectureTracker/`
- 原生程序：`~/Applications/WeChat Lecture Tracker Capture.app`
- 本机数据：`~/Library/Application Support/WeChatLectureTracker/state/`
- 登录启动：`~/Library/LaunchAgents/local.wechat-lecture-tracker.<群名摘要>.plist`

安装器保留已有配置、数据和凭据；旧采集 App 与 Python 包另存带时间戳的备份。安装时不发送邮件、不自动开启服务。安装器使用固定 Bundle ID 的本机 ad-hoc 签名 App；不是 Apple Developer 公证包。系统阻止首次打开时，可在 Finder 右键“打开”或系统设置中允许本次打开。更新编译后可能需要重新授予辅助功能权限。

## 辅助功能与精确群名

```bash
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" authorize
```

在“系统设置 → 隐私与安全性 → 辅助功能”允许 **WeChat Lecture Tracker Capture.app**；必要时用“+”添加上述 App。部分系统会将直接终端启动的操作归属 Terminal，此时也需允许实际启动终端。无需开启屏幕录制，因为采集读取 Accessibility 文本。

登录微信，将 `2026级计算机研究生通知群` 打开为独立、非最小化窗口，然后执行：

```bash
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" verify
```

必须得到原生 `native_probe.ok=true` 才说明当前版本能绑定该群。`accessibility_permission_required` 表示授权未就绪，`process_not_found` 表示微信进程未就绪，`target_window_not_ready` 表示没有可核对的群窗口，`message_list_not_unique` 表示消息列表还需适配。微信默认 Bundle ID 为 `com.tencent.xinWeChat`，不同版本需核查本机实际值再修改配置。

不自动选择联系人栏中同名文字作为当前聊天。主窗口只有在人工核实后，明确填写 `capture.mac_header_identifier` 才能绑定；多列表场景需要核实 `capture.mac_list_identifier`。两者默认留空，匹配失败就等待，不读取其他聊天。Mac 选择器从未在本机实测，首次验收若不通过，须根据实际 Accessibility 结构继续适配。

## 开始、停止与重启

以下命令安装 LaunchAgent 并立即开始：

```bash
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" install --send
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" status
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" restart
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" stop
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" start
```

LaunchAgent 在当前用户登录会话运行，失败退出后保活；不支持注销后在后台读取微信。显式 stop 卸载当前会话的 Agent，start 恢复加载；uninstall 删除登录启动项并停止服务，保留数据、App 和 Keychain，便于恢复。双击已安装的 `mac/restart.command` 可重启。

已安装任务的发送模式以安装时参数为准。开启 `--send` 但 QQ 凭据未配置时只预览，任务保留待发，不消耗失败次数。以后更改发送模式，重新运行 `install` 或 `install --send`。

```bash
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" email setup
```

授权码只在本机隐藏输入；向指定收件邮箱测试成功后写入当前用户 Keychain，服务自动加载。安装器和 verify 不发测试邮件。与 Windows 的 DPAPI 凭据独立，不要拷贝 Windows 凭据文件。

## 本地验收命令

```bash
# 仅合成样例，验证分析、去重、队列、预览，不访问微信或 SMTP
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" verify --offline

# 检查原生编译产物、代码签名和真实目标群可读性
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" verify

# 启动服务后，连续观察心跳；可将 60 改为 86400 做全天观察
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" verify --observe-seconds 60

bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" events
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" outbox
```

后续实机还需验证：关闭/重新打开群窗口后自动恢复，服务异常退出后 LaunchAgent 拉起，重启不重复生成活动/队列，资源占用，QQ 邮件实际送达。`verify --observe-seconds` 检查服务实例和采集心跳，不证明历史完整，也不计算消息漏收率。

只采集已加载、与目标消息列表视口相交的 Accessibility 行。定期核对默认 15 秒，失联等待并重新绑定；AX 事件可提前唤醒。开启尾部跟随只尝试 `AXScrollToVisible`，不保证加载全部历史。原始发送者与发送时间没有可靠证据时仍为未知。默认关闭转发自动展开；可选实现尚需 Mac 实测，附件/OCR、全历史补齐不在本版内。

日常需保留已登录、微信在线的专用会话。服务默认通过 caffeinate 防止空闲系统休眠；手动休眠、锁屏、微信退出、最小化与系统授权变化仍可能造成采集缺口，健康记录会保留可观测的中断。
