import pytest

from app.event_shadow_store import EventShadowStore, INDEX_PROFILE, derive_shadow_task_id, shadow_filter


def test_shadow_task_id_is_stable_and_distinct():
    source = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"
    assert derive_shadow_task_id(source) == derive_shadow_task_id(source)
    assert derive_shadow_task_id(source) != source


def test_shadow_filter_requires_all_three_isolation_dimensions():
    task = derive_shadow_task_id("source")
    assert shadow_filter(task) == {"$and": [
        {"task_id": task}, {"index_profile": INDEX_PROFILE}, {"chunk_level": "event"}
    ]}


@pytest.mark.parametrize("value", ["", "*"])
def test_shadow_identifiers_reject_empty_or_wildcard(value):
    with pytest.raises(ValueError):
        derive_shadow_task_id(value)
    with pytest.raises(ValueError):
        shadow_filter(value)


def test_cleanup_refuses_production_task_wrong_profile_and_wildcard_ids():
    store = EventShadowStore.__new__(EventShadowStore)
    store.source_task_id = "production"
    store.shadow_task_id = derive_shadow_task_id("production")
    with pytest.raises(ValueError):
        store.cleanup_exact(task_id="production", index_profile=INDEX_PROFILE, event_ids=["event-1"])
    with pytest.raises(ValueError):
        store.cleanup_exact(task_id=store.shadow_task_id, index_profile="wrong", event_ids=["event-1"])
    with pytest.raises(ValueError):
        store.cleanup_exact(task_id=store.shadow_task_id, index_profile=INDEX_PROFILE, event_ids=["*"])
