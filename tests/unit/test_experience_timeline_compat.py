from app import experience_timeline as timeline


def canonical_event(event_id="event-1"):
    return {
        "id": event_id,
        "task_id": "task-1",
        "type": "major_event",
        "description": "林晚决定继续记录",
        "chapter": 4,
        "subsection": 1,
        "importance": 8,
        "related_characters": ["林晚"],
        "related_items": [],
        "related_locations": [],
    }


def test_add_event_writes_only_to_event_store(monkeypatch):
    calls = []

    def fake_add_event(**kwargs):
        calls.append(kwargs)
        return canonical_event()

    monkeypatch.setattr(timeline._es, "add_event", fake_add_event)
    result = timeline.add_event(
        {
            "task_id": "task-1",
            "event_type": "major_event",
            "description": "林晚决定继续记录",
            "chapter": 4,
            "importance": 8,
            "related_characters": ["林晚"],
        }
    )

    assert len(calls) == 1
    assert calls[0]["event_type"] == "major_event"
    assert result["event_type"] == "major_event"


def test_list_events_maps_canonical_type_to_legacy_field(monkeypatch):
    monkeypatch.setattr(
        timeline._es,
        "get_events",
        lambda task_id, limit: [canonical_event("event-2")],
    )
    result = timeline.list_events("task-1")
    assert result[0]["id"] == "event-2"
    assert result[0]["event_type"] == "major_event"


def test_list_events_requires_task_scope(monkeypatch):
    monkeypatch.setattr(
        timeline._es,
        "get_events",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not query")),
    )
    assert timeline.list_events() == []
