"""V2.2 quote-anchored evidence: engineering gate with a prompt↔parser round trip.

Why V2.2 exists: demo #2 (2026-07-26, task 0e8513c7) rejected 33/37 items as
`invalid_span` with ZERO `evidence_text_mismatch` — the model's character
offsets were systematically out of bounds, proving offset citation is beyond
LLM capability. V2.2 replaces [start, end] with a 4-20 char verbatim quote;
the parser locates it via find() and rebuilds the exact span. Verbatim
exactness is unchanged: an unfindable quote rejects the item.

Institutionalized lesson from V2.1: every gate below feeds payloads shaped
exactly as the PROMPT instructs into the REAL parser, end to end through the
V2 validator — never hand-built shapes only.
"""

from app.utils.prompt_templates import HANDOVER_EXTRACTION_PROMPT_V22
from app.writing.handover_contract_v2 import (
    build_handover_sources,
    compile_next_boundary,
)
from app.writing.handover_contract_v21 import (
    HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS,
    HANDOVER_COMPACT_V22_MAX_OUTPUT_TOKENS,
    MAX_QUOTE_CHARS,
    MIN_QUOTE_CHARS,
    build_compact_source_registry,
    compact_payload_metrics,
    prompt_example_payload_v22,
    prompt_example_premise_v22,
    restore_and_validate_v22,
    typical_compact_payload_v22,
    worst_legal_compact_payload_v22,
)


PROSE = (
    "林晚在凌晨两点收到第二十版文案的驳回邮件。她盯着屏幕看了很久，"
    "然后打开一个新文档，写下辞职信。走出写字楼时，风把她的头发吹乱了。"
    "街角面包店的灯还亮着，麦子发酵的香气涌过来，她停住了脚步。"
    "林晚等待周野回应她的拍摄请求，保温杯里的红茶已经凉了。"
    "她把相机留在帆布袋里，决定先在书店台阶上坐一会儿。"
)


def _fixture():
    current = {
        "_section": 1, "subsection": 1, "title": "辞职之夜",
        "key_points": ["林晚辞职"], "description": "林晚裸辞当晚的经历",
    }
    following = {
        "_section": 1, "subsection": 2, "title": "三次蹲守",
        "key_points": ["蹲守面包店"], "description": "林晚连续蹲守周野",
    }
    sources = build_handover_sources(
        section=1, subsection=1, generated_text=PROSE,
        current_outline=current, next_outline=following, arc_milestones=[],
    )
    registry = build_compact_source_registry(sources)
    boundary = compile_next_boundary(
        section=1, subsection=1, current_outline=current, next_outline=following
    )
    return registry, boundary


def _run(registry, boundary, **lists):
    payload = {"v": "2.2", "s": [], "o": [], "f": [], "a": []}
    payload.update(lists)
    return restore_and_validate_v22(
        payload, registry=registry, next_boundary=boundary
    )


# ---------------------------------------------------------------------------
# round trip: prompt-literal payloads restore end to end
# ---------------------------------------------------------------------------


def test_quote_anchored_claim_restores_with_sentence_evidence():
    """短引是定位器；证据 span/excerpt 是短引所在完整句（2026-07-27 起）。"""
    registry, boundary = _fixture()
    quote = "林晚等待周野回应"
    sentence = "林晚等待周野回应她的拍摄请求，保温杯里的红茶已经凉了。"

    result = _run(
        registry, boundary,
        s=[[0, quote, "cs", "c", "c", "林晚|等待|回应"]],
    )

    assert result.accepted_claim_count == 1
    assert not result.rejection_counts
    claim = result.contract.end_state.claims[0]
    evidence = claim.evidence[0]
    expected_start = PROSE.find(sentence)
    assert (evidence.start, evidence.end) == (
        expected_start, expected_start + len(sentence)
    )
    assert evidence.excerpt == sentence
    assert quote in evidence.excerpt
    assert PROSE[evidence.start:evidence.end] == evidence.excerpt


def test_open_event_components_verified_inside_quote():
    registry, boundary = _fixture()

    result = _run(
        registry, boundary,
        o=[[0, "林晚等待周野回应", "o", "林晚|等待|回应"]],
    )

    assert len(result.contract.open_events) == 1
    assert not result.rejection_counts


def test_full_prompt_literal_payload_round_trips():
    """One payload per prompt spec line, all restoring together — the shape a
    compliant model would emit for this fixture (a omitted: v1 arcs carry no
    sources, so a compliant model emits an empty a list here)."""
    registry, boundary = _fixture()

    result = _run(
        registry, boundary,
        s=[[0, "红茶已经凉了", "os", "c", "c", "红茶|已经|凉了"]],
        f=[[0, "相机留在帆布袋里", "kf", "c", "c", "相机|留在|帆布袋"]],
        o=[[0, "林晚等待周野回应", "o", "林晚|等待|回应"]],
    )

    assert result.accepted_claim_count == 3
    assert not result.rejection_counts


def test_quote_with_surrounding_whitespace_is_normalized():
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, "  相机留在帆布袋里 ", "os", "c", "c", "相机|留在|帆布袋"]],
    )
    assert result.accepted_claim_count == 1


def test_quote_location_is_deterministic_first_occurrence():
    registry, boundary = _fixture()
    item = [0, "红茶已经凉了", "os", "c", "c", "红茶|已经|凉了"]

    first = _run(registry, boundary, s=[list(item)])
    second = _run(registry, boundary, s=[list(item)])

    ev1 = first.contract.end_state.claims[0].evidence[0]
    ev2 = second.contract.end_state.claims[0].evidence[0]
    assert (ev1.start, ev1.end) == (ev2.start, ev2.end)
    # 证据为短引所在整句；同一短引两次恢复的句边界完全一致
    assert ev1.start == PROSE.find("林晚等待周野回应她的拍摄请求")
    assert "红茶已经凉了" in ev1.excerpt


# ---------------------------------------------------------------------------
# rejection telemetry names the failure
# ---------------------------------------------------------------------------


def test_unfindable_quote_reports_quote_not_found():
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, "这句话不在原文里", "cs", "c", "c", "林晚|等待|回应"]],
    )
    assert result.rejection_counts == {"quote_not_found": 1}


def test_paraphrased_quote_is_rejected_not_fuzzy_matched():
    """逐字性不降:改写(哪怕语义等价)必须拒,不做模糊匹配。"""
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, "林晚等候周野回复", "cs", "c", "c", "林晚|等待|回应"]],
    )
    assert result.rejection_counts == {"quote_not_found": 1}


def test_quote_length_bounds_report_invalid_quote():
    registry, boundary = _fixture()
    too_short = _run(
        registry, boundary,
        s=[[0, "林晚", "cs", "c", "c", "林晚|等待|回应"]],
    )
    too_long = _run(
        registry, boundary,
        s=[[0, PROSE[: MAX_QUOTE_CHARS + 5], "cs", "c", "c", "林晚|等待|回应"]],
    )
    non_str = _run(
        registry, boundary,
        s=[[0, 12345, "cs", "c", "c", "林晚|等待|回应"]],
    )
    assert too_short.rejection_counts == {"invalid_quote": 1}
    assert too_long.rejection_counts == {"invalid_quote": 1}
    assert non_str.rejection_counts == {"invalid_quote": 1}
    assert MIN_QUOTE_CHARS == 4 and MAX_QUOTE_CHARS == 20


def test_old_span_shape_is_rejected_as_wrong_arity():
    """A model emitting the retired v2.1 7-element span format must fail
    loudly on shape, not be silently misread."""
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, 10, 18, "cs", "c", "c", "林晚|等待|回应"]],
    )
    assert result.rejection_counts == {"invalid_claim_shape": 1}


def test_arc_without_arc_source_still_structurally_rejected():
    registry, boundary = _fixture()
    result = _run(registry, boundary, a=[[0, 0, "写下辞职信", "p"]])
    assert result.rejection_counts == {"invalid_milestone_source": 1}


def test_per_list_categories_and_phrase_rule_carry_over():
    registry, boundary = _fixture()
    bad_category = _run(
        registry, boundary,
        s=[[0, "写下辞职信", "kf", "c", "c", "林晚|写下|辞职信"]],
    )
    long_phrase = _run(
        registry, boundary,
        s=[[0, "写下辞职信", "cs", "c", "c", "字" * 8 + "|" + "字" * 8 + "|" + "字" * 2]],
    )
    assert bad_category.rejection_counts == {"invalid_category": 1}
    assert long_phrase.rejection_counts == {"invalid_compact_text": 1}


# ---------------------------------------------------------------------------
# prompt drift guards
# ---------------------------------------------------------------------------


def test_prompt_worked_example_matches_module_payload():
    """Prompt 内嵌示例与模块常量必须逐字一致——示例漂移即红灯。"""
    import json

    rendered = HANDOVER_EXTRACTION_PROMPT_V22.format(source_registry="[]")
    example_json = json.dumps(
        prompt_example_payload_v22(), ensure_ascii=False, separators=(",", ":")
    )
    assert example_json in rendered
    assert prompt_example_premise_v22() in rendered


def test_prompt_worked_example_round_trips_to_full_acceptance():
    """工程 gate 核心：Prompt 教的示例本身必须能通过真实 parser 端到端全收——
    连自己的示例都过不了的 Prompt 没资格进 Demo（V2.1 的教训制度化）。"""
    current = {
        "_section": 1, "subsection": 1, "title": "示例",
        "key_points": ["示例场景"], "description": "示例前提",
    }
    sources = build_handover_sources(
        section=1, subsection=1,
        generated_text=prompt_example_premise_v22(),
        current_outline=current, next_outline=None, arc_milestones=[],
    )
    registry = build_compact_source_registry(sources)
    boundary = compile_next_boundary(
        section=1, subsection=1, current_outline=current, next_outline=None
    )

    result = restore_and_validate_v22(
        prompt_example_payload_v22(), registry=registry, next_boundary=boundary
    )

    assert result.accepted_claim_count == 2  # 1 claim + 1 open event
    assert not result.rejection_counts
    assert result.rejection_shape_skeletons is None


def test_prompt_states_exact_arity_and_empty_arc_rule():
    """Demo #3 主导失败为 arity 方差（19/39）、次要为 a 项结构墙（每小节1条）——
    Prompt 必须显式声明定长与 a 空数组条件。"""
    prompt = HANDOVER_EXTRACTION_PROMPT_V22
    assert "s和f恰好6项，o恰好4项，a恰好4项" in prompt
    assert "没有类型为arc_milestone的行时，a必须为空数组" in prompt


def test_arity_skeleton_telemetry_is_content_free():
    """Demo #3: S1.2/S1.4 的 19 个 item 死于 arity 错误但形状不可知。骨架
    遥测记录容器/长度/元素类型名——绝不含内容字符——让"错成了什么"可观测。"""
    registry, boundary = _fixture()

    result = _run(
        registry, boundary,
        s=[
            [0, 10, 18, "cs", "c", "c", "林晚|等待|回应"],  # 旧 v2.1 七元 span 形状
            {"来源": 0},                                      # dict 而非数组
        ],
        o=[[0, "林晚等待周野回应", "o"]],                     # 少一格
    )

    assert result.rejection_shape_skeletons == {
        "list[7]:int,int,int,str,str,str,str": 1,
        "dict[1]": 1,
        "list[3]:int,str,str": 1,
    }
    # 隐私约束:骨架里不允许出现任何中文/内容字符
    for key in result.rejection_shape_skeletons:
        assert not any("一" <= ch <= "鿿" for ch in key)


def test_non_shape_rejections_do_not_produce_skeletons():
    """invalid_quote / quote_not_found 等非形状拒绝不记录骨架——遥测只回答
    arity 问题,不扩大采集面。"""
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, "这句话不在原文里", "cs", "c", "c", "林晚|等待|回应"]],
    )
    assert result.rejection_counts == {"quote_not_found": 1}
    assert result.rejection_shape_skeletons is None


def test_clean_payload_has_no_skeletons():
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, "红茶已经凉了", "os", "c", "c", "红茶|已经|凉了"]],
    )
    assert result.accepted_claim_count == 1
    assert result.rejection_shape_skeletons is None


def test_prompt_teaches_quotes_and_never_mentions_offsets():
    prompt = HANDOVER_EXTRACTION_PROMPT_V22
    assert '"原文短引"' in prompt
    assert "4~20个连续字符" in prompt
    assert "s只用ts/ls/cs/rs/os/fs" in prompt
    assert "f只用ts/ls/kf/os/fs" in prompt
    assert "不加引号" in prompt
    assert '"v":"2.2"' in prompt
    # 偏移引用已死:V2.2 的 Prompt 不得再出现 start/end 输出要求
    assert "start/end输出" not in prompt
    assert "0<=start<end" not in prompt


# ---------------------------------------------------------------------------
# capacity ledger under the quote cost
# ---------------------------------------------------------------------------


def test_capacity_gate_with_quotes():
    """Quotes enlarge the worst legal payload; the output cap moves 600→1000
    (real estimate_tokens measured worst=813 on 2026-07-26; the initial 800
    left −13 headroom) and the standing gate formula (cap − worst ≥ 100)
    must hold on the REAL estimator this test runs under."""
    assert HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS == 600
    assert HANDOVER_COMPACT_V22_MAX_OUTPUT_TOKENS == 1000

    typical = compact_payload_metrics(typical_compact_payload_v22(), version="2.2")
    worst = compact_payload_metrics(worst_legal_compact_payload_v22(), version="2.2")

    assert typical["estimated_tokens"] <= 400
    assert HANDOVER_COMPACT_V22_MAX_OUTPUT_TOKENS - worst["estimated_tokens"] >= 100


def test_worst_legal_payload_items_are_shape_maximal():
    worst = worst_legal_compact_payload_v22()
    assert len(worst["s"]) == 4 and len(worst["o"]) == 3
    assert len(worst["f"]) == 3 and len(worst["a"]) == 2
    assert len(worst["s"][0][1]) == MAX_QUOTE_CHARS


def test_typical_payload_restores_against_fixture_when_quotes_exist():
    """typical_compact_payload_v22 uses quotes not present in this fixture —
    it must fail loudly as quote_not_found, proving no fuzzy fallback exists
    even for the reference payload."""
    registry, boundary = _fixture()
    result = restore_and_validate_v22(
        typical_compact_payload_v22(), registry=registry, next_boundary=boundary
    )
    reasons = set((result.rejection_counts or {}).keys())
    assert reasons <= {"quote_not_found", "invalid_milestone_source"}


# ---------------------------------------------------------------------------
# quote discipline teaching unit (2026-07-27): negative example + ordering rule
# ---------------------------------------------------------------------------

NEGATIVE_EXAMPLE_PREMISE = "林晚把相机递给周野，转身走进面包店。"
NEGATIVE_EXAMPLE_PARAPHRASE = "相机被递给了周野"
NEGATIVE_EXAMPLE_VERBATIM = "林晚把相机递给周野"


def test_prompt_contains_quote_discipline_teaching_unit():
    rendered = HANDOVER_EXTRACTION_PROMPT_V22.format(source_registry="[]")
    assert "反例" in rendered
    assert NEGATIVE_EXAMPLE_PREMISE in rendered
    assert NEGATIVE_EXAMPLE_PARAPHRASE in rendered
    assert NEGATIVE_EXAMPLE_VERBATIM in rendered
    assert "先选引文，后写短语" in rendered
    assert "不得先想结论再造引文" in rendered


def test_negative_example_is_honest_against_real_parser():
    """反例的两个论断必须由真实 parser 背书：改写被拒（quote_not_found）、
    逐字复制全收——Prompt 教的必须是 parser 真实执行的行为。"""
    current = {
        "_section": 1, "subsection": 1, "title": "递交相机",
        "key_points": ["相机移交"], "description": "林晚把相机交给周野",
    }
    sources = build_handover_sources(
        section=1, subsection=1, generated_text=NEGATIVE_EXAMPLE_PREMISE,
        current_outline=current, next_outline=None, arc_milestones=[],
    )
    registry = build_compact_source_registry(sources)
    boundary = compile_next_boundary(
        section=1, subsection=1, current_outline=current, next_outline=None
    )

    rejected = restore_and_validate_v22(
        {
            "v": "2.2",
            "s": [[0, NEGATIVE_EXAMPLE_PARAPHRASE, "os", "c", "c", "林晚|递给|相机"]],
            "o": [], "f": [], "a": [],
        },
        registry=registry, next_boundary=boundary,
    )
    assert rejected.accepted_claim_count == 0
    assert (rejected.rejection_counts or {}).get("quote_not_found") == 1

    accepted = restore_and_validate_v22(
        {
            "v": "2.2",
            "s": [[0, NEGATIVE_EXAMPLE_VERBATIM, "os", "c", "c", "林晚|递给|相机"]],
            "o": [], "f": [], "a": [],
        },
        registry=registry, next_boundary=boundary,
    )
    assert accepted.accepted_claim_count == 1
    assert not accepted.rejection_counts
