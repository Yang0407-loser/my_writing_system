from app.event_chunker import EventChunker, make_parent


def _parent(text):
    return make_parent(source_id="p1", task_id="t1", section=3, subsection=1, title="测试", text=text)


def test_parent_and_events_have_stable_traceable_contracts():
    parent = _parent("开场叙述。\n\n周六早上，林晚来了。\n\n“进来。”\n\n“好。”")
    events = EventChunker(min_event_chars=4).chunk_parent(parent)
    assert parent["chunk_level"] == "parent"
    assert "text" not in {""}
    assert "".join(event["text"] for event in events) == parent["text"]
    for index, event in enumerate(events):
        assert parent["text"][event["start"]:event["end"]] == event["text"]
        assert event["event_index"] == index
        assert event["parent_source_id"] == parent["source_id"]
        assert event["source_id"].startswith("event-p1-")


def test_invitation_and_response_are_not_split():
    text = "前情。" + "甲" * 130 + "\n\n周六早上，周野说：“进来帮忙。”\n\n林晚回答：“好。”"
    events = EventChunker(min_event_chars=20, max_event_chars=80).chunk_parent(_parent(text))
    matching = [event for event in events if "进来帮忙" in event["text"]]
    assert len(matching) == 1
    assert "回答：“好。”" in matching[0]["text"]


def test_money_people_chain_is_not_split():
    text = "林晚联系季晴筹钱。" + "甲" * 130 + "\n\n顾衍说拿八万。\n\n吴阿姨说拿三万。"
    events = EventChunker(min_event_chars=20, max_event_chars=80).chunk_parent(_parent(text))
    assert len(events) == 1
    assert events[0]["event_type"] == "money_or_funding"
    assert events[0]["actors"] == ["林晚", "季晴", "顾衍", "吴阿姨"]


def test_event_ids_are_deterministic():
    parent = _parent("同一段正文。")
    assert EventChunker().chunk_parent(parent) == EventChunker().chunk_parent(parent)
