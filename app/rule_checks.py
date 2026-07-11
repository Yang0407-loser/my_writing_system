"""写作前后规则检查 — 纯 Python 函数，零 LLM 成本。

pre_check:  提取本节必须包含的弧线事件
post_check: 验证生成文本是否包含必须事件
"""

import re


def pre_check(event_graph, section_num: int, sub_num: int = 0) -> dict:
    """从 EventGraph 提取本节必须体现的弧线事件。

    Returns:
        {"required": ["事件1", "事件2"], "prompt_text": "本节必须体现：..."}
    """
    if not event_graph:
        return {"required": [], "prompt_text": ""}

    required = []
    for e in event_graph._events.values():
        if e.type == "arc_milestone" and e.section == section_num:
            if sub_num == 0 or e.subsection == sub_num:
                required.append(e.description)

    if not required:
        return {"required": [], "prompt_text": ""}

    prompt_text = "## 本节必须体现的情节\n"
    for i, ev in enumerate(required, 1):
        prompt_text += f"{i}. {ev}\n"
    return {"required": required, "prompt_text": prompt_text}


def post_check(generated_text: str, required_events: list[str]) -> dict:
    """检查生成文本是否包含必须事件（关键词模糊匹配）。

    Returns:
        {"missing": ["事件1"], "pass": bool, "warnings": ["未体现: 事件1"]}
    """
    if not required_events:
        return {"missing": [], "pass": True, "warnings": []}

    missing = []
    for ev in required_events:
        # 提取事件关键词（2-4字中文词组）
        keywords = re.findall(r'[一-鿿]{2,4}', ev)
        if not keywords:
            continue
        # 至少 50% 的关键词出现在文本中
        found = sum(1 for kw in keywords if kw in generated_text)
        if found < len(keywords) * 0.5:
            missing.append(ev)

    warnings = [f"⚠ 未体现弧线事件: {ev}" for ev in missing] if missing else []
    return {
        "missing": missing,
        "pass": len(missing) == 0,
        "warnings": warnings,
    }


# ═══════════════════════════════════════════════════════════
# v0.9: 大纲锁强制校验
# ═══════════════════════════════════════════════════════════

def check_outline_lock(generated_text: str, locked_nodes: list[dict]) -> dict:
    """检查已锁定大纲节点的关键事件是否在正文中出现。

    两层策略:
    1. 代码级: 提取 locked 节点的关键词, 50% 命中 → 通过
    2. LLM级: 代码级未通过 → 调 LLM 判断事件是否存在 (轻量, max_tokens=100)

    Returns: {passed: bool, violations: [{node_id, node_title, expected, note}]}
    """
    violations = []
    for node in locked_nodes:
        keywords = _extract_lock_keywords(node)
        if not keywords:
            continue
        code_hits = sum(1 for kw in keywords if kw in generated_text)
        if code_hits >= len(keywords) * 0.5:
            continue

        # 代码级未通过 → LLM 轻量判断
        result = _llm_check_event_presence(
            generated_text,
            node.get("description", "") or node.get("title", ""),
        )
        if not result.get("present"):
            violations.append({
                "node_id": node.get("id", ""),
                "node_title": node.get("title", ""),
                "expected": node.get("description", "") or node.get("title", ""),
                "note": result.get("note", ""),
            })

    return {"passed": len(violations) == 0, "violations": violations}


def _extract_lock_keywords(node: dict) -> list[str]:
    text = (node.get("title", "") + " " + node.get("description", "")).strip()
    if not text:
        return []
    words = re.findall(r"[一-鿿]{2,4}", text)
    return list(set(words))[:5]


def _llm_check_event_presence(text: str, event_desc: str) -> dict:
    try:
        from ..utils.llm_client import get_llm_client
        from ..utils.json_parser import parse_json
        llm = get_llm_client()
        resp = llm.chat_completion(
            [{"role": "system", "content": "你是精确的文本检查工具。只回答 JSON。"},
             {"role": "user", "content": (
                 f'正文中是否明确描述了以下事件?\n'
                 f'事件: {event_desc}\n'
                 f'正文: {text[:1000]}\n'
                 f'回答: {{"present": true/false, "note": "一句话说明"}}'
             )}],
            temperature=0, max_tokens=100, json_mode=True,
        )
        result = parse_json(resp)
        return result if isinstance(result, dict) else {"present": False, "note": ""}
    except Exception:
        return {"present": False, "note": "LLM 检查失败"}
