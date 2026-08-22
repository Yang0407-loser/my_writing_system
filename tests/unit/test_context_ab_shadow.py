from app.context_ab_shadow import assemble_shadow_messages, messages_hash


def test_shadow_assembly_drops_only_complete_optional_items_and_preserves_legacy():
    template = "goal={goal}\nsummary={summary_context}\nrules={rules_context}\nstyle={style_examples}\nrag={retrieved_context}"
    values = {
        "goal": "完成目标",
        "summary_context": "【最近内容】\n旧一\n\n紧邻",
        "rules_context": "标题\n[优先级9] 锁定规则\n[优先级2] 软规则",
        "style_examples": "完整风格示例",
        "retrieved_context": "完整RAG证据",
    }
    user = template.format(**values)
    blocks = [
        {"block_id": "current:goal", "category": "current_writing", "text": "完成目标", "injection_position": "{goal}"},
        {"block_id": "recent:old", "category": "recent_original", "text": "旧一", "injection_position": "{summary_context}"},
        {"block_id": "recent:previous", "category": "recent_original", "text": "紧邻", "injection_position": "{summary_context}"},
        {"block_id": "other:global-rules", "category": "other", "text": values["rules_context"], "injection_position": "{rules_context}"},
        {"block_id": "style:style_examples", "category": "style_examples", "text": "完整风格示例", "injection_position": "{style_examples}"},
        {"block_id": "rag:1", "category": "rag", "text": "完整RAG证据", "injection_position": "{retrieved_context}"},
    ]
    items = [
        ("current:goal", "goal", "P0", True),
        ("recent:old", "old", "P3", False),
        ("recent:previous", "previous", "P1", True),
        ("other:global-rules:line:0", "header", "P3", False),
        ("other:global-rules:line:1", "locked", "P0", True),
        ("other:global-rules:line:2", "soft", "P3", False),
        ("style:style_examples", "style", "P3", False),
        ("rag:1", "rag", "P2", True),
    ]
    run = {
        "profile": "budgeted_broker",
        "items": [{"item_id": item_id, "source_id": source, "priority": priority, "keep": keep} for item_id, source, priority, keep in items],
    }
    legacy = [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": user}]
    sample = {"blocks": blocks, "runtime": {"messages": legacy, "template": template, "values": values}}
    result = assemble_shadow_messages(sample, run)
    shadow = result["shadow_messages"][1]["content"]
    assert result["legacy_messages"] == legacy
    assert messages_hash(legacy) == result["legacy_hash"]
    assert "旧一" not in shadow and "完整风格示例" not in shadow and "软规则" not in shadow
    assert "紧邻" in shadow and "锁定规则" in shadow and "完整RAG证据" in shadow


def test_shadow_assembly_rejects_protected_drop():
    sample = {
        "blocks": [],
        "runtime": {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}], "template": "U", "values": {}},
    }
    run = {"profile": "budgeted_broker", "items": [{"item_id": "rag", "source_id": "rag", "priority": "P2", "keep": False}]}
    try:
        assemble_shadow_messages(sample, run)
    except ValueError as exc:
        assert "protected" in str(exc)
    else:
        raise AssertionError("protected drop should fail")


def test_shadow_assembler_accepts_risk_guarded_profile():
    template = "summary={summary_context}"
    values = {"summary_context": "旧一\n\n紧邻"}
    blocks = [
        {"block_id": "recent:old", "category": "recent_original", "text": "旧一", "injection_position": "{summary_context}"},
        {"block_id": "recent:previous", "category": "recent_original", "text": "紧邻", "injection_position": "{summary_context}"},
    ]
    run = {
        "profile": "risk_guarded_broker",
        "items": [
            {"item_id": "recent:old", "source_id": "old", "priority": "P3", "keep": False},
            {"item_id": "recent:previous", "source_id": "previous", "priority": "P1", "keep": True},
        ],
    }
    legacy = [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": template.format(**values)}]
    sample = {"blocks": blocks, "runtime": {"messages": legacy, "template": template, "values": values}}

    result = assemble_shadow_messages(sample, run)

    assert "旧一" not in result["shadow_messages"][1]["content"]
    assert "紧邻" in result["shadow_messages"][1]["content"]
