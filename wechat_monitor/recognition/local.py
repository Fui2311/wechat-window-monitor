"""Deterministic, bounded string DSL; no model or network dependency."""

from .schema import evidence_text, validate_rule


class LocalCore:
    def recognize(self, message, rule):
        rule = validate_rule(rule, mode="local")
        fields = {
            "text": evidence_text(message),
            "sender": message.get("sender") or "",
            "kind": message.get("kind") or "text",
            "attachment": "\n".join(
                a.get("name", "") for a in message.get("attachments", [])
            ),
        }

        def matches(clause):
            haystack = fields[clause["field"]].casefold()
            needle = clause["value"].casefold()
            return {
                "contains": lambda: needle in haystack,
                "equals": lambda: needle == haystack,
                "starts_with": lambda: haystack.startswith(needle),
                "ends_with": lambda: haystack.endswith(needle),
            }[clause["op"]]()

        groups = rule["match"]
        positive = (not groups["any"] or any(map(matches, groups["any"]))) and all(
            map(matches, groups["all"])
        )
        matched = positive and not any(map(matches, groups["none"]))
        hits = [c["value"] for c in groups["any"] + groups["all"] if matches(c)]
        return {
            "matched": matched,
            "reason": "匹配：" + "、".join(hits) if matched else "未满足本地规则",
            "evidence": evidence_text(message)[:2000] if matched else "",
            "core": "local",
        }
