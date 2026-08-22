"""整句证据窗口（2026-07-27）：短引定位、整句作证。

背景（Demo #5 / 任务 be663720 payload 取证）：成因一 = 命名主语与 20 字
短引联合不可满足（etm 7 + uoec 4 = 11/23 拒绝）。中文正文大量代词与承前
省略，角色名往往在句中而不在短引内；S1.1 七条引文 7/7 逐字命中却 6/7 死
于语义层。本批把证据 span/excerpt 从短引扩展为短引所在完整句——仍为原文
逐字、span 精确、validator 的 text[start:end]==excerpt 一致性天然保持。

诚实边界（负向测试锁定）：代词句（句内无角色名）仍被拒——属已记录的
代词绑定问题域，本批不解决、不放宽。
"""

from app.writing.handover_contract_v2 import (
    build_handover_sources,
    compile_next_boundary,
)
from app.writing.handover_contract_v21 import (
    MAX_EVIDENCE_EXCERPT,
    _sentence_bounds,
    build_compact_source_registry,
    restore_and_validate_v22,
)


CURRENT = {
    "subsection": 1,
    "title": "对街",
    "description": "苏染与程砚的隔街往来",
    "key_points": ["白瓷杯"],
}


def _fixture(text):
    current = dict(CURRENT)
    current["_section"] = 1
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline=current,
        next_outline=None,
        arc_milestones=(),
    )
    registry = build_compact_source_registry(sources, arc_milestones=())
    boundary = compile_next_boundary(
        section=1, subsection=1, current_outline=current, next_outline=None
    )
    return registry, boundary


def _run_s(text, quote, phrase, category="os"):
    registry, boundary = _fixture(text)
    return restore_and_validate_v22(
        {"v": "2.2", "s": [[0, quote, category, "c", "c", phrase]], "o": [], "f": [], "a": []},
        registry=registry,
        next_boundary=boundary,
    )


# ---------------------------------------------------------------- 句边界确定性


def test_sentence_bounds_mid_text():
    text = "第一句结束。苏染从桶里抽出一枝洋桔梗，放在门口。最后一句。"
    quote = "从桶里抽出一枝洋桔梗"
    start = text.find(quote)
    s, e = _sentence_bounds(text, start, start + len(quote))
    assert text[s:e] == "苏染从桶里抽出一枝洋桔梗，放在门口。"


def test_sentence_bounds_at_text_edges():
    text = "苏染把杯子洗干净放回窗台"  # 无句末标点、无前句
    quote = "杯子洗干净"
    start = text.find(quote)
    s, e = _sentence_bounds(text, start, start + len(quote))
    assert (s, e) == (0, len(text))
    assert text[s:e] == text


def test_sentence_bounds_skips_previous_closing_quote():
    text = "她说：“明天见。”程砚把门关上了。后面一句。"
    quote = "程砚把门关上"
    start = text.find(quote)
    s, e = _sentence_bounds(text, start, start + len(quote))
    assert text[s:e] == "程砚把门关上了。"


def test_sentence_bounds_includes_trailing_closing_quote():
    text = "前一句。程砚低声说：“杯子我收下了。”下一句开始。"
    quote = "杯子我收下了"
    start = text.find(quote)
    s, e = _sentence_bounds(text, start, start + len(quote))
    assert text[s:e] == "程砚低声说：“杯子我收下了。”"


def test_newline_is_boundary_but_not_included():
    text = "苏染把花桶收进店里\n程砚在对街看着。"
    quote = "把花桶收进店里"
    start = text.find(quote)
    s, e = _sentence_bounds(text, start, start + len(quote))
    assert text[s:e] == "苏染把花桶收进店里"


# ---------------------------------------------------------------- 恢复链行为


def test_demo5_mirror_named_subject_in_sentence_now_accepted():
    """Demo #5 成因一的镜像：主语名在句中、不在 20 字短引内——旧契约拒
    （evidence_text_mismatch），整句证据下应全收。"""
    text = "前情提要一句。苏染蹲在门口，从桶里抽出一枝白色洋桔梗。后续一句。"
    result = _run_s(text, "从桶里抽出一枝白色洋桔梗", "苏染|抽出|洋桔梗")
    assert result.accepted_claim_count == 1
    assert not result.rejection_counts
    evidence = result.contract.end_state.claims[0].evidence[0]
    assert evidence.excerpt == "苏染蹲在门口，从桶里抽出一枝白色洋桔梗。"


def test_pronoun_sentence_still_rejected_honest_boundary():
    """负向锁定：句内只有代词、无角色名——仍 evidence_text_mismatch。
    代词绑定不在本批范围，不得静默放宽。"""
    text = "前情提要一句。她蹲在门口，从桶里抽出一枝白色洋桔梗。后续一句。"
    result = _run_s(text, "从桶里抽出一枝白色洋桔梗", "苏染|抽出|洋桔梗")
    assert result.accepted_claim_count == 0
    assert result.rejection_counts.get("evidence_text_mismatch") == 1


def test_open_event_component_in_sentence_now_accepted():
    """uoec 同构修复：o 项组成部分对句校验。"""
    text = "开场一句。程砚答应明天把包裹转交给苏染，然后回了店里。结尾一句。"
    registry, boundary = _fixture(text)
    result = restore_and_validate_v22(
        {
            "v": "2.2",
            "s": [],
            "o": [[0, "明天把包裹转交给苏染", "o", "程砚|转交|包裹"]],
            "f": [],
            "a": [],
        },
        registry=registry,
        next_boundary=boundary,
    )
    assert len(result.contract.open_events) == 1
    assert not result.rejection_counts


def test_component_absent_from_whole_sentence_still_rejected():
    text = "开场一句。他答应明天把东西送过去。结尾一句。"
    registry, boundary = _fixture(text)
    result = restore_and_validate_v22(
        {
            "v": "2.2",
            "s": [],
            "o": [[0, "明天把东西送过去", "o", "程砚|转交|包裹"]],
            "f": [],
            "a": [],
        },
        registry=registry,
        next_boundary=boundary,
    )
    assert len(result.contract.open_events) == 0
    assert result.rejection_counts.get("unsupported_open_event_component") == 1


def test_oversized_sentence_falls_back_to_quote_only():
    """句长超过 MAX_EVIDENCE_EXCERPT（140）时回退为短引本身。"""
    long_sentence = "苏染把洋桔梗摆在窗台上" + "，又数了一遍花瓣" * 20 + "。"
    assert len(long_sentence) > MAX_EVIDENCE_EXCERPT
    text = "前一句。" + long_sentence + "后一句。"
    result = _run_s(text, "把洋桔梗摆在窗台上", "苏染|摆|洋桔梗")
    # 回退后 excerpt 是短引本身：主语名不在其中 → 仍被语义层拒绝（诚实回退，
    # 不静默产出超长证据）
    assert result.accepted_claim_count == 0
    assert result.rejection_counts.get("evidence_text_mismatch") == 1


def test_evidence_consistency_invariant_for_validator():
    """validator 一致性不变式：excerpt == source.text[start:end] 且 ≤140。"""
    text = "前情一句。苏染把白瓷杯放回窗台，顺手带上了门。收尾一句。"
    result = _run_s(text, "把白瓷杯放回窗台", "苏染|放回|白瓷杯")
    assert result.accepted_claim_count == 1
    evidence = result.contract.end_state.claims[0].evidence[0]
    assert text[evidence.start:evidence.end] == evidence.excerpt
    assert len(evidence.excerpt) <= MAX_EVIDENCE_EXCERPT
