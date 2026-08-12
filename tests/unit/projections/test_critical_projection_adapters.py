from __future__ import annotations

from copy import deepcopy

from app.canonical.hashing import sha256_text
from app.canonical.projection_ports import ProjectionMessage, ProjectionScope
from app.projections.chroma_story import ChromaStoryProjectionAdapter
from app.projections.handover import HandoverProjectionAdapter
from app.projections.legacy_world import LegacyWorldProjectionAdapter
from app.projections.legacy_scope import LegacyScopeBindingStore
from app.vector_store import VectorStore
from app.writing.subsection_handover_persistence import normalize_history
from app.writing.subsection_handover_history import observation_from_note
from app.world_state import WorldStateManager


SCOPE = ProjectionScope("tenant-1", "project-1")


class FakeBlackboard:
    def __init__(self):
        self.data: dict[tuple[str, str], object] = {}

    def get(self, task_id, key):
        return deepcopy(self.data.get((task_id, key)))

    def set(self, task_id, key, value):
        self.data[(task_id, key)] = deepcopy(value)

    def load_checkpoint(self, task_id):
        return {}


class FakeVectorStore:
    def __init__(self):
        self.rows = {}

    def add_text(self, text, metadata, *, document_id=None):
        self.rows[document_id] = {"text": text.strip(), "metadata": dict(metadata)}
        return document_id

    def list_canonical_chunks(self, *, tenant_id, project_id, task_id):
        return tuple(
            {
                "record_id": row_id,
                "text": row["text"],
                "metadata": row["metadata"],
            }
            for row_id, row in self.rows.items()
            if row["metadata"].get("tenant_id") == tenant_id
            and row["metadata"].get("project_id") == project_id
            and row["metadata"].get("task_id") == task_id
        )

    def delete_canonical_chunks(self, *, tenant_id, project_id, task_id):
        ids = [
            item["record_id"]
            for item in self.list_canonical_chunks(
                tenant_id=tenant_id, project_id=project_id, task_id=task_id
            )
        ]
        for row_id in ids:
            del self.rows[row_id]
        return len(ids)


class ProductionCollection:
    def __init__(self):
        self.rows = {}

    def get(self, *, ids=None, where=None, limit=None, include=None):
        rows = self.rows.items()
        if ids is not None:
            rows = ((row_id, row) for row_id, row in rows if row_id in ids)
        if where is not None:
            clauses = where.get("$and", [where])
            rows = (
                (row_id, row)
                for row_id, row in rows
                if all(
                    all(row["metadata"].get(key) == value for key, value in clause.items())
                    for clause in clauses
                )
            )
        rows = list(rows)
        if limit is not None:
            rows = rows[:limit]
        return {
            "ids": [row_id for row_id, _ in rows],
            "documents": [row["text"] for _, row in rows],
            "metadatas": [row["metadata"] for _, row in rows],
        }

    def add(self, *, ids, documents, metadatas):
        for row_id, text, metadata in zip(ids, documents, metadatas):
            self.rows[row_id] = {"text": text, "metadata": metadata}

    def upsert(self, *, ids, documents, metadatas):
        self.add(ids=ids, documents=documents, metadatas=metadatas)

    def delete(self, ids):
        for row_id in ids:
            self.rows.pop(row_id, None)

    def count(self):
        return len(self.rows)


def production_vector_store():
    store = VectorStore.__new__(VectorStore)
    store._collection = ProductionCollection()
    store._last_search_trace = {}
    return store


def message(projector_id: str) -> ProjectionMessage:
    draft = "Alpha paragraph.\n\nBeta paragraph."
    return ProjectionMessage(
        projection_event_id=f"event-{projector_id}",
        tenant_id=SCOPE.tenant_id,
        project_id=SCOPE.project_id,
        commit_id="commit-1",
        revision_id="revision-1",
        state_version_id="state-1",
        projector_id=projector_id,
        projector_version="v1",
        barrier_kind="critical",
        event_type="canonical.subsection.committed",
        stream_position=7,
        payload={
            "revision": {
                "content": draft,
                "content_hash": sha256_text(draft),
                "metadata": {
                    "task_id": "task-1",
                    "section": 2,
                    "subsection": 3,
                    "title": "Title",
                    "topic": "Topic",
                    "prompt_hash": sha256_text("prompt"),
                    "handover_candidate": {
                        "foreshadowing": "A clue remains",
                        "character_state": "Alert",
                        "open_threads": ["door"],
                        "new_facts": ["  The   bell rings.  ", "Moon rises"],
                    },
                    "generation_metadata": {},
                },
            }
        },
    )


def bind(board, scope=SCOPE, task_id="task-1"):
    return LegacyScopeBindingStore(board).approve(
        task_id=task_id,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        operator_id="operator-1",
        reason="Task 8 controlled migration",
        approved_at="2026-08-12T12:00:00Z",
    )


def assert_duplicate_clear_replay(adapter, projection_message):
    receipt1 = adapter.apply(projection_message)
    receipt2 = adapter.apply(projection_message)
    assert receipt1.content_digest == receipt2.content_digest
    before = adapter.actual_records(SCOPE)
    assert before
    assert adapter.expected_records((projection_message,)) == before
    assert before == tuple(sorted(before, key=lambda item: (item.stream_position, item.record_id)))
    adapter.clear(SCOPE)
    assert adapter.actual_records(SCOPE) == ()
    adapter.apply(projection_message)
    assert adapter.actual_records(SCOPE) == before


def test_world_adapter_uses_deterministic_fact_ids_and_isolated_clear():
    board = FakeBlackboard()
    same_task = WorldStateManager(board, "task-1")
    same_task.upsert_fact(
        fact_id="same-task-other-project",
        category="history",
        fact="Keep same task",
        canonical_tenant_id="tenant-1",
        canonical_project_id="project-other",
        stream_position=1,
        commit_id="commit-other-project",
        revision_id="revision-other-project",
        projection_event_id="event-other-project",
    )
    other = WorldStateManager(board, "other-task")
    other.upsert_fact(
        fact_id="unrelated",
        category="history",
        fact="Keep me",
        canonical_tenant_id="tenant-other",
        canonical_project_id="project-other",
        stream_position=1,
        commit_id="commit-other",
        revision_id="revision-other",
        projection_event_id="event-other",
    )
    adapter = LegacyWorldProjectionAdapter(board, SCOPE, "task-1")

    assert_duplicate_clear_replay(adapter, message("legacy_world_event"))

    ids = [record.record_id for record in adapter.actual_records(SCOPE)]
    assert ids == [
        "world-fact-" + sha256_text("event-legacy_world_event:1:The bell rings."),
        "world-fact-" + sha256_text("event-legacy_world_event:2:Moon rises"),
    ]
    adapter.clear(SCOPE)
    assert WorldStateManager(board, "other-task").get_all_facts()[0]["fact"] == "Keep me"
    assert WorldStateManager(board, "task-1").get_all_facts()[0]["fact"] == "Keep same task"


def test_world_physical_identity_allows_same_logical_id_across_projects():
    board = FakeBlackboard()
    manager = WorldStateManager(board, "task-1")
    common = dict(
        fact_id="logical-same",
        category="history",
        fact="Same fact",
        stream_position=1,
        commit_id="commit-1",
        revision_id="revision-1",
        projection_event_id="event-1",
    )
    manager.upsert_fact(
        **common, canonical_tenant_id="tenant-1", canonical_project_id="project-1"
    )
    manager.upsert_fact(
        **common, canonical_tenant_id="tenant-1", canonical_project_id="project-2"
    )
    manager = WorldStateManager(board, "task-1")
    assert len(manager.get_all_facts()) == 2
    assert len(
        manager.list_projected_facts(tenant_id="tenant-1", project_id="project-1")
    ) == 1


def test_handover_adapter_preserves_content_id_and_unrelated_task():
    board = FakeBlackboard()
    board.set("other-task", "subsection_handover_history_v1", {"sentinel": True})
    adapter = HandoverProjectionAdapter(board, SCOPE, "task-1")
    other_project_message = message("handover_context").model_copy(
        update={
            "project_id": "project-other",
            "commit_id": "commit-other-project",
            "revision_id": "revision-other-project",
            "projection_event_id": "event-other-project",
            "stream_position": 1,
            "payload": {
                "revision": {
                    **message("handover_context").payload["revision"],
                    "content": message("handover_context").payload["revision"]["content"],
                    "content_hash": message("handover_context").payload["revision"]["content_hash"],
                }
            },
        }
    )
    HandoverProjectionAdapter(
        board, ProjectionScope("tenant-1", "project-other"), "task-1"
    ).apply(other_project_message)

    assert_duplicate_clear_replay(adapter, message("handover_context"))

    records = normalize_history(board.get("task-1", "subsection_handover_history_v1"))
    only_id = next(iter(records.records))
    assert sha256_text("task-1") in only_id
    assert len(records.records) == 2
    adapter.clear(SCOPE)
    assert board.get("other-task", "subsection_handover_history_v1") == {"sentinel": True}
    other_records = HandoverProjectionAdapter(
        board, ProjectionScope("tenant-1", "project-other"), "task-1"
    ).actual_records(ProjectionScope("tenant-1", "project-other"))
    assert len(other_records) == 1


def test_handover_adapter_replaces_bound_legacy_content_record_on_clear_replay():
    board = FakeBlackboard()
    adapter = HandoverProjectionAdapter(board, SCOPE, "task-1")
    projection_message = message("handover_context")
    metadata = projection_message.payload["revision"]["metadata"]
    note = metadata["handover_candidate"]
    adapter.recorder.capture_committed(
        section=2,
        subsection=3,
        output_sha256=projection_message.payload["revision"]["content_hash"],
        prompt_messages_hash=metadata["prompt_hash"],
        commit_idempotency_key=projection_message.commit_id,
        handover_note=note,
        observation=observation_from_note(note),
    )
    import pytest

    with pytest.raises(ValueError, match="binding|ownership"):
        adapter.actual_records(SCOPE)

    bind(board)
    adapter.clear(SCOPE)
    adapter.apply(projection_message)

    assert adapter.actual_records(SCOPE) == adapter.expected_records((projection_message,))


def test_chroma_adapter_uses_deterministic_ids_metadata_and_isolated_clear(monkeypatch):
    store = FakeVectorStore()
    store.rows["other"] = {
        "text": "keep",
        "metadata": {
            "tenant_id": "tenant-1",
            "project_id": "project-other",
            "task_id": "task-other",
        },
    }
    adapter = ChromaStoryProjectionAdapter(store, SCOPE, "task-1", chunk_size=17, overlap=0)

    assert_duplicate_clear_replay(adapter, message("chroma_story_chunks"))

    records = adapter.actual_records(SCOPE)
    assert all(record.payload["metadata"]["project_id"] == "project-1" for record in records)
    assert all(record.payload["metadata"]["stream_position"] == 7 for record in records)
    adapter.clear(SCOPE)
    assert "other" in store.rows


def test_adapter_rejects_scope_or_task_mismatch():
    adapter = LegacyWorldProjectionAdapter(FakeBlackboard(), SCOPE, "task-1")
    wrong_scope = message("legacy_world_event").model_copy(update={"project_id": "other"})
    wrong_task = message("legacy_world_event").model_copy(
        update={
            "payload": {
                **message("legacy_world_event").payload,
                "revision": {
                    **message("legacy_world_event").payload["revision"],
                    "metadata": {
                        **message("legacy_world_event").payload["revision"]["metadata"],
                        "task_id": "other-task",
                    },
                },
            }
        }
    )

    import pytest

    with pytest.raises(ValueError, match="scope"):
        adapter.apply(wrong_scope)
    with pytest.raises(ValueError, match="task"):
        adapter.apply(wrong_task)


def test_adapters_reject_wrong_projector_version_or_barrier():
    import pytest

    adapters = (
        (LegacyWorldProjectionAdapter(FakeBlackboard(), SCOPE, "task-1"), "legacy_world_event"),
        (HandoverProjectionAdapter(FakeBlackboard(), SCOPE, "task-1"), "handover_context"),
        (ChromaStoryProjectionAdapter(FakeVectorStore(), SCOPE, "task-1"), "chroma_story_chunks"),
    )
    for adapter, projector_id in adapters:
        valid = message(projector_id)
        for mutation in (
            {"projector_id": "analytics"},
            {"projector_version": "v999"},
            {"barrier_kind": "non_blocking"},
        ):
            with pytest.raises(ValueError, match="spec"):
                adapter.apply(valid.model_copy(update=mutation))


def test_vector_store_canonical_document_id_never_uses_legacy_content_dedupe():
    store = production_vector_store()
    metadata = {"task_id": "task-1", "project_id": "project-1"}
    assert store.add_text("same", metadata, document_id="canonical-1") == "canonical-1"
    assert store.add_text("same", metadata, document_id="canonical-2") == "canonical-2"
    assert set(store._collection.rows) == {"canonical-1", "canonical-2"}

    legacy = production_vector_store()
    first = legacy.add_text("same", metadata)
    second = legacy.add_text("same", metadata)
    assert first == second
    assert len(legacy._collection.rows) == 1


def test_chroma_adapter_rejects_sink_returning_wrong_id():
    import pytest

    class WrongIdStore(FakeVectorStore):
        def add_text(self, text, metadata, *, document_id=None):
            super().add_text(text, metadata, document_id="wrong-id")
            return "wrong-id"

    adapter = ChromaStoryProjectionAdapter(WrongIdStore(), SCOPE, "task-1")
    with pytest.raises(RuntimeError, match="converge|identity"):
        adapter.apply(message("chroma_story_chunks"))


def test_handover_semantic_upsert_is_latest_position_and_order_independent():
    board = FakeBlackboard()
    adapter = HandoverProjectionAdapter(board, SCOPE, "task-1")
    first = message("handover_context").model_copy(
        update={"commit_id": "commit-1", "revision_id": "revision-1", "stream_position": 1}
    )
    second = first.model_copy(
        update={"commit_id": "commit-2", "revision_id": "revision-2", "stream_position": 2}
    )

    adapter.apply(second)
    adapter.apply(first)
    actual = adapter.actual_records(SCOPE)
    assert len(actual) == 1
    assert actual[0].commit_id == "commit-2"
    assert actual[0].stream_position == 2
    assert adapter.expected_records((first, second)) == actual
    assert adapter.expected_records((second, first)) == actual


def test_handover_same_position_conflict_fails_closed():
    import pytest

    board = FakeBlackboard()
    adapter = HandoverProjectionAdapter(board, SCOPE, "task-1")
    first = message("handover_context")
    conflict = first.model_copy(update={"commit_id": "commit-conflict", "revision_id": "revision-x"})
    adapter.apply(first)
    with pytest.raises(ValueError, match="conflict"):
        adapter.apply(conflict)


def test_legacy_binding_is_explicit_durable_and_conflicting_rebind_fails():
    import pytest

    board = FakeBlackboard()
    store = LegacyScopeBindingStore(board)
    approved = bind(board)
    assert store.require(task_id="task-1", scope=SCOPE) == approved
    assert bind(board) == approved
    with pytest.raises(ValueError, match="bound|conflict"):
        store.approve(
            task_id="task-1",
            tenant_id="tenant-1",
            project_id="project-other",
            operator_id="operator-2",
            reason="must not steal",
            approved_at="2026-08-12T13:00:00Z",
        )


def test_legacy_binding_round_trips_real_blackboard_json_strings():
    import json

    class JsonBlackboard(FakeBlackboard):
        def get(self, task_id, key):
            value = super().get(task_id, key)
            return json.dumps(value) if isinstance(value, dict) else value

    board = JsonBlackboard()
    approved = bind(board)
    assert LegacyScopeBindingStore(board).require(task_id="task-1", scope=SCOPE) == approved


def test_unmarked_handover_requires_exact_binding_before_migration_or_clear():
    import pytest

    board = FakeBlackboard()
    recorder = HandoverProjectionAdapter(board, SCOPE, "task-1").recorder
    projection_message = message("handover_context")
    note = projection_message.payload["revision"]["metadata"]["handover_candidate"]
    recorder.capture_committed(
        section=2,
        subsection=3,
        output_sha256=projection_message.payload["revision"]["content_hash"],
        prompt_messages_hash="prompt",
        commit_idempotency_key="legacy",
        handover_note=note,
        observation=observation_from_note(note),
    )
    adapter = HandoverProjectionAdapter(board, SCOPE, "task-1")
    with pytest.raises(ValueError, match="binding|ownership"):
        adapter.actual_records(SCOPE)
    with pytest.raises(ValueError, match="binding|ownership"):
        adapter.clear(SCOPE)

    bind(board)
    adapter.clear(SCOPE)
    adapter.apply(projection_message)
    assert adapter.actual_records(SCOPE) == adapter.expected_records((projection_message,))


def test_unmarked_world_requires_binding_and_clear_replay_does_not_touch_neighbor():
    import pytest

    board = FakeBlackboard()
    legacy = WorldStateManager(board, "task-1")
    legacy.add_fact("history", "Legacy target fact")
    neighbor = WorldStateManager(board, "neighbor-task")
    neighbor.add_fact("history", "Neighbor fact")
    adapter = LegacyWorldProjectionAdapter(board, SCOPE, "task-1")
    with pytest.raises(ValueError, match="binding|ownership"):
        adapter.actual_records(SCOPE)
    bind(board)
    adapter.clear(SCOPE)
    adapter.apply(message("legacy_world_event"))
    assert len(adapter.actual_records(SCOPE)) == 2
    assert WorldStateManager(board, "neighbor-task").get_all_facts()[0]["fact"] == "Neighbor fact"
