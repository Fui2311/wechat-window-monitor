# 自动发现策略静态核查报告

**审查类型**：静态代码审查（仅读文件，无运行时执行）
**审查日期**：2026-10-22
**审查范围**：以下四个文件，逐一实际读取，未凭空推断

| 文件 | 实际读取行数 |
|------|------------|
| `wechat_tracker/console/discovery_policy.py` | 20 行 |
| `wechat_tracker/console/queries.py` | 291 行 |
| `wechat_tracker/console/discovery.py` | 91 行 |
| `docs/console-usage.md` | 50 行 |

---

## 一、快照/隐藏目录过滤规则

### 源码（`discovery_policy.py` L6–8）

```python
def ignored_discovery_path(relative: Path) -> bool:
    return any(part.startswith(".") or any(word in part.lower()
               for word in ("backup", "before", "previous", "last-good")) for part in relative.parts)
```

**实际行为**：
- 对 `relative.parts` 中的**每一层**路径组件做检查。
- 任意组件以 `.` 开头 → 排除（覆盖隐藏目录，包括 `.git`、`.DS_Store` 等）。
- 任意组件小写含 `backup`、`before`、`previous`、`last-good` 之一 → 排除。
- 参数名为 `relative`，表明调用方须传相对路径；函数本身不做 resolve/absolute。

### 文档（`console-usage.md` L14）

> 自动发现跳过带 backup、before、previous、last-good 标记的快照和隐藏子目录；规则相对于发现根判断，不因项目上层目录名称排除历史。

**一致性判断**：✅ 完全一致。文档提到的四个关键词与代码 `("backup", "before", "previous", "last-good")` 逐字吻合；隐藏子目录（`.` 前缀）也在代码中体现。

---

## 二、相对根判断

### `discovery.py` 中的调用（L43）

```python
if ignored_discovery_path(sub.relative_to(sdir)):
    continue
```

`sdir` 是 `search_dirs` 中的当前搜索根，`sub` 是候选服务目录；传入 `relative_to(sdir)` 确保是相对路径。

### `queries.py` 中的调用（L160）

```python
for sqlite_path in database_candidates(root):
```

`database_candidates`（`discovery_policy.py` L11–20）内部：

```python
for directory, children, files in os.walk(root, topdown=True, followlinks=False):
    folder = Path(directory)
    children[:] = sorted(name for name in children if not ignored_discovery_path(Path(name)))
    ...
    if ... not ignored_discovery_path(Path(name)) ...:
```

这里对 `children` 过滤时传入的是 `Path(name)`，其中 `name` 是 `os.walk` 返回的**单层目录名**，等价于相对于当前层级的单分量路径，符合"相对根判断"语义。对文件名的 `ignored_discovery_path(Path(name))` 亦同理（单分量）。

> [!NOTE]
> `database_candidates` 对目录分量过滤的逻辑是：剪掉含快照词的**子目录**后，`os.walk` 不再递归进入，因此上层根目录名称（如项目目录名叫 `backup-project`）**不会**影响该根内部的发现——`relative_to` 已在 `discovery.py` 侧保证；`database_candidates` 侧直接用单分量 `name`，同样只看子目录自身名称，不看祖先。

**与文档的一致性**：✅ 文档"规则相对于发现根判断，不因项目上层目录名称排除历史"与两处调用方式均一致。

---

## 三、明确活动库/服务根的保留

### `queries.py` `_list_databases`（L130–215）

1. **活动库先置**（L135–151）：`active_db = Path(active_data_dir).resolve() / "tracker.sqlite3"`，无论是否存在都先 `append` 到结果，并加入 `seen`。不经过 `ignored_discovery_path`。
2. **服务根扫描**（L154–213）：
   - `search_roots` 包含 `Path(active_data_dir).resolve().parent`（活动库父目录）、`PROJECT_ROOT / "state"` 以及所有传入 `services` 的 `data_dir`。
   - 扫描时通过 `database_candidates(root)` 过滤快照，再用 `seen` 去重防止活动库被重复列出。
   - **服务库标签覆盖**（L201–203）：若某条路径匹配 `service_by_db` 且 `running=True`，标签改为 `"运行中服务库 - ..."` 并设 `is_live_service=True`。

### `discovery.py` `_list_services`（L10–91）

1. **当前服务先置**（L14–22）：`current_dir = Path(active_config["data_dir"]).resolve()`，直接 append，加入 `seen_dirs`。不走 `ignored_discovery_path`。
2. **其他目录扫描**（L31–58）：先经 `ignored_discovery_path(sub.relative_to(sdir))` 过滤，排除快照；`seen_dirs` 去重。
3. **LaunchAgents**（L60–89）：读 plist 找 `--config` 路径，若 `la_dir` 已在 `seen_dirs` 则只补填 `config_path`，不重复添加。

### 文档（`console-usage.md` L14）

> 当前活动库和明确识别的服务根按配置保留，过滤不删除文件。

**一致性判断**：✅ 代码中活动库/当前服务均在扫描前先加入 `seen`/`seen_dirs`，不受 `ignored_discovery_path` 过滤；服务根通过 `services` 参数显式传入或从 LaunchAgents 配置读取，均不经过快照过滤逻辑。与文档描述一致。

---

## 四、使用说明与代码行为的额外核对

### 4.1 `followlinks=False`

`database_candidates` 使用 `os.walk(..., followlinks=False)`，且 L19 显式排除符号链接：

```python
and not candidate.is_symlink()
```

文档未明确提及符号链接处理，但无矛盾。

### 4.2 `.tmp.sqlite3` 排除

代码 L18：`not name.lower().endswith(".tmp.sqlite3")`，文档未提及但无矛盾。

### 4.3 只读连接

`_safe_db_connect`（L44）使用 `?mode=ro` URI，所有读取路径均为只读。文档（L12）"查看记录和管理服务"一节描述行为与此一致（选库不切换监控群，不写入）。

### 4.4 Demo 库识别

代码（`queries.py` L170）：`is_demo = "demo" in name_lower or "demo" in parent_lower`，文档（L42）"Demo 演示库"行为描述一致。

---

## 五、发现的问题

### 问题 1：`database_candidates` 对子目录过滤仅取单层名，多层嵌套时祖先层不再检查

**位置**：`discovery_policy.py` L15

```python
children[:] = sorted(name for name in children if not ignored_discovery_path(Path(name)))
```

`Path(name)` 是单分量，`ignored_discovery_path` 的 `relative.parts` 只有一个元素，等价于只检查直接子目录名。这是正确行为——`os.walk` 的 `topdown=True` 加 `children[:]` 剪枝已经确保被排除目录不再递归，因此不会漏过深层快照目录。

**结论**：逻辑正确，无阻断问题。此处记录是为了消除可能的阅读疑虑。

### 问题 2：`discovery.py` `search_dirs` 深度限制为两层（`glob("*/service.json")` + `glob("*/*/service.json")`）

**位置**：`discovery.py` L35

```python
candidates = list(sdir.glob("*/service.json")) + list(sdir.glob("*/*/service.json"))
```

仅发现 `sdir/*/service.json` 和 `sdir/*/*/service.json`，第三层及更深不被发现。文档未声明发现深度，无文档不一致，但这是一个**潜在遗漏风险**（若数据目录嵌套超过两层则不被发现）。**非阻断问题，建议在文档或代码注释中明确发现深度上限。**

### 问题 3：`_list_databases` 搜索根包含 `active_data_dir` 的父目录，但 `_list_services` 不对称包含

**位置**：`queries.py` L154 vs `discovery.py` L24–29

`queries.py` 搜索根：`active_data_dir` 父目录 + `PROJECT_ROOT/state` + 各服务 `data_dir`。
`discovery.py` 搜索根：`PROJECT_ROOT/state` + `current_dir.parent`（若不等于 state_root）+ 额外条件 `current_dir.parent.parent.name == "state"` 时添加 `parent.parent`。

两者的搜索根构造逻辑不完全对称，但文档并未承诺两个函数使用相同的搜索根。**无文档不一致，属于实现细节差异，建议评估是否需要对齐以避免数据库列表与服务列表发现范围不一致。非阻断问题。**

---

## 六、阻断问题

**无阻断问题。**

自动发现过滤规则、相对根判断、活动库/服务根保留逻辑与 `docs/console-usage.md` 中的使用说明在核查范围内均一致，未发现逻辑错误或文档误导。

---

## 七、建议（非阻断）

| 编号 | 建议 | 对应位置 |
|------|------|---------|
| B-1 | 在 `discovery.py` 注释中明确"最多搜索两层深度"，避免使用者误以为深层目录会被发现 | `discovery.py` L35 |
| B-2 | 评估 `_list_databases` 与 `_list_services` 的搜索根是否需要对齐，或在文档注明二者范围差异 | `queries.py` L154, `discovery.py` L24–29 |
| B-3 | 文档可补充"符号链接数据库不被发现"，与代码 `not candidate.is_symlink()` 保持显式对应 | `discovery_policy.py` L19, `docs/console-usage.md` |

---

*报告由静态审查生成；未执行任何命令、脚本、网络请求或数据库写操作。Codex 独占产品代码，本报告不修改任何源码文件。*
