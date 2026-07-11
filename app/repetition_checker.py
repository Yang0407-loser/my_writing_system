"""重复检测 —— jieba 分词 + TF-IDF + 轻量 LLM 节拍验证。

链路：
1. jieba 分词 + 停用词过滤 → TF-IDF 余弦相似度
2. 相似度 ≥ 0.85 → 轻量 LLM 判断情节是否有新进展（50 tokens）
3. 低相似度或 LLM 判有进展 → 放行；LLM 判无进展 → 触发重试
"""

import re
import logging
import jieba
from typing import Optional

logger = logging.getLogger("writing_system.repetition")

# ═══════════════════════════════════════════════════════════
# 分词
# ═══════════════════════════════════════════════════════════

STOP_WORDS = set("""
的了在是我有和就不人他这都也一要那它后好到上中下说为
把被让给从对与但而或虽然因为所以如果之以其所于即
当同时突然忽然似乎然而然后于是接着此时这时只见只听见
一个什么可以没有他们自己知道已经还是只是出来起来时候
这个那个这样的话觉得看见听见
""".split())


def _tokenize_cn(text: str) -> list[str]:
    """jieba 分词 + 停用词过滤，只保留 ≥2 字的实词。"""
    tokens = jieba.lcut(text)
    return [t for t in tokens if len(t) >= 2 and t not in STOP_WORDS]


# ═══════════════════════════════════════════════════════════
# TF-IDF 向量化 + 余弦相似度
# ═══════════════════════════════════════════════════════════

def _build_tfidf_matrix(texts: list[str]):
    """简化的 TF-IDF 构建。返回 (tf_vectors, vocab_list)。"""
    from math import log

    tokenized = [_tokenize_cn(t) for t in texts]
    n_docs = len(tokenized)

    vocab = {}
    for tokens in tokenized:
        for t in tokens:
            vocab[t] = vocab.get(t, 0) + 1

    vocab = {w: c for w, c in vocab.items() if c >= 2}

    idf = {}
    for w in vocab:
        doc_count = sum(1 for tokens in tokenized if w in tokens)
        idf[w] = log((n_docs + 1) / (doc_count + 1)) + 1.0

    vocab_list = list(vocab.keys())
    tf_vectors = []
    for tokens in tokenized:
        tf = {}
        total = len(tokens) if tokens else 1
        for t in tokens:
            if t in vocab:
                tf[t] = tf.get(t, 0) + 1 / total
        vec = [tf.get(w, 0) * idf.get(w, 0) for w in vocab_list]
        tf_vectors.append(vec)

    return tf_vectors, vocab_list


def _cosine_sim(vec_a, vec_b) -> float:
    """余弦相似度。"""
    import math
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ═══════════════════════════════════════════════════════════
# 重复检测
# ═══════════════════════════════════════════════════════════

def check_repetition(
    sub_text: str,
    previous_texts: list[str],
    threshold: float = 0.85,
    lookback: int = 3,
) -> dict:
    """检查本节正文与前面小节的余弦相似度。

    Args:
        sub_text: 当前小节正文
        previous_texts: 前面所有小节的正文列表（按顺序）
        threshold: 相似度阈值，超过则进入 LLM 节拍检测
        lookback: 只检查最近 N 节

    Returns:
        {"repeated": bool, "max_similarity": float, "similar_section": int | None}
    """
    if not previous_texts or not sub_text:
        return {"repeated": False, "max_similarity": 0.0, "similar_section": None}

    recent = previous_texts[-lookback:] if len(previous_texts) > lookback else previous_texts
    start_idx = len(previous_texts) - len(recent)

    try:
        truncated = [sub_text[:2000]] + [t[:2000] for t in recent]
        vectors, _ = _build_tfidf_matrix(truncated)

        current_vec = vectors[0]
        max_sim = 0.0
        max_idx = None

        for i, vec in enumerate(vectors[1:], 1):
            sim = _cosine_sim(current_vec, vec)
            if sim > max_sim:
                max_sim = sim
                max_idx = start_idx + i - 1

        return {
            "repeated": max_sim > threshold,
            "max_similarity": round(max_sim, 3),
            "similar_section": max_idx,
        }
    except Exception as e:
        logger.warning(f"重复检测出错: {e}", exc_info=True)
        return {"repeated": False, "max_similarity": 0.0, "similar_section": None}


# ═══════════════════════════════════════════════════════════
# 轻量 LLM 情节节拍检测（方案 C：封装在模块内）
# ═══════════════════════════════════════════════════════════

def llm_check_beat_advancement(prev_text: str, curr_text: str) -> dict:
    """轻量 LLM 判断情节是否有新进展。

    仅在 TF-IDF ≥ 0.85 时由 check_subsection_quality 内部调用。
    max_tokens=50, json_mode。
    """
    try:
        from .utils.llm_client import get_llm_client
        from .utils.json_parser import parse_json

        llm = get_llm_client()
        system = (
            "你是精确的故事结构分析工具。只回答JSON。判断原则："
            "看结尾不看开头；有新信息=进展；状态变化=进展；故意呼应=进展；"
            "只有完全重复无新信息才是真重复。"
        )
        prompt = (
            f"上一段结尾：{prev_text[-300:]}\n"
            f"这一段内容：{curr_text[:600]}\n\n"
            f"这一段相比上一段，情节有新进展吗？（日常延续但有新变化也算进展）\n"
            f'回答JSON: {{"advanced": true/false, "what": "不超过8字简述"}}'
        )
        resp = llm.chat_completion(
            [{"role": "system", "content": system},
             {"role": "user", "content": prompt}],
            temperature=0, max_tokens=50, json_mode=True,
        )
        result = parse_json(resp)
        return result if isinstance(result, dict) else {"advanced": True, "what": ""}
    except Exception as e:
        logger.warning(f"LLM节拍检测失败: {e}", exc_info=True)
        return {"advanced": True, "what": ""}


# ═══════════════════════════════════════════════════════════
# 旧 LLM 推进判断（保留，供审阅阶段按需调用）
# ═══════════════════════════════════════════════════════════

def llm_check_advancement(
    prev_text: str,
    current_text: str,
    target_goal: str,
) -> dict:
    """LLM 判断本节是否推进了情节目标。max_tokens=100, temperature=0。"""
    try:
        from .utils.llm_client import get_llm_client
        from .utils.json_parser import parse_json

        llm = get_llm_client()
        prompt = (
            f"上一节最后一段：{prev_text[-400:]}\n"
            f"本节内容：{current_text[:600]}\n"
            f"本节目标：{target_goal}\n\n"
            f'回答JSON: {{"advanced": true/false, "reason": "一句话，不超过20字"}}'
        )
        resp = llm.chat_completion(
            [{"role": "system", "content": "你是精确的故事结构分析工具。只回答JSON。"},
             {"role": "user", "content": prompt}],
            temperature=0, max_tokens=100, json_mode=True,
        )
        result = parse_json(resp)
        return result if isinstance(result, dict) else {"advanced": True, "reason": ""}
    except Exception as e:
        logger.warning(f"LLM推进判断失败: {e}", exc_info=True)
        return {"advanced": True, "reason": "LLM检查失败，假定通过"}


# ═══════════════════════════════════════════════════════════
# 集成入口：供 Writer._generate_with_retry 调用
# ═══════════════════════════════════════════════════════════

def check_subsection_quality(
    sub_text: str,
    previous_texts: list[str],
    prev_sub_text: str = "",
    target_goal: str = "",
) -> dict:
    """综合质量检查入口。纯代码 → LLM 两级递进。

    链路：
    1. jieba + TF-IDF 与最近 3 节对比（阈值 0.85）
    2. 高相似度 → 轻量 LLM 判断情节节拍（50 tokens）
    3. 调用方只需看 result["pass"]

    Returns:
        {"pass": bool, "repetition": {...}, "beat_check": {...} | None}
    """
    rep = check_repetition(sub_text, previous_texts)

    if not rep["repeated"]:
        return {"pass": True, "repetition": rep, "beat_check": None}

    beat = llm_check_beat_advancement(prev_sub_text, sub_text)

    return {
        "pass": beat.get("advanced", True),
        "repetition": rep,
        "beat_check": beat,
    }
