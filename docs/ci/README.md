# 持续集成

1.0.0 的活动工作流是 [ci.yml](../../.github/workflows/ci.yml)：在 Linux、macOS、Windows 和 Python 3.11/3.13 上运行静态检查、回归测试、构建、wheel 安装后验收及 Mac ZIP 清单检查，另有独立的 Swift 原生编译任务。

本目录的 `macos-validation.yml` 保留为 0.4.0 历史草案，不会执行。早期 OAuth workflow 权限不足的记录属于当时状态；当前结果以 GitHub Actions 的实际运行状态为准。云端任务不登录微信、不调用真实 SMTP、不替代目标桌面的窗口和授权验收。
