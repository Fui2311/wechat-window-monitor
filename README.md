# 微信群讲座通知助手

本地采集指定微信群的可读消息，识别讲座与学术报告，保留原文，并生成邮件通知与日历文件。提供命令行和浏览器控制台，支持 macOS Accessibility、Windows UIAutomation 和离线 JSONL 输入。

**正式版本：1.1.2 · Python 3.11+ · 核心运行时无第三方 Python 依赖。** 安装包见 [GitHub Releases](https://github.com/Fui2311/wechat_tracking/releases)。

默认使用本地规则、仅预览邮件；启用 LLM 后才向所配置的兼容接口发送待分析正文，显式启用发信后才投递 SMTP。不会向微信群发送消息或代替用户报名。

## 功能与边界

- 发现讲座相关消息即生成通知，不以时间缺失、取消、入选资格或置信度阻止通知；报名开始时间、活动时间、链接均以原文为依据。
- 默认场地策略只接收石牌校区及未明确举办校区的消息；明确在其他校区举办的活动不通知。履历、主办单位中的校区不作为举办地点。
- 规则模式保留完整可读原文；LLM 模式展示提取结果及原文，接口失败自动回退规则。模型开关、地址和密钥可在控制台配置。
- SQLite 保存原始消息、采集指纹、通知记录与持久队列；重复采集去重，发送失败退避重试，预览不会把任务记为已发送。
- 控制台显示采集到的讲座/非讲座消息、服务状态、邮件预览、队列、历史只读库和 ICS 导出；支持探测并切换精确群名。

采集依赖已登录的本机桌面和可读的目标群窗口，只覆盖已加载的可访问消息。锁屏、系统休眠、未加载历史、微信版本变化及突发大量消息可能造成缺口。转发卡片摘要不等于正文；自动展开是可选实验功能，会按配置短暂使用前台。附件只保留可读元数据，不执行 OCR、不读取未下载文件。SMTP 采用至少一次投递，服务器接受后进程意外退出可能导致重复邮件。

## 快速开始

### 源码安装与离线体验

```bash
git clone https://github.com/Fui2311/wechat_tracking.git
cd wechat_tracking
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
wechat-track --version
wechat-track init demo.local.toml --platform jsonl
wechat-track --config demo.local.toml demo
wechat-track --config demo.local.toml web --open
```

Windows PowerShell 用 `python` 创建环境，激活命令为 `.\.venv\Scripts\Activate.ps1`。也可始终使用 `.\.venv\Scripts\python.exe -m wechat_tracker ...`。私有仓库需先通过自己的 GitHub 账号认证。

`demo` 使用包内合成/人工转写样例，固定走本地规则，写入配置目录下的 `state/demo.sqlite3`、`demo-email-preview.txt` 和 `demo.ics`，不访问微信、LLM 或 SMTP。它验证软件流程，不能证明桌面采集或邮件送达。

首次打开网页默认查看当前活动库；上面的 `demo` 写入独立演示库。请在页头数据源中选择「Demo 演示」，再查看讲座、采集消息及邮件预览；当前活动库为空不代表回放失败。也可直接点击网页「运行 Demo」，完成后会自动选中演示库。

安装 wheel 后可在任意工作目录使用 `wechat-track init` 创建配置；不会覆盖已有文件。下列命令都显式指定配置，避免误用本机另一份安装的配置。

### macOS 实时部署

推荐下载 Release 中的 `WeChatLectureTracker-macOS-1.1.2.zip`，解压后执行：

```bash
python3 mac/verify-package.py .
bash mac/install.command
```

需先安装 Python 3.11+ 和 Apple Command Line Tools（`xcode-select --install`）。安装器在本机编译、签名 Swift 辅助程序，保留已有配置和数据，不自动发信。它是源码安装包，无需另一台 Windows 主机。

安装后编辑 `~/Library/Application Support/WeChatLectureTracker/config.toml` 中的精确 `group`；在系统辅助功能设置中授权采集 App，将目标群打开为独立、非最小化窗口：

```bash
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" authorize
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" verify --offline
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" probe
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" install
bash "$HOME/Library/Application Support/WeChatLectureTracker/mac/manage.command" web --open
```

`probe` 成功后再启动预览服务。完整安装、升级、回滚和故障排查见 [部署指南](docs/deployment.md) 与 [macOS 说明](docs/macos.md)。源码开发者先运行 `python scripts/build-mac-package.py`，再从生成的 ZIP 安装；源码工作区不带安装清单。

### Windows 实时部署

在已安装项目的环境中创建配置，填入精确群名：

```powershell
wechat-track init config.local.toml --platform windows --group "你的精确群名"
wechat-track --config config.local.toml probe
wechat-track --config config.local.toml watch --once --include-existing
wechat-track --config config.local.toml service install
wechat-track --config config.local.toml service status
wechat-track --config config.local.toml web --open
```

需要已登录的 Windows 微信、交互式用户会话、PowerShell/.NET UIAutomation 和可读的目标群独立窗口。`service install` 创建当前用户登录任务。Linux 可运行离线处理、测试和本地控制台，不提供 Linux 微信采集适配器。参见 [Windows 采集](docs/capture.md) 与 [服务管理](docs/service.md)。

## 配置与日常命令

配置模板：Windows [config.example.toml](config.example.toml)，macOS [mac/config.example.toml](mac/config.example.toml)。`init` 自动选择平台，也支持显式 `--platform`。

| 配置项 | 用途与默认值 |
| --- | --- |
| `group` | 要采集的完整精确群名，白名单校验 |
| `data_dir` | 相对于配置文件目录的状态目录，默认 `state` |
| `processing_mode` | `rules`（默认）或 `llm` |
| `llm_base_url` / `llm_model` | 兼容 Chat Completions 的基础地址与模型名称 |
| `llm_api_key_env` | 密钥环境变量名；控制台保存的独立 `.llm-key` 文件优先 |
| `reminder_minutes` | 默认 `[]`，关闭活动前定时提醒 |
| `[capture]` | `macos_ax` / `windows_uia` / `jsonl`，辅助程序路径和转发策略 |
| `[service]` | 核对间隔、调度间隔、首次是否分析现有可见消息、备份和防系统休眠 |
| `[email]` | SMTP 主机、端口、TLS 模式及凭据环境变量名；不写明文授权码 |

```bash
wechat-track --config config.local.toml events
wechat-track --config config.local.toml outbox
wechat-track --config config.local.toml tick
wechat-track --config config.local.toml calendar state/lectures.ics
wechat-track --config config.local.toml service status
wechat-track --config config.local.toml service stop
wechat-track --config config.local.toml service start
```

前台 `watch` 首次默认只建立可见消息基线，`--include-existing` 才处理已有消息；后台服务模板的 `service.include_existing=true`，首次会分析当前可见消息。请选择符合需要的行为。不要对同一数据库同时运行多个发信进程。

控制台只监听本机 `127.0.0.1:8088`，校验 Host/Origin；关闭控制台不停止独立的监控服务。切换群名时先探测新窗口、停止旧实例并分离数据目录；新群保持停止，需显式点击启动预览。旧群历史保留只读。

采集消息翻到较早页面后会固定本次浏览的入库边界，后台新消息不会把记录挤到下一页造成重复。返回第一页、重新搜索或点击页头刷新可读取最新消息；页头刷新会将采集消息返回第一页。

### 邮箱与模型

`email setup` 会要求在本机隐藏输入授权码，并实际发送一封配置测试邮件；macOS 用 Keychain、Windows 用当前用户 DPAPI 保存凭据。日常开启发信需要显式 `--send` 或控制台“启动并发信”：

```bash
wechat-track --config config.local.toml email setup
wechat-track --config config.local.toml service install --send
```

控制台会区分“查看的数据源”和“操作的管理群”。默认规则模式收起可选 LLM 参数，按需展开；手动刷新同时发现新增数据源并保留未保存草稿；停止确认绑定当时的管理群，切群后的过期请求会被拒绝。列表短暂读取失败后可自动恢复；网页日历会核对错误和空结果，避免下载无效文件。开启发信前展示当前管理群的已有积压，历史待发通知可能立即投递；Demo 始终只预览。LLM 设置可在控制台修改并于下一条新消息生效；默认规则模式不调用网络。控制台的模型测试只处理输入框里的文字，不入库、不入队、不发信，但会调用所选模型接口。详见 [邮箱配置](docs/email.md)、[模型处理](docs/llm-processing.md) 和 [控制台使用流程](docs/console-usage.md)。

## 项目模块与结构

```text
wechat_tracker/
├── domain/               # 纯规则：消息校验、稳定指纹、时间、策略版本
├── application/          # 用例：采集入库、通知身份/版本、重算、队列调度
│   ├── ports.py          # Repository / MessageProcessor / MailSender 契约
│   ├── tracker.py        # 用例协调器，注入仓储、分析器、发信器和时钟
│   ├── events.py         # 通知身份、旧活动版本兼容与入队规则
│   └── delivery.py       # 领取任务、预览、过期、重试与投递记账
├── persistence/          # SQLite 仓储实现与原子领取 SQL
├── extraction/           # 设置/密钥持久化、LLM HTTP 适配、规则回退管线
├── presentation/         # 纯文本/HTML 通知格式，预览和投递共用
├── console/              # HTTP 适配、只读查询、服务发现、公开状态投影
├── analyzer.py           # 本地中文识别、时间与报名信息、场地策略
├── store.py              # SQLite 连接、建表、完整性检查、校验备份
├── capture.py / macos.py # Windows UIA / macOS AX 标准化与精确来源校验
├── stream.py / watcher.py# 原生帧传输、卡片展开缓存与轮询补全
├── service.py            # 常驻进程、控制通道、锁、健康状态与备份
├── autostart.py          # macOS LaunchAgent / Windows 登录任务
├── credentials.py       # Keychain / DPAPI 与邮箱授权
├── engine.py             # 兼容业务入口，集中装配默认适配器
├── processing.py / web.py# 稳定公共入口，转到各自模块
├── notify.py             # SMTP/TLS 传输，兼容导出格式化函数
├── bootstrap.py / cli.py # 配置初始化与命令行入口
└── resources/            # Swift、PowerShell、合成样例和配置模板
    └── web/              # 浏览器资源，无需构建步骤
        ├── presentation.js # 纯显示规则：时间、链接安全、监控状态
        ├── http.js         # 注入 fetch 的请求与错误处理
        ├── app.js          # 页面状态、DOM 和异步请求协调
        └── index.html / app.css # 页面结构与样式
mac/                      # Mac 源码安装器、管理脚本、原生验证
scripts/                  # 构建、安装包验收及开发辅助脚本
tests/                    # 行为回归、依赖边界与适配器测试
docs/                     # 架构、部署、平台说明及历史验收记录
.github/workflows/        # 多平台回归、打包与安装后冒烟检查
```

业务层依赖 `ports.py` 中的结构化接口；SQLite 和 SMTP 的选择由 `Engine` 装配，调用方可注入自己的实现。`application/` 不导入桌面采集、SQLite、HTTP、配置持久化或 SMTP。Web 查询以 SQLite 只读连接读取，不能通过查看历史库触发建表或重算。原有 `Engine`、处理接口、命令行、Web API 和 SQLite 表格式继续兼容。

前端通过两个明确的模块接口调用展示规则和 HTTP 适配器：展示规则只接收数据与时区，不读取 DOM、本机存储或配置全局；HTTP 适配器接收请求函数，可用假传输独立验证。主控制器负责页面状态与交互。浏览器按展示模块、HTTP 模块、控制器的顺序加载，无需前端构建工具或运行时依赖。

数据流程与扩展契约见 [架构说明](docs/architecture.md)。项目使用 Python 标准库与系统接口，没有直接依赖 pywechat/wxauto 等机器人框架；历史调研见 [复用核查](docs/github-reuse.md)。

## 开发、验证与发布

```bash
python -m pip install -e '.[dev]'
python scripts/sync-version.py --check
python -m ruff check wechat_tracker tests mac scripts
python -m unittest discover -s tests -v
node --test tests/frontend/*.test.cjs  # 开发测试需要 Node.js 22+；运行产品不需要
python -m build
python scripts/check-release.py
python scripts/build-mac-package.py
```

`check-release.py` 在仓库外创建干净虚拟环境，从 wheel 安装后核对资源、配置初始化、样例去重、邮件预览、ICS 和 HTTP API，禁止调用微信、LLM 或 SMTP。CI 对 PR 和 main 运行验证，功能分支 push 不再与 PR 重复触发。CI 在 Linux、macOS、Windows 运行 Python 回归，并单独运行异步前端状态回归；另检查源码分发包可重新构建及 Mac 安装 ZIP 的清单。桌面窗口、权限、转发和 SMTP 实际送达需要在目标机器单独验收，CI 成功不代表这些物理条件已经满足。

开发和发布操作需要完整 Git 仓库；Mac ZIP 用于安装，不包含开发测试脚本。版本号以 `pyproject.toml` 为唯一来源，修改后运行 `python scripts/sync-version.py --write` 同步 Python、原生助手、界面及文档标签，再审查差异和运行验证。`--check` 只读检查，CI 和安装包冒烟会拒绝标签漂移；历史 CHANGELOG 不会被批量替换。

版本变更见 [CHANGELOG.md](CHANGELOG.md)。`state/`、本机配置、`.env`、授权码、模型密钥、聊天数据库和构建产物均不应进入 Git；发布脚本仅打包明确允许的源码与资源。旧版升级及数据库恢复步骤见 [部署指南](docs/deployment.md)。历史交接和实测文档保留原日期，其运行快照不代表当前服务状态。
