import inspect

from app.context_broker import ContextBroker, ContextItem, older_recent_relevance, priority_for


def item(name, source_type, priority, requirement, tokens, text="内容"):
    return ContextItem(
        item_id=name, source_id=f"source:{name}", source_type=source_type,
        requirement=requirement, priority=priority, text=text,
        estimated_tokens=tokens, injection_position="user prompt",
    )


def test_context_item_trace_has_required_fields_but_no_text():
    trace = item("goal", "current_writing", "P0", "hard_required", 10).trace()
    required = {
        "item_id", "source_id", "source_type", "requirement", "priority", "text_hash",
        "characters", "estimated_tokens", "injection_position", "section", "subsection",
        "actors", "keep", "keep_reason", "drop_reason", "budget_before", "budget_after", "provenance",
    }
    assert required <= trace.keys()
    assert "text" not in trace


def test_priority_contract_protects_required_sources():
    assert priority_for("fixed_prompt") == ("hard_required", "P0")
    assert priority_for("recent_original", immediate_previous=True) == ("continuity_required", "P1")
    assert priority_for("rag") == ("evidence_required", "P2")
    assert priority_for("style_examples") == ("optional_context", "P3")
    assert priority_for("other", locked=True) == ("hard_required", "P0")


def test_budget_is_soft_and_never_drops_p0_p1_p2():
    items = [
        item("hard", "fixed_prompt", "P0", "hard_required", 50),
        item("previous", "recent_original", "P1", "continuity_required", 50, "林晚推门"),
        item("rag", "rag", "P2", "evidence_required", 50),
        item("style", "style_examples", "P3", "optional_context", 50),
    ]
    result = ContextBroker(target_tokens=100).select(items, profile="budgeted_broker", query="林晚推门")
    by_id = {entry["item_id"]: entry for entry in result["items"]}
    assert all(by_id[name]["keep"] for name in ("hard", "previous", "rag"))
    assert not by_id["style"]["keep"]
    assert result["budget_overflow_reason"] == "protected_P0_P1_P2_exceed_soft_budget"


def test_ambiguous_older_recent_is_kept_with_fallback():
    older = item("older", "recent_original", "P3", "optional_context", 20, "林晚看向窗外。")
    result = older_recent_relevance(older, query="林晚核对账目", immediate_text="林晚推门进店。")
    assert result["keep"] is True
    assert result["fallback_reason"] == "insufficient_signal_to_drop_safely"


def test_runtime_api_cannot_receive_evaluation_fields():
    signature = str(inspect.signature(ContextBroker.select))
    for forbidden in ("must_recall_facts", "gold_sections", "human_relevant", "supports_which_fact"):
        assert forbidden not in signature
