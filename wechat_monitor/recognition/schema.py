"""Finite, auditable recognition rules. Generated rules are data, never code."""

import json

MODES = {"local", "llm"}
FIELDS = {"text", "sender", "kind", "attachment"}
OPS = {"contains", "equals", "starts_with", "ends_with"}


def text(value, name, limit=4000, required=True):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (required and not value.strip())
    ):
        raise ValueError(
            f"{name} 必须为{'非空' if required else ''}文本（最多{limit}字符）"
        )
    return value.strip()


def validate_rule(value, *, mode=None, target=None):
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "mode",
        "target",
        "instructions",
        "match",
    }:
        raise ValueError(
            "规则必须包含 schema_version / mode / target / instructions / match"
        )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["mode"] not in MODES
    ):
        raise ValueError("不支持的规则版本或识别模式")
    if mode is not None and value["mode"] != mode:
        raise ValueError("规则模式与窗口配置不一致")
    objective = text(value["target"], "识别目标")
    if target is not None and objective != target.strip():
        raise ValueError("规则目标已过期，请按当前目标重新生成")
    instructions = text(
        value["instructions"], "语义规则", 12000, value["mode"] == "llm"
    )
    match = value["match"]
    if not isinstance(match, dict) or set(match) != {"any", "all", "none"}:
        raise ValueError("本地规则须有 any / all / none 条件组")
    for name, clauses in match.items():
        if not isinstance(clauses, list) or len(clauses) > 40:
            raise ValueError("每组最多40个条件")
        for clause in clauses:
            if not isinstance(clause, dict) or set(clause) != {"field", "op", "value"}:
                raise ValueError("每个条件须为 field / op / value")
            if clause["field"] not in FIELDS or clause["op"] not in OPS:
                raise ValueError("不支持的条件字段或运算符")
            text(clause["value"], "条件值", 500)
    if value["mode"] == "local" and not (match["any"] or match["all"]):
        raise ValueError("本地规则至少需要一个正向匹配条件")
    # A JSON copy prevents callers from mutating an already validated rule.
    return json.loads(
        json.dumps(
            dict(value, target=objective, instructions=instructions), ensure_ascii=False
        )
    )


def evidence_text(message, depth=0):
    if not isinstance(message, dict) or depth > 6:
        return ""
    parts = [message.get("text", "")]
    for key in ("children", "attachments"):
        for child in message.get(key, []):
            if not isinstance(child, dict):
                continue
            parts.extend([child.get("name", ""), evidence_text(child, depth + 1)])
    return "\n".join(part for part in parts if isinstance(part, str) and part)
