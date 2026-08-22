"""Prompt↔Parser alignment for the V2.1 compact handover transport.

Background (2026-07-26, task 1f6581ee): the only real V2.1 demo emitted 32
items across four subsections and the restore layer rejected every single one
as `invalid_contract_shape`. The offline cross-check that closed that report
found the mismatches encoded here: payloads a model produces by following the
prompt LITERALLY must restore; shapes the prompt now explicitly forbids must
keep rejecting — with subdivided telemetry reasons instead of one opaque bucket.
"""

from app.utils.prompt_templates import HANDOVER_EXTRACTION_PROMPT_V21
from app.writing.handover_contract_v2 import (
    build_handover_sources,
    compile_next_boundary,
)
from app.writing.handover_contract_v21 import (
    HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS,
    MAX_COMPACT_TEXT,
    build_compact_source_registry,
    compact_payload_metrics,
    restore_and_validate_v21,
    worst_legal_compact_payload,
)


PROSE = (
    "林晚在凌晨两点收到第二十版文案的驳回邮件。她盯着屏幕看了很久，"
    "然后打开一个新文档，写下辞职信。走出写字楼时，风把她的头发吹乱了。"
    "街角面包店的灯还亮着，麦子发酵的香气涌过来，她停住了脚步。"
    "林晚等待周野回应她的拍摄请求，保温杯里的红茶已经凉了。"
    "她把相机留在帆布袋里，决定先在书店台阶上坐一会儿。"
) * 2


def _fixture():
    current = {
        "_section": 1,
        "subsection": 1,
        "title": "辞职之夜",
        "key_points": ["林晚辞职", "闻到面包香"],
        "description": "林晚裸辞当晚的经历",
    }
    following = {
        "_section": 1,
        "subsection": 2,
        "title": "三次蹲守",
        "key_points": ["蹲守面包店"],
        "description": "林晚连续蹲守周野",
    }
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=PROSE,
        current_outline=current,
        next_outline=following,
        arc_milestones=[],  # v1 弧线无 source_id/hash，真实运行中 registry 没有 arc 源
    )
    registry = build_compact_source_registry(sources)
    boundary = compile_next_boundary(
        section=1, subsection=1, current_outline=current, next_outline=following
    )
    return registry, boundary


def _run(registry, boundary, **lists):
    payload = {"v": "2.1", "s": [], "o": [], "f": [], "a": []}
    payload.update(lists)
    return restore_and_validate_v21(payload, registry=registry, next_boundary=boundary)


ANCHOR = PROSE.find("林晚等待周野回应")


# ---------------------------------------------------------------------------
# prompt-literal payloads must restore
# ---------------------------------------------------------------------------


def test_phrase_with_sixteen_char_segment_sum_restores():
    """Prompt: "三段短语合计不超过16字（不含|分隔符）". A phrase whose three
    segments sum to exactly 16 is 18 chars raw — the pre-fix parser rejected
    it on raw length, one of the proven causes of the 32/32 demo failure."""
    registry, boundary = _fixture()
    # 2+6+8 = 16 字，raw 18；三段均逐字取自证据区间（validator 策略层要求）
    phrase = "林晚|等待周野回应|保温杯里的红茶已"
    parts = phrase.split("|")
    assert sum(len(part) for part in parts) == MAX_COMPACT_TEXT
    assert len(phrase) == MAX_COMPACT_TEXT + 2
    span_text = "林晚等待周野回应她的拍摄请求，保温杯里的红茶已"
    assert PROSE[ANCHOR:ANCHOR + len(span_text)] == span_text

    result = _run(
        registry, boundary,
        s=[[0, ANCHOR, ANCHOR + len(span_text), "cs", "c", "c", phrase]],
    )

    assert result.accepted_claim_count == 1
    assert not result.rejection_counts


def test_segment_sum_over_sixteen_rejects_with_specific_reason():
    registry, boundary = _fixture()
    phrase = "字" * 8 + "|" + "字" * 8 + "|" + "字" * 2  # sum 18 > 16

    result = _run(
        registry, boundary,
        s=[[0, ANCHOR, ANCHOR + 8, "cs", "c", "c", phrase]],
    )

    assert result.accepted_claim_count == 0
    assert result.rejection_counts == {"invalid_compact_text": 1}


def test_control_claim_and_open_event_restore():
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, ANCHOR, ANCHOR + 8, "cs", "c", "c", "林晚|等待|回应"]],
        o=[[0, ANCHOR, ANCHOR + 8, "o", "林晚|等待|回应"]],
    )
    # accepted_claim_count 同时计入 end-state claim 与 open event
    assert result.accepted_claim_count == 2
    assert not result.rejection_counts
    assert len(result.contract.open_events) == 1


# ---------------------------------------------------------------------------
# the prompt must carry the parser's real constraints (drift guards)
# ---------------------------------------------------------------------------


def test_prompt_states_per_list_category_sets():
    """Pre-fix the prompt listed all 7 category codes globally; the parser
    allows {ts,ls,cs,rs,os,fs} for s and {ts,ls,kf,os,fs} for f. A model
    putting kf in s was shape-rejected. The prompt must state both sets."""
    assert "s只用ts/ls/cs/rs/os/fs" in HANDOVER_EXTRACTION_PROMPT_V21
    assert "f只用ts/ls/kf/os/fs" in HANDOVER_EXTRACTION_PROMPT_V21


def test_prompt_pins_numeric_literals_and_separator_exclusion():
    assert "不加引号" in HANDOVER_EXTRACTION_PROMPT_V21
    assert "不含|分隔符" in HANDOVER_EXTRACTION_PROMPT_V21


def test_category_outside_per_list_set_still_rejects():
    """Parser semantics unchanged: kf stays invalid for s (states vs facts is
    a V2 contract distinction). The fix is prompt-side communication."""
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, ANCHOR, ANCHOR + 8, "kf", "c", "c", "林晚|等待|回应"]],
    )
    assert result.rejection_counts == {"invalid_category": 1}


# ---------------------------------------------------------------------------
# subdivided telemetry: the next failed demo must be classifiable offline
# ---------------------------------------------------------------------------


def test_quoted_numbers_report_invalid_source_index():
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[["0", str(ANCHOR), str(ANCHOR + 8), "cs", "c", "c", "林晚|等待|回应"]],
    )
    assert result.rejection_counts == {"invalid_source_index": 1}


def test_float_offsets_report_invalid_span():
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, float(ANCHOR), float(ANCHOR + 8), "cs", "c", "c", "林晚|等待|回应"]],
    )
    assert result.rejection_counts == {"invalid_span": 1}


def test_wrong_offsets_on_open_event_report_component_mismatch():
    """Documented residual: the model cannot count character offsets; open
    events verify actors/action/object verbatim inside the excerpt, so a
    drifted span fails with a reason that names the real problem."""
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        o=[[0, ANCHOR + 40, ANCHOR + 48, "o", "林晚|等待|回应"]],
    )
    assert result.rejection_counts == {"unsupported_open_event_component": 1}


def test_arc_without_arc_source_reports_milestone_source():
    """Documented residual: with CHARACTER_ARC_CONTRACT_VERSION=v1 the
    milestones carry no source_id/hash, build_handover_sources skips them,
    and every `a` item is structurally rejected. The telemetry must say so."""
    registry, boundary = _fixture()
    result = _run(registry, boundary, a=[[0, 0, ANCHOR, ANCHOR + 8, "p"]])
    assert result.rejection_counts == {"invalid_milestone_source": 1}


def test_unrecognized_details_fall_back_to_generic_bucket():
    """A non-list item raises a shape error whose text is not a registered
    reason — it must land in the generic invalid_contract_shape bucket, not
    crash and not invent a new reason."""
    registry, boundary = _fixture()
    result = _run(registry, boundary, s=[{"not": "a-list"}])
    assert result.rejection_counts == {"invalid_claim_shape": 1}


# ---------------------------------------------------------------------------
# capacity ledger stays honest under the relaxed rule
# ---------------------------------------------------------------------------


def test_worst_legal_payload_reflects_segment_sum_rule_and_fits_budget():
    worst = worst_legal_compact_payload()
    phrase = worst["s"][0][-1]
    parts = phrase.split("|")
    assert len(parts) == 3
    assert sum(len(part) for part in parts) == MAX_COMPACT_TEXT
    assert len(phrase) == MAX_COMPACT_TEXT + 2

    metrics = compact_payload_metrics(worst)
    assert (
        HANDOVER_COMPACT_V21_MAX_OUTPUT_TOKENS - metrics["estimated_tokens"] >= 100
    )
