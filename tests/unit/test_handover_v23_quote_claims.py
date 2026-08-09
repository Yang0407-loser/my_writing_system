"""V2.3「引文即主张」工程 gate。

设计动因（Demo #4–#6 payload 逐条归因）：短语逐字包含检查在中文叙事上
双向失效——代词/省略句拒真（约 8 条/次），多名句无法验证归因却能放行；
16 字预算与逐字覆盖内在冲突（压缩类约 5 条/次）。短语唯一不可替代的
贡献（实体绑定）恰是逐字检查验证不了的，整层退役、绑定另行立项。

V2.3 契约承诺：逐字溯源（短引 find 定位 + 整句证据）。不承诺分类正确。
制度化教训延续：每个 gate 都把 Prompt 字面形状喂进真实 parser 端到端。
"""

import json

from app.utils.prompt_templates import HANDOVER_EXTRACTION_PROMPT_V23
from app.writing.handover_contract_v2 import (
    adapt_v2_to_legacy_handover_note,
    build_handover_sources,
    compile_next_boundary,
)
from app.writing.handover_contract_v21 import (
    HANDOVER_COMPACT_V23_MAX_OUTPUT_TOKENS,
    build_compact_source_registry,
    compact_payload_metrics,
    prompt_example_payload_v23,
    prompt_example_premise_v23,
    restore_and_validate_v23,
    typical_compact_payload_v23,
    worst_legal_compact_payload_v23,
)

PROSE = (
    "林晚在凌晨两点收到第二十版文案的驳回邮件。她盯着屏幕看了很久，"
    "然后打开一个新文档，写下辞职信。走出写字楼时，风把她的头发吹乱了。"
    "街角面包店的灯还亮着，麦子发酵的香气涌过来，她停住了脚步。"
    "林晚等待周野回应她的拍摄请求，保温杯里的红茶已经凉了。"
    "她把相机留在帆布袋里，决定先在书店台阶上坐一会儿。"
)


def _fixture(text=PROSE):
    current = {
        "_section": 1, "subsection": 1, "title": "辞职之夜",
        "key_points": ["林晚辞职"], "description": "林晚裸辞当晚的经历",
    }
    following = {
        "_section": 1, "subsection": 2, "title": "三次蹲守",
        "key_points": ["蹲守面包店"], "description": "林晚连续蹲守周野",
    }
    sources = build_handover_sources(
        section=1, subsection=1, generated_text=text,
        current_outline=current, next_outline=following, arc_milestones=[],
    )
    registry = build_compact_source_registry(sources)
    boundary = compile_next_boundary(
        section=1, subsection=1, current_outline=current, next_outline=following
    )
    return registry, boundary


def _run(registry, boundary, **lists):
    payload = {"v": "2.3", "s": [], "o": [], "f": [], "a": []}
    payload.update(lists)
    return restore_and_validate_v23(
        payload, registry=registry, next_boundary=boundary
    )


# ---------------------------------------------------------------------------
# prompt ↔ parser round trip
# ---------------------------------------------------------------------------


def test_prompt_worked_example_matches_module_payload():
    rendered = HANDOVER_EXTRACTION_PROMPT_V23.format(source_registry="[]")
    embedded = json.dumps(
        prompt_example_payload_v23(), ensure_ascii=False, separators=(",", ":")
    )
    assert embedded in rendered
    assert prompt_example_premise_v23() in rendered


def test_prompt_worked_example_round_trips_to_full_acceptance():
    registry, boundary = _fixture(prompt_example_premise_v23())
    result = restore_and_validate_v23(
        prompt_example_payload_v23(), registry=registry, next_boundary=boundary
    )
    assert result.accepted_claim_count == 2  # 1 claim + 1 open event
    assert not result.rejection_counts
    assert result.rejection_shape_skeletons is None


def test_prompt_negative_example_is_honest_against_real_parser():
    premise = "林晚把相机递给周野，转身走进面包店。"
    registry, boundary = _fixture(premise)
    rejected = _run(
        registry, boundary, s=[[0, "相机被递给了周野", "os", "c", "c"]]
    )
    assert rejected.accepted_claim_count == 0
    assert (rejected.rejection_counts or {}).get("quote_not_found") == 1
    accepted = _run(
        registry, boundary, s=[[0, "林晚把相机递给周野", "os", "c", "c"]]
    )
    assert accepted.accepted_claim_count == 1
    assert not accepted.rejection_counts


def test_prompt_states_v23_arity_and_carries_no_phrase_rules():
    rendered = HANDOVER_EXTRACTION_PROMPT_V23.format(source_registry="[]")
    assert "s和f恰好5项，o恰好3项，a恰好4项" in rendered
    assert "a必须为空数组" in rendered
    # 短语层退役：不得再出现短语规则残留
    assert "主体|动作" not in rendered
    assert "短语" not in rendered
    assert "16字" not in rendered and "16 字" not in rendered


# ---------------------------------------------------------------------------
# shape: v2.2 items are wrong arity here
# ---------------------------------------------------------------------------


def test_old_v22_claim_shape_is_rejected_as_wrong_arity():
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        s=[[0, "林晚等待周野回应", "cs", "c", "c", "林晚|等待|回应"]],
    )
    assert result.accepted_claim_count == 0
    assert (result.rejection_counts or {}).get("invalid_claim_shape") == 1
    assert result.rejection_shape_skeletons  # 骨架遥测继续工作


def test_old_v22_open_event_shape_is_rejected_as_wrong_arity():
    registry, boundary = _fixture()
    result = _run(
        registry, boundary,
        o=[[0, "林晚等待周野回应", "o", "林晚|等待|回应"]],
    )
    assert not result.contract.open_events
    assert (result.rejection_counts or {}).get("invalid_open_event_shape") == 1


def test_per_list_categories_carry_over():
    registry, boundary = _fixture()
    result = _run(registry, boundary, f=[[0, "林晚等待周野回应", "cs", "c", "c"]])
    assert (result.rejection_counts or {}).get("invalid_category") == 1


# ---------------------------------------------------------------------------
# anchor-scoped tense check（Demo #6 回归的修复在 V2.3 落地）
# ---------------------------------------------------------------------------


def test_state_negation_in_sentence_no_longer_poisons_current_claim():
    """Demo #6 镜像：证据句含"没有…"状态否定，claim 标 current——旧句级
    标记检查误伤（3/3 假阳性），anchor 级检查应全收。"""
    text = "前一句。他把那枝花插在玻璃杯里，花瓣还撑着，没有萎。后一句。"
    registry, boundary = _fixture(text)
    result = _run(registry, boundary, s=[[0, "他把那枝花插在玻璃杯里", "os", "c", "c"]])
    assert result.accepted_claim_count == 1
    assert not result.rejection_counts


def test_marker_inside_quote_still_rejects_current_claim():
    """守卫保留在正确的作用域：短引本身含非当前标记 + 标 current → 仍拒。"""
    text = "前一句。她计划明天把杯子送回去。后一句。"
    registry, boundary = _fixture(text)
    result = _run(registry, boundary, s=[[0, "计划明天把杯子送回去", "os", "c", "c"]])
    assert result.accepted_claim_count == 0
    assert (result.rejection_counts or {}).get("tense_or_state_mismatch") == 1


# ---------------------------------------------------------------------------
# 消费端投影：note 字段携带整句原文（消费优先）
# ---------------------------------------------------------------------------


def test_note_projection_uses_full_sentence_excerpts():
    text = "开场一句。她把白瓷杯放回窗台，顺手带上了门。收尾一句。"
    registry, boundary = _fixture(text)
    result = _run(
        registry, boundary,
        s=[[0, "把白瓷杯放回窗台", "cs", "c", "c"]],
        o=[[0, "顺手带上了门", "o"]],
    )
    note = adapt_v2_to_legacy_handover_note(result)
    assert note["character_state"] == "她把白瓷杯放回窗台，顺手带上了门。"
    assert note["open_threads"] == "她把白瓷杯放回窗台，顺手带上了门。"


def test_note_projection_dedupes_shared_sentences():
    """同句多 claim（Demo #6 实际出现）在投影中去重。"""
    text = "开场一句。二楼的窗户还亮着，烘焙机的低鸣还在持续。收尾一句。"
    registry, boundary = _fixture(text)
    result = _run(
        registry, boundary,
        s=[
            [0, "二楼的窗户还亮着", "os", "c", "c"],
            [0, "烘焙机的低鸣还在持续", "os", "c", "c"],
        ],
    )
    assert result.accepted_claim_count == 2
    note = adapt_v2_to_legacy_handover_note(result)
    assert note["new_facts"] == ["二楼的窗户还亮着，烘焙机的低鸣还在持续。"]


def test_open_event_needs_no_actor_annotation():
    registry, boundary = _fixture()
    result = _run(registry, boundary, o=[[0, "林晚等待周野回应", "o"]])
    assert len(result.contract.open_events) == 1
    assert result.contract.open_events[0].actors == ()
    assert not result.rejection_counts


# ---------------------------------------------------------------------------
# capacity
# ---------------------------------------------------------------------------


def test_capacity_gate_v23():
    worst = compact_payload_metrics(worst_legal_compact_payload_v23(), version="2.3")
    assert (
        HANDOVER_COMPACT_V23_MAX_OUTPUT_TOKENS - worst["estimated_tokens"] >= 100
    )


def test_typical_payload_restores_when_quotes_exist():
    registry, boundary = _fixture(
        "林晚决定继续记录见闻。林晚等待周野回应邀请。相册被留在书店里。她翻开了那本相册。"
    )
    result = restore_and_validate_v23(
        typical_compact_payload_v23(), registry=registry, next_boundary=boundary
    )
    reasons = set((result.rejection_counts or {}).keys())
    # 弧线项引用不存在的 arc_milestone 行——结构墙拒绝属预期
    assert reasons <= {"quote_not_found", "invalid_milestone_source"}
    assert result.accepted_claim_count >= 3


# ---------------------------------------------------------------------------
# writer 路由端到端（bare-writer + fake LLM，同 payload 持久化 gate 的模式）
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, response):
        self.response = response

    def chat_completion(self, messages, **kwargs):
        sink = kwargs.get("completion_metadata_sink")
        if sink is not None:
            sink({"finish_reason": "stop", "output_tokens": 64})
        return self.response


def test_writer_routes_v23_and_persists_payload():
    from app.agents.writer import Writer
    from app.config import settings

    writer = Writer.__new__(Writer)
    payload = prompt_example_payload_v23()
    writer.llm = _FakeLLM(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    original = settings.WRITER_HANDOVER_CONTRACT_VERSION
    settings.WRITER_HANDOVER_CONTRACT_VERSION = "v2.3"
    try:
        note, observation = writer._extract_handover_v21_with_observation(
            section_text=prompt_example_premise_v23(),
            section_num=1,
            sub_num=1,
            event_graph=None,
            current_subsection={"subsection": 1, "title": "t", "description": "d", "key_points": []},
            next_subsection={"subsection": 2, "title": "t2", "description": "d2", "key_points": []},
            task_id="v23-routing-test",
        )
    finally:
        settings.WRITER_HANDOVER_CONTRACT_VERSION = original
    assert note is not None
    assert observation.contract_version == "v2.3"
    assert observation.producer_version == "writer-handover-contract-v2.3"
    assert observation.payload_version == "2.3"
    assert observation.accepted_claim_count == 2
    assert observation.restored_claim_count == 2
    assert observation.compact_payload == payload
    assert note["next_boundary"]["next_subsection"] == 2
    assert note["next_boundary"]["allowed_start_events"] == ["d2"]
    assert note["next_boundary"]["must_not_repeat_events"] == ["d"]
