import inspect

from app.continuity_risk_guard import ContinuityRiskGuard
from app.context_broker import ContextItem


def older(text: str, actors=()) -> ContextItem:
    return ContextItem(
        item_id="recent:old",
        source_id="golden:S1:U1",
        source_type="recent_original",
        requirement="optional_context",
        priority="P3",
        text=text,
        estimated_tokens=100,
        injection_position="{summary_context}",
        actors=actors,
    )


def assess(text: str, **kwargs):
    defaults = {
        "query": "林晚继续处理邀请",
        "immediate_text": "林晚回到房间。",
        "handover_text": "",
        "peer_older_texts": (),
    }
    defaults.update(kwargs)
    return ContinuityRiskGuard().assess(older(text), **defaults)


def test_guard_protects_time_state_and_unfinished_chain_with_short_evidence():
    result = assess("周六下午，周野住院后仍没有回答林晚的邀请。")

    assert result.protect is True
    risk_types = {item["risk_type"] for item in result.risks}
    assert {
        "relative_time_anchor",
        "durable_character_or_world_state",
        "unfinished_interaction_chain",
    } <= risk_types
    assert all(item["evidence"] and len(item["evidence"]) <= 80 for item in result.risks)


def test_guard_protects_unique_current_event_source():
    result = assess(
        "林晚整理面包婚礼请柬并检查社区名单。",
        query="面包婚礼需要确认社区名单",
        immediate_text="院子里摆好长桌。",
        peer_older_texts=("周野清理烤箱。",),
    )

    assert result.protect is True
    assert any(item["risk_type"] == "unique_current_event_source" for item in result.risks)


def test_guard_protects_handover_reference():
    result = assess(
        "陌生号码发来威胁短信，林晚把手机扣下。",
        query="继续写作",
        immediate_text="林晚走进活动室。",
        handover_text="待承接：陌生号码的威胁短信后续。",
    )

    assert result.protect is True
    assert any(item["risk_type"] == "handover_explicit_reference" for item in result.risks)


def test_guard_drops_only_clear_nonmatch_and_keeps_ambiguous_actor_overlap():
    clear = assess(
        "雨水落在空置货架上。",
        query="季晴核对借款安排",
        immediate_text="季晴打开账本。",
    )
    ambiguous_item = older("林晚整理桌面。", actors=("林晚",))
    ambiguous = ContinuityRiskGuard().assess(
        ambiguous_item,
        query="林晚继续工作",
        immediate_text="门外传来脚步声。",
        handover_text="",
    )

    assert clear.protect is False
    assert clear.reason == "clear_continuity_nonmatch"
    assert ambiguous.protect is True
    assert ambiguous.risks[0]["risk_type"] == "uncertain_continuity_fallback"


def test_guard_runtime_contract_has_no_evaluation_labels():
    source = inspect.getsource(ContinuityRiskGuard)
    forbidden = {"must_recall_facts", "gold_sections", "human_relevant", "supports_which_fact"}

    assert not any(field in source for field in forbidden)
