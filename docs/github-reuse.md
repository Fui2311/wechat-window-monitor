# GitHub 源码核查与合并转发复用方案

核查日期：2026-10-15。只读取 GitHub 原始源码与许可证，没有安装、导入或执行第三方库。GitHub 匿名 API 本次返回 403 rate limit；改用 `git ls-remote` 固定分支提交，再读取对应提交的 `raw.githubusercontent.com` 文件。

## 核查结论

| 项目及固定提交 | 已确认的可复用入口 | 对微信 4.1 合并转发的证据边界 | 许可证 |
| --- | --- | --- | --- |
| [Hello-Mr-Crab/pywechat](https://github.com/Hello-Mr-Crab/pywechat/tree/8589baa049bb91d3a500602c167f07b2f8397a13) | `src/pyweixin/WeChatAuto.py` 的 `Messages.dump_chat_history`；`WeChatTools.py` 的 `Navigator.open_chat_history`；`utils.py` 的群历史解析函数 | `pyweixin` 声明支持 4.1.6+，但读到的上述 API 获取普通聊天历史；未在核查的这几个文件找到打开并解析合并转发的专用 API。 | 根 [LICENSE](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/LICENSE) 为 LGPL 2.1 |
| [cluic/wxauto](https://github.com/cluic/wxauto/tree/05e5a52379c99f401dc512acfdd811b3d701da8b) | `wxauto/elements.py` 的 `SelfMessage.parse()` / `FriendMessage.parse()` 明确解析合并转发 | 使用旧 `ChatRecordWnd`；[README](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/README.md) 声明微信 3.9.X，不能据此声称支持本机微信 4.1.15.13。 | 根 [LICENSE](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/LICENSE) 为 Apache 2.0 |
| [claw-codes/wx4py](https://github.com/claw-codes/wx4py/tree/ccbc7c13905f35b9861b7fb13ff8257b5258e749) | `src/features/chat.py` 的 `_get_chat_message_list()`、`_read_visible_chat_items()`、`get_chat_history()` | 4.x 的 `chat_message_list` 与 `mmui` 行类与本机实测吻合；核查的聊天模块没有合并转发专用解析。`ForwardRuleHandler` 是向别的聊天转发，不是读取收到的合并记录。 | [pyproject.toml](https://github.com/claw-codes/wx4py/blob/ccbc7c13905f35b9861b7fb13ff8257b5258e749/pyproject.toml#L7) 声明 AGPL-3.0-or-later；根 LICENSE 为 AGPL v3 |

## 实际代码路径及行为

`wxauto` 的 [SelfMessage.parse，827–848 行](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/elements.py#L827) 和 [FriendMessage.parse，938–959 行](https://github.com/cluic/wxauto/blob/05e5a52379c99f401dc512acfdd811b3d701da8b/wxauto/elements.py#L938) 的流程是：显示聊天窗口、将卡片滚入视口、点击卡片、查找顶级 `ClassName='ChatRecordWnd'`、遍历其中列表；每行按 `TextControl[0]` / `[1]` / `[2]` 获取发送者、时间和正文，最后按 Esc。可复用的是“转发父记录 → 子消息 → 每条证据时间”的数据结构。本项目当前 4.x UIA 行不暴露同样的 TextControl 子树，因此不复制它的窗口类、固定序号和点击坐标。

`pyweixin` 的 [Messages.dump_chat_history，3965–4026 行](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/src/pyweixin/WeChatAuto.py#L3965) 会打开聊天、打开普通历史窗口、进入多选状态并遍历，再使用 [parse_group_chat_history，512 行起](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/src/pyweixin/utils.py#L512) 解释发送者与正文。其 [窗口选择器，Uielements.py:348](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/src/pyweixin/Uielements.py#L348) 是 `mmui::SearchMsgUniqueChatWindow`，而 [Navigator.open_chat_history，1505–1563 行](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/src/pyweixin/WeChatTools.py#L1505) 使用与具体好友/群名有关的窗口标题。它与泛标题 `群聊的聊天记录` 的合并记录窗口不同，不能混用。

`wx4py` 的 [chat.py:1202–1234](https://github.com/claw-codes/wx4py/blob/ccbc7c13905f35b9861b7fb13ff8257b5258e749/src/features/chat.py#L1202) 明确使用 `AutomationId='chat_message_list'`，时间行类 `mmui::ChatItemView`，正文行类 `mmui::ChatTextItemView` / `mmui::ChatBubbleItemView`。这些选择器支持保留本项目已验证的主群采集路径。其 [get_chat_history，1267 行起](https://github.com/claw-codes/wx4py/blob/ccbc7c13905f35b9861b7fb13ff8257b5258e749/src/features/chat.py#L1267) 会搜索并滚动目标聊天，不能直接作为本项目只读当前目标窗口的调用入口。其 [README:326–328](https://github.com/claw-codes/wx4py/blob/ccbc7c13905f35b9861b7fb13ff8257b5258e749/README.md#L326) 也说明 4.x UI 不暴露发送者信息，与本机观测一致。

若以后复制或派生源码，需按对应许可证保留版权、许可证和修改说明，并评估 LGPL/AGPL 的分发或服务提供义务。本轮实现没有粘贴这些项目的程序代码，采用公开选择器事实和本机实际观察独立实现，当前没有引入这些包为依赖。

## 本项目合并转发模块

1. 普通采集统一输出 `kind='forward'`，兼容现有分析器；旧拼写 `forwarded` 的顶层 JSONL 输入归一化。卡片识别涵盖 `群聊的聊天记录` 和 `X和Y的聊天记录`。
2. 用户已打开合并记录后，由 `read_forward(config, parent_id)` 读取已有窗口；自动点击、滚动、下载均不在此函数中。PowerShell `-Mode forward` 使用目标进程、精确标题、可选窗口句柄、唯一 List 和直接子行 AutomationId 进行校验。
3. `normalize_forward_snapshot(snapshot, config, parent_id)` 是纯函数，可读取受控 computer-use 工具已经采集的 AX 快照，避免同一轮混用桌面操作后端。`capture.forward_parent_id` 必须与显式 `parent_id` 一致。
4. 泛标题不能证明原群，因此父子记录明确保留 `source_group_verified=false` 和 `identity_binding='explicit_local_parent_id'`。来自 computer-use 的快照可以只有进程名及窗口 ID；不会伪造其未提供的 PID。
5. 空行/虚拟化哨兵不会变成消息；记录 `incomplete_reasons`。仅读取已观察的加载列表，不以已见到列表顶部/底部作为完整历史证明。
6. 只剥离正文最末尾明确的完整日期和时间，记录时区 `+08:00`，保留 `raw_text` 以审计；正文中的落款日期和活动日期保留。没有完整发送时间时继续使用 `sent_at=null`。
7. 文件和图片保留卡片证据、待展开标记，不读取未下载附件。JSONL 输入保留 `children` 与 `attachments`，并递归检查群白名单和数量/深度。

本轮合并窗口的真实内容验证由根任务使用用户当前 computer-use 窗口完成；新增 PowerShell `forward` 模式只做静态/单元验证，未在本轮调用另一个 UI 自动化后端。普通群窗口的原有 UIA 一次采集验证与上述新增模式的验证范围保持区分。
