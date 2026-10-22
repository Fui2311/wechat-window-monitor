# 静态审查报告：首次离线体验与升级命令一致性

**范围**：`README.md`、`docs/deployment.md`、`wechat_tracker/cli.py`、`mac/install.command`、`mac/manage.command`、`pyproject.toml`  
**日期**：2026-10-23  
**方法**：纯静态阅读，无命令执行、无网络、无数据库、无 git 操作。  
**版本锚点**：`pyproject.toml` L7 → `version = "1.1.1"`，与 README L5、deployment.md L1 标题一致。

---

## 一、真实错误（源码可证明的不一致）

### E-1 `install.command` 执行的是 `mac/install.py`，而非脚本自身

**证据**  
- `mac/install.command` L10：`exec "$candidate" mac/install.py`  
- README L47：`bash mac/install.command`（正确）  
- deployment.md L24：`bash mac/install.command`（正确）  

`install.command` 只是一个 Python 探针 + exec 桥，实际工作由 `mac/install.py` 完成。文档入口命令本身没错，但两份文档对"安装器"的描述（"在本机编译、签名 Swift 辅助程序"，README L50；deployment.md L24 "编译 Swift、进行本机 ad-hoc 签名"）均将行为归因于 `install.command`，而该脚本只做版本检测和 exec 转发，实际安装逻辑在审查范围外的 `mac/install.py`。在本次审查内无法进一步核实 `install.py` 是否确实执行了这些步骤。

> 属于"归因描述不精确"而非"命令错误"。用户按文档执行不会出错，但若 `mac/install.py` 不存在（如在无清单的开发目录直接运行），`install.command` 会以 `set -euo pipefail` 退出，错误信息不够明确。

---

### E-2 `manage.command verify` 不传 `--offline`，默认行为文档未对齐

**证据**  
- README L56：`bash "…/manage.command" verify --offline`  
- deployment.md L29：`bash "…/manage.command" verify --offline`  
- `mac/manage.command` L18：`verify) shift; exec "$PYTHON" "$BASE/mac/verify.py" --config "$CONFIG" "$@" ;;`

脚本将 `--offline` 原样透传给 `mac/verify.py`（审查范围外）。文档的用法本身一致，但 `--offline` 是否必须由 `verify.py` 决定，脚本层无法拒绝省略该标志。若用户省略 `--offline` 而 `verify.py` 默认执行联网检查，则文档给出的命令是"正确但最小化"版本。

> 两份文档行为描述一致，不构成文档间矛盾。边界：`verify.py` 的实际行为未在审查范围内。

---

### E-3 `manage.command` 不支持 `tick` 子命令

**证据**  
- `mac/manage.command` L20：只路由 `probe|events|outbox|calendar|web`  
- `mac/manage.command` L21：只路由 `run|start|stop|restart|status|install|uninstall`（以 `service` 为前缀）  
- `mac/manage.command` L22：Usage 中未列出 `tick`  
- `wechat_tracker/cli.py` L57–59：`tick` 是有效子命令，支持 `--send` 和 `--now`  
- deployment.md L66：`tick --send` 提及为"一次真实投递"  

部署指南的排错表（L66）提到 `tick --send`，并无前缀 `manage.command`，只是裸 `wechat-track` 调用。这是正确的——`manage.command` 确实不封装 `tick`。文档在这一点上没有明确错误，但 macOS 用户如需一次性投递须直接调用 Python，`manage.command` 的 Usage 也未提示该路径，存在文档缺口（见 S-1）。

---

### E-4 README 与 deployment.md 安装序列步骤不一致

**证据**  
- `mac/manage.command` L21：`run|start|stop|restart|status|install|uninstall) exec "$PYTHON" -m wechat_tracker --config "$CONFIG" service "$@" ;;`  
- README L58–59：序列为 `install` → `web --open`（5 步）  
- deployment.md L36–38：序列为 `install` → `status` → `web --open`（6 步）

命令路由本身正确。但 README 省略了 `status` 确认步骤，deployment.md 多一步，造成步骤数量不一致，属于建议级别（见 S-2）。

---

### E-5 离线体验命令序列验证（与实现一致）

**证据**  
- `cli.py` L69：`demo.add_argument("--now", default="2026-10-15T13:00:00+08:00")`  
- `cli.py` L163–167：demo 写入 `Path(config["data_dir"]) / "demo-email-preview.txt"` 和 `demo.ics`  
- `cli.py` L135：demo 使用 `"demo.sqlite3"` 而非 `"tracker.sqlite3"`  
- README L37："`state/demo.sqlite3`、`demo-email-preview.txt` 和 `demo.ics`"（与代码一致）  
- README L31：`wechat-track --config demo.local.toml demo`（正确）  

**README 第 22–33 行的离线体验命令序列与 `cli.py` 实现完全一致，无错误。**

---

## 二、建议（非源码错误，属于清晰度或一致性改进）

### S-1 `manage.command` Usage 字符串缺少 `tick` 使用提示

- `manage.command` L22 的 Usage 未提及 macOS 下如何进行单次真实投递（需直接调 Python）。  
- 建议在 Usage 末尾或 deployment.md macOS 节增加提示：macOS 用户执行一次性投递须用 `"$BASE/.venv/bin/python" -m wechat_tracker --config "$BASE/config.toml" tick --send`。

### S-2 README 与 deployment.md 安装序列步骤数不同

- README L58–59 省略了 `status` 步骤（5 步 vs deployment.md 的 6 步）。  
- 建议统一，或在 README 中标注 `status` 为可选确认步骤。

### S-3 `install.command` Python 候选顺序未覆盖 `pyenv`/`asdf` 管理的路径

- `install.command` L8–11 探测顺序：`$WECHAT_PYTHON` → `python3` → `/opt/homebrew/bin/python3` → `/usr/local/bin/python3`。  
- `pyenv`/`asdf` 管理的 Python（通常在 `~/.pyenv/shims/python3` 或 `~/.asdf/shims/python3`）若未加入 PATH，仅靠硬编码路径无法找到。  
- `$WECHAT_PYTHON` 是逃生出口，但 README L50 未提及该变量。建议 README 补充一行：如使用 `pyenv`/`asdf`，可 `export WECHAT_PYTHON=$(pyenv which python3)` 后再运行安装器。

### S-4 deployment.md wheel 安装命令中文件名含硬编码版本号

- deployment.md L62：`pip install 路径\wechat_lecture_tracker-1.1.1-py3-none-any.whl`  
- 该文件名与 `pyproject.toml` L7 的 `version = "1.1.1"` 及 `name = "wechat-lecture-tracker"` 匹配，当前版本无误。  
- 但文档中的版本号硬编码会在下一个版本升级时失效。建议改写为 `pip install 路径\wechat_lecture_tracker-*.whl` 或加注说明需替换版本号。

---

## 三、核查项——当前范围内可确认的一致性

| 核查点 | 结论 |
|---|---|
| `pyproject.toml` 版本 → README/deployment.md 标题 | ✅ 均为 1.1.1 |
| `wechat-track` 入口点 | ✅ `pyproject.toml` L21 与 `cli.py` L29 `def main` 一致 |
| `--version` 行为 | ✅ `cli.py` L37 读取 `__version__`，路径符合 setuptools 约定 |
| `init` 子命令 `--platform` 选项 | ✅ `cli.py` L43：`choices=["macos", "windows", "jsonl"]`；README/deployment.md 均使用合法值，一致 |
| `demo` 使用独立数据库 | ✅ `cli.py` L135 确认 `demo.sqlite3`；README L37 描述一致 |
| `demo` 不发送 SMTP | ✅ `cli.py` L162：`send=False` 硬编码；README L37 "不访问微信、LLM 或 SMTP"，一致 |
| `manage.command authorize` | ✅ L17 调用 `open App`，与 deployment.md L28/README L55 一致 |
| `manage.command probe/events/outbox/calendar/web` | ✅ L20 直接透传子命令，与文档命令序列一致 |
| `manage.command install/stop/start/restart/status/uninstall` | ✅ L21 以 `service` 为前缀透传，与 README L56–59、deployment.md L35–38 一致 |
| `rebuild-analysis` 安全检查 | ✅ `cli.py` L144 在服务运行时拒绝重算；deployment.md L80 描述一致 |
| `web --port` 选项 | ✅ `cli.py` L79；deployment.md L96 提及 `--port 8089`，一致 |
| `tick --send + --now` 互斥 | ✅ `cli.py` L192–193；deployment.md L66 逻辑一致 |

---

## 四、不能在当前范围内验证的边界

- `mac/install.py`：安装器真实行为（Swift 编译、签名、文件复制）无法核实。
- `wechat_tracker/__init__.py`：`__version__` 的实际值（推测由 `sync-version.py` 同步）。
- `mac/verify.py`：`--offline` 标志的语义及是否为必须项。
- `manage.command` 调用链中 `start_service`、`manage_startup` 的实现细节。
- `wechat-track` 入口在 wheel 安装后的可执行性（无法实际运行）。

---

## 五、结论

**无阻断问题。** 用户按 README 和 deployment.md 执行首次离线体验和 macOS 安装/升级命令，不会因文档错误导致流程失败。

发现的主要问题均为**建议级**（S-1～S-4），或为**归因描述不精确**（E-1）。  
`manage.command` 不封装 `tick`（E-3）已在文档中隐含正确处理（用裸 `wechat-track` 调用），但可在 Usage 中补充提示。  
README 与 deployment.md 安装序列差一个 `status` 步骤（E-4/S-2），不影响正确性。
