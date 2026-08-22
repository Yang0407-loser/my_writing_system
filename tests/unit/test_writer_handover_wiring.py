from app.agents.writer import Writer, _HANDOVER_NEXT_BOUNDARY_VERSIONS


class BriefLLM:
    def chat_completion(self, *_args, **_kwargs):
        return "人物已经离开公司，下一场应承接新的行动。"


def handover_note():
    return {
        "foreshadowing": "",
        "character_state": "林晚已经离开公司",
        "open_threads": "正式辞职尚未完成",
        "new_facts": ["辞职信只发送给私人邮箱"],
        "next_boundary": {
            "allowed_start_events": ["林晚第一次前往面包店"],
            "must_not_repeat_events": ["林晚离开公司"],
            "stop_or_transition_reason": "transition without replay",
        },
    }


def test_v23_receives_the_same_next_subsection_boundary_as_other_v2_versions():
    assert _HANDOVER_NEXT_BOUNDARY_VERSIONS == {"v2", "v2.1", "v2.2", "v2.3"}


def test_latest_subsection_handover_is_promoted_immediately():
    previous = {"character_state": "旧状态"}
    current = handover_note()

    assert Writer._advance_local_handover(previous, current) is current
    assert Writer._advance_local_handover(previous, None) is previous


def test_fallback_brief_preserves_facts_and_explicit_boundary():
    brief = Writer._build_handover_brief(handover_note(), llm_client=None)

    assert "辞职信只发送给私人邮箱" in brief
    assert "已完成、不得重新演一遍：林晚离开公司" in brief
    assert "下一小节允许承接：林晚第一次前往面包店" in brief


def test_llm_brief_cannot_drop_the_deterministic_boundary():
    brief = Writer._build_handover_brief(handover_note(), llm_client=BriefLLM())

    assert "人物已经离开公司" in brief
    assert "【小节连续性边界】" in brief
    assert "不得重新演一遍" in brief
