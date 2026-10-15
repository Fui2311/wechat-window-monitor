# 0.4.0：Mac 接续开发交接

当前需求：手动报名，通知固定为讲座摘要、报名截止、活动时间三项。活动开始前24小时/1小时提醒，报名截止仅展示。

从 [HANDOFF.md](https://github.com/Fui2311/wechat_tracking/blob/main/HANDOFF.md) 开始，里面包含私密仓库克隆、Mac离线基线、安装、AX适配、恢复测试、邮箱与全天验收的接续事项，以及给下一位开发助手的提示词。

交付资产：

- `wechat_tracking-0.4.0-source.zip`：完整Git开发树，包含源码、测试、平台脚本、配置模板、交接/运行文档及Word/PDF；开发优先克隆Git仓库以保留历史。
- `WeChatLectureTracker-macOS-0.4.0.zip`：独立Mac源码安装包，含HANDOFF及32项SHA256清单；在Mac本机编译Swift。
- 对应 `.zip.sha256`：单独下载Mac包时的外层校验。
- `wechat_lecture_tracker-0.4.0-py3-none-any.whl`：共享Python业务包，不替代Mac原生安装器。
- `WeChatLectureTracker-0.4.0-Mechanism.pdf/.docx`：当前三项通知机制说明。
- `handoff-validation.json`：Windows侧本轮核验摘要，明确列出尚待Mac实测的项目。
- `SHA256SUMS.txt`：全部Release资产的SHA256校验清单。

已在Windows再次通过150项测试、Mac包完整清单/CRC和项目外离线回放。Swift原生编译、辅助功能/微信群绑定、Keychain、LaunchAgent及Mac实机运行仍待验证，因此此Release标为预发布。

Git仓库已核实为私密，Release访问权限随仓库。上传内容不含本机凭据、配置、生产聊天库/原始抓取、日志、备份或Windows运行环境。Windows现有服务继续运行；QQ邮箱按用户选择尚未配置，没有真实投递结果。

注意：Git开发树没有安装包清单，不能直接从克隆目录运行mac/install.command。请先按HANDOFF构建安装ZIP，再在解压目录安装；修改源码后也需重新构建/安装才会更新运行副本。
