"""AI痕迹检测 —— 高频词提取 + 套路模式匹配（零LLM成本）。"""

import re
from collections import Counter

# AI常见套路词库（对应Writer写作禁区8条）
AI_PATTERNS = [
    # 1. 心理直述
    "他感到", "她感到", "他感受到", "她感受到", "他意识到", "她意识到",
    "他心里", "她心里", "他暗想", "她暗想", "他心想", "她心想",
    "他猛然想起", "她猛然想起", "他忽然想到", "她忽然想到",
    "他明白", "她明白", "他突然明白", "她突然明白",
    # 2. 模板式神态
    "眼中闪过一丝", "眼底闪过一丝", "眼中闪过一抹", "眼底闪过一抹",
    "嘴角勾起", "嘴角微微", "嘴角泛起", "嘴角露出",
    "眸中", "眸光", "眼眸中", "眼瞳中",
    "眉头一皱", "眉头微皱", "眉头紧锁", "眉头皱了皱",
    "脸色一变", "脸色微变", "神色一变", "面色一变",
    "目光一闪", "眼神一闪", "瞳孔一缩", "瞳孔微缩",
    "嘴角抽搐", "眼角抽搐",
    # 3. 通用比喻/陈词
    r"像.{1,4}一样", r"如同.{1,4}一般", r"仿佛.{1,4}似的",
    "像蝴蝶", "如同被雷", "仿佛时间凝固", "仿佛一切都",
    r"一股\S{1,3}的", "莫名的", "一股莫名的",
    # 4. 机械过渡
    "随着时间的推移", "随着时间推移", "渐渐地", "渐渐的",
    "不知不觉中", "不知不觉间", "不知不觉地",
    "与此同时", "就在这时", "正在这时", "恰在此时",
    "就这样", "就这样地", "就这样的",
    # 5. 套话模板
    r"在这个充满.{2,10}的世界里",
    r".{1,5}不仅是一种.{2,8}，更是一种.{2,8}",
    r"在.{2,6}的过程中",
    r"这不仅仅是一次.{2,6}，更是",
    r"一切[，,]都.{2,10}",
    # 6. 空洞总结
    r"从此[，,].{2,10}开始了",
    r"从此[，,].{2,10}了新的",
    "新的旅程", "新的篇章", "新的一天",
    "这一切", "这一路", "这一战",
    "人生就是", "命运就是", "生活就是",
    # 7. 过度修饰标记
    r"的\S{1,2}[，,、]的\S{1,2}[，,、]的",  # 叠形容词: 苍凉的、破败的、布满青苔的
    r"无[与伦比以限尽穷]的", r"[极其]?[非是]?无比\S{1,3}的",
    r"极[为其之]?\S{1,3}的",
    # 8. 过度强调
    "深深地", "不由得", "不由自主地", "情不自禁地",
    "说来也奇怪", "说来奇怪", "也不知道", "谁也不",
    # 9. AI偏好句式
    r".+，仿佛.{3,15}，.+",  # "他站在窗前，仿佛看到了什么，..."
    r"或许[，,].+[，,].+[。.]",  # 模糊推测句式
]

# 各类模式对应的建议文案
PATTERN_ADVICE = {
    "心理直述": "用动作/对话替换：'他攥紧拳头' 替代 '他感到愤怒'",
    "模板式神态": "用人物独有习惯动作替换：'他用食指敲了两下桌面——这是他犹豫时的老习惯'",
    "通用比喻": "用角色所处环境的元素打比方：铁匠用铁、猎户用山",
    "机械过渡": "用具体事件推进时间：'第三天早上，他发现食物不够了'",
    "套话模板": "删掉或改写为具体描述",
    "空洞总结": "删除总结句，让故事自然收束",
    "过度修饰": "选最准确的一个形容词，其余删掉",
    "过度强调": "删掉修饰副词，让动词自己说话",
}


def detect_high_frequency(text: str, threshold: int = 5) -> list[dict]:
    """高频词检测 (2-4字中文词)。"""
    words = re.findall(r'[一-鿿]{2,4}', text)
    counter = Counter(words)
    results = []
    for word, count in counter.most_common(50):
        if count >= threshold:
            results.append({"word": word, "count": count, "density": round(count / max(len(words), 1) * 100, 2)})
    return results


def _classify_pattern(matched: str) -> str:
    """将匹配的模式归类。"""
    psych = ["感到", "感受到", "意识到", "心里", "暗想", "心想", "猛然想起", "忽然想到", "明白", "突然明白"]
    face = ["眼中闪过", "眼底闪过", "嘴角", "眸", "眉头", "脸色", "神色", "面色", "目光一闪", "眼神一闪", "瞳孔", "抽搐"]
    cliche = ["像", "如同", "仿佛", "一股", "莫名的"]
    transition = ["随着", "渐渐", "不知不觉", "与此同时", "就在这时", "正在这时", "恰在此时", "就这样"]
    template = ["在这个充满", "不仅是一种", "在", "这不仅仅是", "一切"]
    summary = ["从此", "新的", "这一", "人生就是", "命运就是", "生活就是"]
    overmod = ["的", "无比", "极其", "极"]
    emphasis = ["深深地", "不由得", "不由自主", "情不自禁", "说来也", "也不知道", "谁也不"]

    for p in psych:
        if p in matched: return "心理直述"
    for f in face:
        if f in matched: return "模板式神态"
    for c in cliche:
        if c in matched: return "通用比喻"
    for t in transition:
        if t in matched: return "机械过渡"
    for t in template:
        if t in matched: return "套话模板"
    for s in summary:
        if s in matched: return "空洞总结"
    for o in overmod:
        if o in matched: return "过度修饰"
    for e in emphasis:
        if e in matched: return "过度强调"
    return "其他"


def detect_ai_patterns(text: str) -> list[dict]:
    """AI套路模式匹配（含分类和建议）。"""
    findings = []
    for pattern in AI_PATTERNS:
        for m in re.finditer(pattern, text):
            start = max(0, m.start() - 20)
            end = min(len(text), m.end() + 20)
            matched = m.group()
            cat = _classify_pattern(matched)
            findings.append({
                "pattern": pattern if len(pattern) < 30 else pattern[:30] + "...",
                "match": matched,
                "category": cat,
                "advice": PATTERN_ADVICE.get(cat, ""),
                "position": m.start(),
                "context": text[start:end],
            })
    return findings


def analyze_text(text: str) -> dict:
    """综合分析：高频词 + AI痕迹 + 分类建议。"""
    high_freq = detect_high_frequency(text, threshold=4)
    ai_patterns = detect_ai_patterns(text)
    total_words = len(re.findall(r'[一-鿿]', text))

    # 按类别统计
    cat_counts = {}
    for p in ai_patterns:
        cat = p.get("category", "其他")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # 收集去重建议
    advices = []
    seen = set()
    for p in ai_patterns:
        if p.get("advice") and p["advice"] not in seen:
            advices.append({"category": p["category"], "advice": p["advice"]})
            seen.add(p["advice"])

    # 评分
    pattern_score = max(0, 10 - len(ai_patterns) * 0.8)
    freq_score = max(0, 10 - len([h for h in high_freq if h["count"] >= 8]) * 0.5)

    return {
        "total_chars": total_words,
        "high_frequency_words": high_freq,
        "ai_patterns": ai_patterns,
        "pattern_count": len(ai_patterns),
        "category_counts": cat_counts,
        "advices": advices,
        "ai_score": round((pattern_score + freq_score) / 2, 1),
        "verdict": "人类写作风格" if pattern_score + freq_score >= 16 else
                   "略有AI痕迹" if pattern_score + freq_score >= 12 else
                   "明显AI痕迹" if pattern_score + freq_score >= 8 else
                   "严重AI痕迹",
    }


def compare_subsections_trend(logs: list[dict]) -> dict:
    """对比最近小节的 AI 检测趋势。

    Args:
        logs: analyze_text() 结果的列表，每个元素含 ai_score, pattern_count, total_chars, category_counts

    Returns:
        {"trend_worse": bool, "warnings": list[str], "latest_score": float, "avg_score": float,
         "density_ratio": float, "dominant_category": str}
    """
    if not logs or len(logs) < 2:
        return {"trend_worse": False, "warnings": [], "latest_score": 10, "avg_score": 10,
                "density_ratio": 1.0, "dominant_category": ""}

    # 最近 N 条的对比基线
    recent = logs[-10:]
    latest = logs[-1]

    avg_score = sum(r.get("ai_score", 10) for r in recent) / len(recent)
    avg_density = sum(
        r.get("pattern_count", 0) / max(r.get("total_chars", 1), 1)
        for r in recent
    ) / len(recent)

    latest_density = latest.get("pattern_count", 0) / max(latest.get("total_chars", 1), 1)

    warnings = []

    # 趋势恶化检测
    if len(recent) >= 3 and latest.get("ai_score", 10) < avg_score - 1.5:
        warnings.append(f"AI得分下降: {latest['ai_score']:.1f} < 均值{avg_score:.1f}")

    if len(recent) >= 3 and latest_density > avg_density * 1.5:
        warnings.append(f"模式密度升高: {latest_density*1000:.1f}/千字 > 均值{avg_density*1000:.1f}")

    # 类别集中度
    cat = latest.get("category_counts", {})
    if cat:
        total = sum(cat.values()) or 1
        dominant = max(cat, key=cat.get)
        ratio = cat[dominant] / total
        if ratio > 0.5:
            warnings.append(f"类别集中: {dominant}占{ratio*100:.0f}%")

    return {
        "trend_worse": bool(warnings),
        "warnings": warnings,
        "latest_score": latest.get("ai_score", 10),
        "avg_score": round(avg_score, 1),
        "density_ratio": round(latest_density / max(avg_density, 0.0001), 1),
        "dominant_category": max(cat, key=cat.get) if cat else "",
    }
