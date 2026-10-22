# 邮箱提醒配置

通知模块只通过校验证书的 TLS 发送文本邮件，不读取或发送群文件。测试和本地预览不会发送邮件；是否发送由运行入口的显式发送选项决定。

## QQ 邮箱本机配置

当前服务默认 `smtp.qq.com`、SSL、465。用户已选择稍后配置，在此之前保持预览和排队，不消耗 SMTP 重试。

```powershell
python -m wechat_tracker --config config.local.toml email setup
python -m wechat_tracker --config config.local.toml email status
```

也可运行 `scripts/configure-email.ps1` 打开本机交互流程。发件邮箱、收件邮箱和 SMTP 授权码只在本机填写，授权码隐藏输入；启用 QQ 邮箱 SMTP 后使用其授权码，不使用 QQ 登录密码。配置流程会先发一封明确的测试邮件，成功才保存。Windows 使用当前账户 DPAPI 加密到数据目录，macOS 使用当前用户 Keychain；不要跨机器拷贝凭据文件。服务每5秒重新检查配置，配置成功后无需重启。

`--send` 与凭据同时就绪才会外发。已安装任务的发送模式以安装参数为准；需变更时重新 `service install --send`（或不带 `--send` 仅预览）。`tick/watch --send` 与常驻服务均可加载本机加密凭据，也支持下面的进程环境变量入口。

## 环境变量入口

配置示例（仅使用占位 SMTP 主机和环境变量名）：

```json
{
  "smtp_host": "smtp.example.com",
  "smtp_port": 465,
  "smtp_security": "ssl",
  "smtp_user_env": "WECHAT_SMTP_USER",
  "smtp_password_env": "WECHAT_SMTP_PASSWORD",
  "from_env": "WECHAT_EMAIL_FROM",
  "to_env": "WECHAT_EMAIL_TO",
  "smtp_timeout": 15
}
```

除 `smtp_timeout` 外，以上字段均为必填。`smtp_timeout` 默认 15 秒。未知字段会报错。`smtp_security` 仅支持 `ssl`（常用端口 465）或 `starttls`（常用端口 587），具体主机和端口以邮箱服务商说明为准；TLS 协商失败不会继续登录或降级发送。

运行前，在当前进程环境或用户环境中设置这四个环境变量：

| 环境变量 | 内容 |
| --- | --- |
| `WECHAT_SMTP_USER` | 邮箱服务商要求的 SMTP 用户名 |
| `WECHAT_SMTP_PASSWORD` | SMTP 专用授权码或应用密码 |
| `WECHAT_EMAIL_FROM` | 一个发件邮箱地址 |
| `WECHAT_EMAIL_TO` | 一个或多个收件邮箱地址，使用英文逗号分隔 |

例如，收件变量可以是 `first@example.com,second@example.com`。地址使用纯邮箱地址，不包含显示姓名或换行。以上环境变量名可以在配置中更改。授权码、邮箱账号和收发地址均不应写入配置文件、代码、截图或版本控制。

`send_email` 只做一次发送；本地预览、去重、发件队列和重试由调用层管理。部分收件人拒收也会返回失败，因此重新发送前应留意已经成功接收的收件人。错误信息不回显服务器响应、密码或邮件内容。

1.0.0 默认规则模式直接展示实际可读原文；LLM 模式展示提取结果与原文。报名开始时间、活动时间与链接以消息证据为准，截止时间不冒充开始时间。发现讲座相关消息即通知，不判断名单资格或取消整场活动。默认 `reminder_minutes = []`，不建立活动前定时提醒；报名由用户自行完成。
