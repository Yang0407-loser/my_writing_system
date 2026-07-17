"""交接笔记穿透率 —— 检查前一节的 handover 关键词在下一节正文中的命中情况。

零 LLM 调用，纯关键词提取 + 匹配。
"""

import re
import jieba


def _extract_keywords(text: str, min_len: int = 2, max_len: int = 6) -> set[str]:
    """从文本中提取有意义的实词作为关键词。"""
    if not text or text in ("无", "（无）"):
        return set()

    words = jieba.cut(text)
    keywords = set()
    for w in words:
        w = w.strip()
        if min_len <= len(w) <= max_len and re.search(r'[一-鿿]', w):
            keywords.add(w)
    return keywords


def compute_handover_penetration(
    handover_chain: list[dict],
    section_texts: dict[int, str],
) -> dict:
    """计算交接笔记穿透率。

    Args:
        handover_chain: 交接链 [{from_section, to_section, foreshadowing, character_state, open_threads}, ...]
        section_texts: {section_num: full_section_text}

    Returns:
        {
            "per_link": [{from_section, to_section, kw_count, hit_count, penetration}, ...],
            "overall_penetration": float,  # 平均穿透率
            "total_keywords": int,
            "total_hits": int,
            "verdict": "优秀" | "良好" | "一般" | "无效",
        }
    """
    if not handover_chain:
        return {
            "per_link": [],
            "overall_penetration": 0.0,
            "total_keywords": 0,
            "total_hits": 0,
            "verdict": "无交接数据",
        }

    per_link = []
    total_kw = 0
    total_hits = 0

    for h in handover_chain:
        from_sec = h.get("from_section", 0)
        to_sec = h.get("to_section", from_sec + 1)
        target_text = section_texts.get(to_sec, "")

        # 拼接交接笔记中所有非空字段
        handover_text = " ".join(
            h.get(k, "") or ""
            for k in ("foreshadowing", "character_state", "open_threads")
            if h.get(k) and h[k] not in ("无", "（无）")
        )

        keywords = _extract_keywords(handover_text)
        kw_count = len(keywords)
        if kw_count == 0:
            per_link.append({
                "from_section": from_sec,
                "to_section": to_sec,
                "kw_count": 0,
                "hit_count": 0,
                "penetration": None,
            })
            continue

        hit_count = sum(1 for kw in keywords if kw in target_text)
        penetration = round(hit_count / kw_count, 3)

        per_link.append({
            "from_section": from_sec,
            "to_section": to_sec,
            "kw_count": kw_count,
            "hit_count": hit_count,
            "penetration": penetration,
        })
        total_kw += kw_count
        total_hits += hit_count

    overall = round(total_hits / max(total_kw, 1), 3)

    if overall >= 0.7:
        verdict = "优秀"
    elif overall >= 0.5:
        verdict = "良好"
    elif overall >= 0.3:
        verdict = "一般"
    elif total_kw == 0:
        verdict = "无交接数据"
    else:
        verdict = "无效"

    return {
        "per_link": per_link,
        "overall_penetration": overall,
        "total_keywords": total_kw,
        "total_hits": total_hits,
        "verdict": verdict,
    }
