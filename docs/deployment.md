# 部署、升级与发布（2.0.0）

## 安装

Python 3.11+，建议独立虚拟环境。源码安装执行 `python -m pip install .`；wheel安装：

```bash
python -m pip install 路径/wechat_window_monitor-2.0.0-py3-none-any.whl
wechat-monitor --version
wechat-monitor --data-dir ./state/my-workspace serve --open
```

默认127.0.0.1:8765，不允许外网监听。不要把控制台端口代理到公网。本地同目录只允许运行一个监控进程；不同目录可选不同端口。Ctrl+C正常关闭并等待正在执行的模型/捕获结束。关闭浏览器不停止Python进程。

## macOS 原生辅助程序

在本机安装Apple Command Line Tools（`xcode-select --install`），运行：

```bash
python scripts/build-monitor-helper.py
open "$HOME/Applications/WeChat Window Monitor Capture.app"
```

脚本只编译、签名独立App，不启动采集、不安装登录任务、不修改旧讲座辅助App。若目标App已存在会拒绝覆盖，请先选择不同的 `--output /完整路径/NewCapture.app`，或保留旧副本后自行更换。系统设置中给新App辅助功能权限；窗口设置填实际可执行文件路径。保持微信登录、目标会话为独立非最小化窗口，点击“检查窗口”，确认再开始监控。

macOS因辅助功能和签名身份依赖目标机器，不提供跨机器可直接授权的预编译二进制；源码和构建脚本随源码包提供。wheel包含原生Swift源码，也可将已下载源码中的构建脚本配合安装环境运行。

## Windows

保持微信交互桌面会话，PowerShell/.NET UIAutomation可用。窗口设置选择Windows UIAutomation；进程名默认Weixin，必要时在高级设置填写实际进程名。只支持现有探针能证明消息列表结构的会话，精确标题相同但结构不同会拒绝读取，不猜测替代窗口。

## 模型

设置兼容Chat Completions基础URL、模型名称、API Key或密钥环境变量名。远程HTTPS、本机可HTTP；不跟随重定向。接口必须支持 `response_format=json_object`，输出严格JSON。保存设置不调用接口。目标生成、语义测试和新消息识别是显式/已启用的调用；本地识别不调用模型。

生成规则后核对JSON，尤其关键词无法表达的语义边界。任意生成代码不会执行。失败消息在记录页标识，点击重试后需运行监控才会继续处理。

## 升级、备份、旧版本

停止该工作空间进程，确认结束；备份完整数据目录及当前虚拟环境/安装版本，再安装新wheel。数据库原文、规则、结果和本地密钥一起保留在数据目录，不把备份上传。升级后默认停止，用离线检查和窗口探针核对后再开始。

旧 wechat_tracking 讲座版的数据库、SMTP设置和常驻服务不会自动导入或控制。旧应用可以继续独立使用，但不要与新应用对同一桌面窗口同时执行前台动作。本产品默认只读捕获，不自动展开转发、不滚动/恢复窗口。旧mac/install.command、build-mac-package.py和讲座命令只保留为历史兼容工具，部署本产品请按本指南。

## 发布验收

Node22.12+/24执行npm ci、test、build；Python执行全部回归、ruff、版本同步检查、构建和check-release.py。构建产物包括Vue资源、通用包及继承的原生资源；安装冒烟在仓库外临时目录验证，无真实桌面、模型或SMTP调用。发布记录必须区分本地合成验证、平台CI与真实桌面采集，不能用假捕获宣称私聊/公众号实机全兼容。
