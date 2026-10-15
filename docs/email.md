# 邮箱提醒配置

通知模块只通过校验证书的 TLS 发送文本邮件，不读取或发送群文件。测试和本地预览不会发送邮件；是否发送由运行入口的显式发送选项决定。

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

提醒会注明来源群、发送者、原文摘录及需要核实的内容。报名要求只是通知内容；系统不能据此确认你是否报名成功或在名单里。需要回群查看的名单 PDF 不会自动作为邮件附件发送。
