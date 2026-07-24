from app.routers.outline import OutlineBudgetAdviceBody, get_outline_budget_advice


def _nodes():
    return [
        {"id": "section-1", "parent_id": "", "title": "第一章", "sort_order": 0},
        {
            "id": "sub-1",
            "parent_id": "section-1",
            "title": "相遇",
            "description": "林晚邀请周野进店，周野答应。",
            "key_points": ["林晚邀请周野进店并得到回应"],
            "target_words": 500,
            "sort_order": 0,
        },
    ]


def test_budget_advice_route_is_read_only_and_traceable(monkeypatch):
    def forbidden_storage():
        raise AssertionError("read-only advice must not touch Redis")

    monkeypatch.setattr("app.routers.outline._get_redis", forbidden_storage)
    result = get_outline_budget_advice(
        "task-1",
        OutlineBudgetAdviceBody(
            nodes=_nodes(),
            style_profile={"dialogue_ratio": 0.4},
            character_names=["林晚", "周野"],
            chapter_budget=500,
        ),
    )
    advice = result["chapters"][0]["subsections"][0]
    assert advice["source_manifest"]
    assert advice["event_units"][0]["source_id"].startswith("sub-1")
    assert advice["event_contract"]["status"] == "proposed"
    assert advice["event_contract"]["stop_after_event_id"] is None
    assert result["chapters"][0]["allocated_total"] == 500


def test_frontend_advice_is_applied_only_by_explicit_action():
    source = open("app/static/js/main.js", encoding="utf-8").read()
    template = open("app/static/index.html", encoding="utf-8").read()
    request_block = source.split("async function requestOutlineBudgetAdvice", 1)[1].split(
        "function applyBudgetRecommendation", 1
    )[0]
    apply_block = source.split("function applyBudgetRecommendation", 1)[1].split(
        "async function confirmEventContract", 1
    )[0]
    confirm_block = source.split("async function confirmEventContract", 1)[1].split(
        "function toggleBudgetAdvice", 1
    )[0]
    assert "target_words =" not in request_block
    assert "API.saveOutlineNodes" not in request_block
    assert "node.target_words =" in apply_block
    assert "budgetApplyValue(advice)" in apply_block
    assert "API.saveOutlineNodes" not in apply_block
    assert "node.event_contract = confirmed" in confirm_block
    assert "await saveOutlineFn()" in confirm_block
    assert "budget-advice-popup" in template
    assert "budget-advice-row" not in template
    assert 'title="查看篇幅建议"' in template
    assert "确认事件结构并保存大纲" in template
    assert "确认事件结构不会应用推荐字数" in template
