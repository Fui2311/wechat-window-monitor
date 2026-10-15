# macOS CI 草案（尚未启用）

`macos-validation.yml` 是手动触发的原生编译/安装器验收草案，计划在macOS runner编译arm64与x86_64，执行Python测试，再从独立ZIP安装并运行离线验收和原生self-test。它不登录微信、不发送邮件，不验证真实Accessibility/Keychain/LaunchAgent行为。

2026-10-15尝试保存到 `.github/workflows/` 时，GitHub明确拒绝：当前OAuth App没有 `workflow` scope。因此改为普通文档草案，不会被GitHub Actions执行，没有云端编译结果。其余代码与实测记录照常推送。

以后由用户决定是否为仓库配置具备workflow权限的授权，再将草案放入 `.github/workflows/` 手动运行。当前Mac交付的原生编译、安装与实机行为仍以Mac本地验收为准。
