from app.routers.outline import (
    _build_tree_from_nodes,
    _flatten_tree,
    _outline_v2_to_tree,
    _tree_to_outline_v2,
)
from app.writing.outline_event_contract import (
    OutlineEventContractCompiler,
    canonicalise_confirmed_tree,
)


def _confirmed_contract():
    sub = {
        "subsection": 1,
        "source_id": "sub-1",
        "title": "相遇",
        "description": "",
        "key_points": ["林晚邀请周野进店，周野回应"],
        "target_words": 600,
    }
    compiler = OutlineEventContractCompiler()
    proposed = compiler.compile_chapter(
        section=1,
        subsections=[sub],
        character_names=["林晚", "周野"],
        chapter_target_words=600,
    ).subsection_contracts[0].model_dump(mode="json")
    proposed["status"] = "confirmed"
    proposed["confirmation_requested"] = True
    for event in proposed["events"]:
        event["status"] = "confirmed"
        event["user_confirmed"] = True
        event["requiredness"] = "soft"
    return sub, proposed


def test_optional_contract_survives_existing_tree_flat_v2_checkpoint_shapes():
    sub, submitted = _confirmed_contract()
    nodes = [
        {"id": "section-1", "parent_id": "", "title": "第一章"},
        {
            "id": "sub-1",
            "parent_id": "section-1",
            **sub,
            "event_contract": submitted,
        },
    ]
    tree = canonicalise_confirmed_tree(_build_tree_from_nodes(nodes))
    stored_contract = tree[0]["children"][0]["event_contract"]
    assert stored_contract["status"] == "confirmed"
    flat_nodes = _flatten_tree(tree)
    assert flat_nodes[1]["event_contract"]["contract_hash"] == stored_contract["contract_hash"]
    outline_v2 = _tree_to_outline_v2(tree)
    assert outline_v2[0]["subsections"][0]["event_contract"] == stored_contract
    restored = _outline_v2_to_tree(outline_v2)
    assert restored[0]["children"][0]["event_contract"] == stored_contract


def test_legacy_outline_without_contract_remains_compatible():
    tree = _build_tree_from_nodes([
        {"id": "section-1", "parent_id": "", "title": "第一章"},
        {
            "id": "sub-1",
            "parent_id": "section-1",
            "title": "旧小节",
            "description": "林晚开始记录。",
            "key_points": [],
            "target_words": 500,
        },
    ])
    outline_v2 = _tree_to_outline_v2(tree)
    assert "event_contract" not in outline_v2[0]["subsections"][0]


def test_normal_outline_save_marks_changed_confirmed_contract_stale():
    sub, submitted = _confirmed_contract()
    submitted["confirmation_requested"] = True
    tree = [{
        "id": "section-1",
        "children": [dict(sub, id="sub-1", event_contract=submitted)],
    }]
    canonicalise_confirmed_tree(tree)
    confirmed = tree[0]["children"][0]["event_contract"]
    assert confirmed["status"] == "confirmed"
    tree[0]["children"][0]["key_points"] = ["林晚独自关店"]
    canonicalise_confirmed_tree(tree)
    assert tree[0]["children"][0]["event_contract"]["status"] == "stale"
