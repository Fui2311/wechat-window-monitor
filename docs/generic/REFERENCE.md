# 通用识别规则与 API

## 规则 schema v1

```json
{
  "schema_version": 1,
  "mode": "local",
  "target": "提醒项目构建失败",
  "instructions": "仅提醒明确构建失败；这套本地规则只检查关键词，不理解讽刺或否定语义",
  "match": {
    "any": [{"field": "text", "op": "contains", "value": "构建失败"}],
    "all": [],
    "none": [{"field": "sender", "op": "equals", "value": "测试机器人"}]
  }
}
```

三组条件：any至少一个成立（空组不约束），all全部成立，none全部不成立。本地模式要求any或all至少有一个条件，防止空规则全命中。每组最多40个条件、每个值最多500字符，大小写不敏感。

字段text包含已读取的父/子消息正文与附件名；sender只匹配父消息发送者，kind匹配父消息类型，attachment匹配附件名称。运算符contains/equals/starts_with/ends_with，不支持正则或任意代码。需要复杂语义应使用llm模式。llm模式instructions不能为空，match可以为空；输出的命中证据必须逐字存在于已读取正文/附件名中。消息过长、输出不合法或无证据会失败留存，不静默伪装成未命中。

规则版本不可变。编辑后保存新版本，启用指针可回到与当前目标/模式相同的旧版本。改目标/模式使旧版本不可启用，历史保留。记录携带实际rule_id和core以供审计。

## API

仅同源localhost HTTP，本机端口默认8765。修改操作须POST application/json且正文不超过256KB。不提供远程身份认证，不可对公网代理。

| 接口 | 方法与用途 |
| --- | --- |
| /api/bootstrap | GET，窗口清单、运行状态/统计、公共模型设置、默认原生路径 |
| /api/model | POST，base_url/model/timeout/api_key_env/api_key/clear_key；密钥不返回 |
| /api/windows | POST，新建name/target/mode/interval/include_existing/capture，保持停止 |
| /api/windows/:id | POST，保存完整配置与revision；拒绝过期或运行中修改 |
| /api/windows/:id/rules | GET全部版本；POST {spec}保存编辑的新版本 |
| /api/windows/:id/generate | POST {}，调用一次模型生成并保存草稿 |
| /api/windows/:id/rule-prompt | GET，仅local；返回prompt与当前revision，不调用模型、不保存规则 |
| /api/windows/:id/import-rule | POST {content,revision}，仅local；导入完整JSON或单个JSON代码块，保存origin=imported的新草稿；不启用、不启动 |
| /api/windows/:id/activate | POST {rule_id}，明确启用版本，运行时拒绝变更 |
| /api/windows/:id/test | POST {spec,message}，无持久化识别测试；语义模式会调用模型 |
| /api/windows/:id/probe | POST {}，只读精确窗口就绪检查 |
| /api/windows/:id/start | POST {}，开始独立轮询，需有活动规则 |
| /api/windows/:id/stop | POST {}，请求停止，当前调用完成后成为stopped |
| /api/windows/:id/retry | POST {}，失败记录改为pending，由运行时按当前规则处理 |
| /api/windows/:id/records | GET，limit=1..100、before正整数游标、q文本、category=all/matched/error |

捕获配置允许source=macos_ax/windows_uia/jsonl、mac_helper、jsonl_path、mac_header_identifier、mac_list_identifier和process_name。其余字段拒绝保存，避免界面显示保存成功但底层实际忽略配置。

导入 content 最多100000字符，同时受请求正文256KB上限约束。必须符合完整 schema v1，模式与当前窗口目标一致；拒绝重复JSON字段、NaN/Infinity、解释文字、代码、额外字段及空正向条件。revision 必须为当前窗口的整数版本，过期则返回400且不写规则库。成功后使用已有 test / activate 接口试测并明确启用。导出只包含目标、规则格式与约束，不读取模型设置或聊天记录；剪贴板由前端写入，失败时保留可手动复制的文本。

状态：starting/monitoring/waiting/recognition_error/stopping/stopped。只有running=true且monitoring为绿点。状态不可用时灰点且显示连接异常，不能用旧缓存声称当前监控正常。停止等待中仍运行，设置保存保持禁用。

## 开发目录

Vue组件独立维护；HTTP传输和展示函数有纯契约测试，组件卸载取消请求。前端npm build产物由资源路由提供，禁止任意文件读取。历史游标页不自动跳回最新；“查看最新”明确重置游标。
