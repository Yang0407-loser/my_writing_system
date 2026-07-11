"""自动提取规则后验证 — 不依赖 LLM 置信度, 用代码规则二次确认。"""

import re

ACTION_WORDS = [
    "获得", "持有", "使用", "拿出", "递给", "接过", "丢失", "毁坏",
    "握着", "拔出", "收入", "取出", "装备", "吞下", "注入",
    "携带", "佩戴", "赠予", "交换", "夺取", "捡起", "放下",
]

LOC_MARKERS = [
    "在", "来到", "抵达", "进入", "离开", "穿过", "位于", "前往",
    "山门", "城内", "府邸", "宫殿", "洞府", "森林", "沙漠",
    "广场", "大殿", "密室", "山谷", "山顶", "海岸", "村庄",
]


def validate_extracted_entity(entity: dict, full_text: str) -> dict:
    """规则后验证: 返回 {accepted: bool, reason: str}"""
    name = entity.get("name", "")
    etype = entity.get("type", "")

    if not name or len(name) < 2:
        return {"accepted": False, "reason": "名称过短"}

    if etype == "character":
        count = full_text.count(name)
        if count < 2:
            return {"accepted": False, "reason": f"'{name}' 仅出现 {count} 次, 可能是误提取"}

    elif etype == "item":
        has_action = any(aw in full_text for aw in ACTION_WORDS)
        if not has_action:
            return {"accepted": False, "reason": f"'{name}' 未与持有/使用动词共现"}

    elif etype == "location":
        # 检查名字附近 (前后10字) 是否有位置标记
        idx = full_text.find(name)
        if idx < 0:
            return {"accepted": False, "reason": "名字不在正文中"}
        nearby = full_text[max(0, idx - 10):idx + len(name) + 10]
        has_marker = any(m in nearby for m in LOC_MARKERS)
        if not has_marker:
            return {"accepted": False, "reason": f"'{name}' 未与地点标记共现"}

    elif etype == "faction":
        # 势力需要 势力类后缀或前缀
        faction_suffixes = ["宗", "派", "门", "教", "会", "盟", "帮", "组织",
                           "殿", "阁", "宫", "府", "国", "族", "军"]
        has_suffix = any(name.endswith(s) or s in name for s in faction_suffixes)
        count = full_text.count(name)
        if not has_suffix and count < 3:
            return {"accepted": False, "reason": f"'{name}' 无势力特征词且仅出现 {count} 次"}

    return {"accepted": True, "reason": ""}


def validate_batch(entities: list[dict], full_text: str) -> tuple[list[dict], list[dict]]:
    """批量验证, 返回 (confirmed, suggested)"""
    confirmed = []
    suggested = []
    for entity in entities:
        result = validate_extracted_entity(entity, full_text)
        if result["accepted"]:
            confirmed.append(entity)
        else:
            entity["reject_reason"] = result["reason"]
            suggested.append(entity)
    return confirmed, suggested
