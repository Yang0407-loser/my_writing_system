from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Barrier, Lock, Thread

from app.canonical.hashing import sha256_text
from app.canonical.projection_ports import ProjectionMessage, ProjectionScope
from app.projections.chroma_story import ChromaStoryProjectionAdapter
from app.projections.handover import HandoverProjectionAdapter
from app.projections.legacy_world import LegacyWorldProjectionAdapter
from app.projections.legacy_scope import LegacyScopeBindingStore
from app.blackboard import Blackboard
from app.vector_store import VectorStore
from app.writing.subsection_handover_history import observation_from_note
from app.world_state import WorldStateManager


SCOPE = ProjectionScope("tenant-1", "project-1")


class FakeBlackboard:
    def __init__(self):
        self.data: dict[tuple[str, str], object] = {}
        self.hashes: dict[str, dict[str, object]] = {}
        self.lock = Lock()

    def get(self, task_id, key):
        return deepcopy(self.data.get((task_id, key)))

    def set(self, task_id, key, value):
        self.data[(task_id, key)] = deepcopy(value)

    def set_if_absent(self, task_id, key, value):
        with self.lock:
            if (task_id, key) in self.data:
                return False
            self.data[(task_id, key)] = deepcopy(value)
            return True

    def hash_get(self, namespace, field):
        return deepcopy(self.hashes.get(namespace, {}).get(field))

    def hash_get_all(self, namespace):
        return deepcopy(self.hashes.get(namespace, {}))

    def hash_set(self, namespace, field, value):
        with self.lock:
            self.hashes.setdefault(namespace, {})[field] = deepcopy(value)

    def hash_delete(self, namespace, *fields):
        with self.lock:
            values = self.hashes.get(namespace, {})
            removed = sum(field in values for field in fields)
            for field in fields:
                values.pop(field, None)
            return removed

    def hash_upsert_by_position(self, namespace, field, value, position):
        with self.lock:
            existing = self.hashes.setdefault(namespace, {}).get(field)
            if existing is None:
                self.hashes[namespace][field] = deepcopy(value)
                return "inserted"
            old_position = existing["stream_position"]
            if position < old_position:
                return "stale"
            if position == old_position:
                old_semantic = {key: item for key, item in existing.items() if key != "created_at"}
                new_semantic = {key: item for key, item in value.items() if key != "created_at"}
                return "identical" if old_semantic == new_semantic else "conflict"
            self.hashes[namespace][field] = deepcopy(value)
            return "updated"

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
    assert WorldStateManager(board, "other-task").list_projected_facts(
        tenant_id="tenant-other", project_id="project-other"
    )[0]["fact"] == "Keep me"
    assert WorldStateManager(board, "task-1").list_projected_facts(
        tenant_id="tenant-1", project_id="project-other"
    )[0]["fact"] == "Keep same task"


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
    assert manager.get_all_facts() == []
    assert len(
        manager.list_projected_facts(tenant_id="tenant-1", project_id="project-1")
    ) == 1


def test_world_interleaved_preconstructed_adapters_do_not_overwrite_neighbor_scope():
    board = FakeBlackboard()
    p1 = LegacyWorldProjectionAdapter(board, SCOPE, "task-1")
    p2_scope = ProjectionScope("tenant-1", "project-2")
    p2 = LegacyWorldProjectionAdapter(board, p2_scope, "task-1")
    p2_message = message("legacy_world_event").model_copy(
        update={"project_id": "project-2", "projection_event_id": "event-p2"}
    )

    p1.apply(message("legacy_world_event"))
    p2.apply(p2_message)

    assert len(LegacyWorldProjectionAdapter(board, SCOPE, "task-1").actual_records(SCOPE)) == 2
    assert len(LegacyWorldProjectionAdapter(board, p2_scope, "task-1").actual_records(p2_scope)) == 2
    p1.clear(SCOPE)
    assert len(LegacyWorldProjectionAdapter(board, p2_scope, "task-1").actual_records(p2_scope)) == 2


def test_world_legacy_runtime_does_not_read_canonical_neighbor_records():
    board = FakeBlackboard()
    adapter = LegacyWorldProjectionAdapter(board, SCOPE, "task-1")
    adapter.apply(message("legacy_world_event"))

    runtime = WorldStateManager(board, "task-1")
    assert runtime.get_all_facts() == []
    assert runtime.query_relevant([], top_k=8) == []
    assert len(runtime.list_projected_facts(tenant_id="tenant-1", project_id="project-1")) == 2


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

    records = adapter.actual_records(SCOPE)
    assert sha256_text("task-1") in records[0].record_id
    adapter.clear(SCOPE)
    assert board.get("other-task", "subsection_handover_history_v1") == {"sentinel": True}
    other_records = HandoverProjectionAdapter(
        board, ProjectionScope("tenant-1", "project-other"), "task-1"
    ).actual_records(ProjectionScope("tenant-1", "project-other"))
    assert len(other_records) == 1


def test_handover_concurrent_project_writes_do_not_overwrite_each_other():
    board = FakeBlackboard()
    p2_scope = ProjectionScope("tenant-1", "project-2")
    adapters = (
        HandoverProjectionAdapter(board, SCOPE, "task-1"),
        HandoverProjectionAdapter(board, p2_scope, "task-1"),
    )
    messages = (
        message("handover_context"),
        message("handover_context").model_copy(
            update={"project_id": "project-2", "projection_event_id": "event-p2"}
        ),
    )
    failures = []
    threads = [
        Thread(target=lambda a=a, m=m: _capture_thread_failure(failures, a.apply, m))
        for a, m in zip(adapters, messages)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert failures == []
    assert len(adapters[0].actual_records(SCOPE)) == 1
    assert len(adapters[1].actual_records(p2_scope)) == 1


def _capture_thread_failure(failures, operation, *args):
    try:
        operation(*args)
    except Exception as error:
        failures.append(error)


def test_handover_legacy_checkpoint_history_excludes_canonical_records():
    from app.writing.subsection_handover_persistence import history_for_checkpoint

    board = FakeBlackboard()
    HandoverProjectionAdapter(board, SCOPE, "task-1").apply(message("handover_context"))

    assert history_for_checkpoint(board, "task-1") is None
    scoped = HandoverProjectionAdapter(board, SCOPE, "task-1").recorder.list_canonical_records(
        tenant_id="tenant-1", project_id="project-1"
    )
    assert len(scoped) == 1


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


def test_legacy_binding_conflicting_concurrent_approval_has_one_durable_winner():
    class RacingBoard(FakeBlackboard):
        def __init__(self):
            super().__init__()
            self.empty_read_barrier = Barrier(2)

        def get(self, task_id, key):
            value = super().get(task_id, key)
            if key == "canonical_legacy_scope_binding_v1" and value is None:
                self.empty_read_barrier.wait(timeout=5)
            return value

    board = RacingBoard()
    results, failures = [], []

    def approve(project_id):
        try:
            results.append(
                LegacyScopeBindingStore(board).approve(
                    task_id="task-1",
                    tenant_id="tenant-1",
                    project_id=project_id,
                    operator_id=" operator ",
                    reason=" controlled migration ",
                    approved_at="2026-08-12T12:00:00Z",
                )
            )
        except Exception as error:
            failures.append(error)

    threads = [Thread(target=approve, args=(project,)) for project in ("project-1", "project-2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 1
    assert len(failures) == 1
    winner = LegacyScopeBindingStore(board).get(task_id="task-1")
    assert winner == results[0]
    assert winner.operator_id == "operator"
    assert winner.reason == "controlled migration"
    parsed = datetime.fromisoformat(winner.approved_at.replace("Z", "+00:00"))
    assert parsed.tzinfo == timezone.utc


def test_legacy_binding_generates_utc_timestamp_and_rejects_blank_audit_fields():
    import pytest

    board = FakeBlackboard()
    generated = LegacyScopeBindingStore(board).approve(
        task_id="task-1",
        tenant_id="tenant-1",
        project_id="project-1",
        operator_id="operator",
        reason="migration",
        approved_at=None,
    )
    assert datetime.fromisoformat(generated.approved_at).utcoffset().total_seconds() == 0
    assert LegacyScopeBindingStore(board).approve(
        task_id="task-1",
        tenant_id="tenant-1",
        project_id="project-1",
        operator_id="operator",
        reason="migration",
        approved_at=None,
    ) == generated
    for field in ("operator_id", "reason"):
        kwargs = {
            "task_id": "other-task",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "operator_id": "operator",
            "reason": "migration",
            "approved_at": "2026-08-12T12:00:00Z",
        }
        kwargs[field] = "   "
        with pytest.raises(ValueError):
            LegacyScopeBindingStore(FakeBlackboard()).approve(**kwargs)


def test_real_blackboard_binding_approval_uses_atomic_setnx():
    import fakeredis

    board = Blackboard()
    board._redis = fakeredis.FakeRedis()
    winners, failures = [], []
    barrier = Barrier(2)

    def approve(project_id):
        barrier.wait(timeout=5)
        try:
            winners.append(
                LegacyScopeBindingStore(board).approve(
                    task_id="task-atomic",
                    tenant_id="tenant-1",
                    project_id=project_id,
                    operator_id="operator",
                    reason="migration",
                    approved_at="2026-08-12T12:00:00Z",
                )
            )
        except Exception as error:
            failures.append(error)

    threads = [Thread(target=approve, args=(project,)) for project in ("project-1", "project-2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(winners) == 1
    assert len(failures) == 1
    assert LegacyScopeBindingStore(board).get(task_id="task-atomic") == winners[0]


def test_real_blackboard_concurrent_implicit_timestamp_returns_one_durable_binding(
    monkeypatch,
):
    import fakeredis
    import app.projections.legacy_scope as legacy_scope

    class RacingBlackboard(Blackboard):
        def __init__(self):
            self._redis = fakeredis.FakeRedis()
            self.empty_reads = Barrier(2)

        def get(self, task_id, key):
            value = super().get(task_id, key)
            if key == "canonical_legacy_scope_binding_v1" and value is None:
                self.empty_reads.wait(timeout=5)
            return value

    class SequencedDatetime(datetime):
        counter = 0
        counter_lock = Lock()

        @classmethod
        def now(cls, tz=None):
            with cls.counter_lock:
                cls.counter += 1
                ordinal = cls.counter
            return datetime(2026, 8, 12, 12, 0, 0, ordinal, tzinfo=timezone.utc)

    monkeypatch.setattr(legacy_scope, "datetime", SequencedDatetime)
    board = RacingBlackboard()
    results, failures = [], []

    def approve():
        try:
            results.append(
                LegacyScopeBindingStore(board).approve(
                    task_id="task-implicit-race",
                    tenant_id="tenant-1",
                    project_id="project-1",
                    operator_id="operator",
                    reason="migration",
                    approved_at=None,
                )
            )
        except Exception as error:
            failures.append(error)

    threads = [Thread(target=approve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert failures == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert LegacyScopeBindingStore(board).get(task_id="task-implicit-race") == results[0]


def test_legacy_binding_explicit_timestamp_retry_remains_strict():
    import pytest

    board = FakeBlackboard()
    bind(board)
    with pytest.raises(ValueError, match="bound|conflict"):
        LegacyScopeBindingStore(board).approve(
            task_id="task-1",
            tenant_id="tenant-1",
            project_id="project-1",
            operator_id="operator-1",
            reason="Task 8 controlled migration",
            approved_at="2026-08-12T12:00:01Z",
        )


def test_real_blackboard_scoped_world_and_handover_writes_survive_concurrency():
    import fakeredis

    board = Blackboard()
    board._redis = fakeredis.FakeRedis()
    p2_scope = ProjectionScope("tenant-1", "project-2")
    cases = (
        (LegacyWorldProjectionAdapter, "legacy_world_event", 2),
        (HandoverProjectionAdapter, "handover_context", 1),
    )
    for adapter_type, projector_id, count in cases:
        adapters = (
            adapter_type(board, SCOPE, "task-real"),
            adapter_type(board, p2_scope, "task-real"),
        )
        base = message(projector_id).model_copy(
            update={
                "payload": {
                    "revision": {
                        **message(projector_id).payload["revision"],
                        "metadata": {
                            **message(projector_id).payload["revision"]["metadata"],
                            "task_id": "task-real",
                        },
                    }
                }
            }
        )
        messages = (
            base,
            base.model_copy(
                update={"project_id": "project-2", "projection_event_id": f"event-{projector_id}-p2"}
            ),
        )
        barrier = Barrier(2)
        failures = []

        def apply(adapter, projection_message):
            barrier.wait(timeout=5)
            _capture_thread_failure(failures, adapter.apply, projection_message)

        threads = [
            Thread(target=apply, args=(adapter, projection_message))
            for adapter, projection_message in zip(adapters, messages)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert failures == []
        assert len(adapters[0].actual_records(SCOPE)) == count
        assert len(adapters[1].actual_records(p2_scope)) == count
        adapters[0].clear(SCOPE)
        assert len(adapters[1].actual_records(p2_scope)) == count


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


def test_world_partial_canonical_markers_fail_closed_for_all_operations():
    import pytest

    for marker, value in (
        ("canonical_tenant_id", "tenant-1"),
        ("stream_position", 7),
        ("commit_id", "commit-1"),
    ):
        board = FakeBlackboard()
        legacy = WorldStateManager(board, "task-1")
        legacy.add_fact("history", "Malformed")
        payload = board.get("task-1", "world_state")
        payload["facts"][0][marker] = value
        board.set("task-1", "world_state", payload)
        adapter = LegacyWorldProjectionAdapter(board, SCOPE, "task-1")
        for operation in (
            lambda: adapter.actual_records(SCOPE),
            lambda: adapter.apply(message("legacy_world_event")),
            lambda: adapter.clear(SCOPE),
        ):
            with pytest.raises(ValueError, match="malformed|marker|identity"):
                operation()


def test_world_partial_markers_inside_canonical_namespace_block_all_operations():
    import pytest

    for operation_name in ("actual", "apply", "clear"):
        board = FakeBlackboard()
        adapter = LegacyWorldProjectionAdapter(board, SCOPE, "task-1")
        adapter.apply(message("legacy_world_event"))
        namespace = adapter.manager._canonical_namespace("tenant-1", "project-1")
        for payload in board.hashes[namespace].values():
            del payload["revision_id"]
        operation = {
            "actual": lambda: adapter.actual_records(SCOPE),
            "apply": lambda: adapter.apply(message("legacy_world_event")),
            "clear": lambda: adapter.clear(SCOPE),
        }[operation_name]
        with pytest.raises(ValueError, match="malformed|marker|identity"):
            operation()
        assert board.hashes[namespace]


def test_world_old_envelope_canonical_records_are_not_legacy_runtime_ghosts():
    board = FakeBlackboard()
    legacy = WorldStateManager(board, "task-1")
    legacy.add_fact("history", "Legacy visible")
    payload = board.get("task-1", "world_state")
    old_canonical = deepcopy(payload["facts"][0])
    old_canonical.update(
        {
            "fact_id": "old-canonical",
            "fact": "Canon hidden",
            "canonical_tenant_id": "tenant-1",
            "canonical_project_id": "project-1",
            "stream_position": 1,
            "commit_id": "commit-old",
            "revision_id": "revision-old",
            "projection_event_id": "event-old",
        }
    )
    payload["facts"].append(old_canonical)
    neighbor_canonical = deepcopy(old_canonical)
    neighbor_canonical.update(
        {
            "fact_id": "old-neighbor",
            "fact": "Neighbor Canon preserved",
            "canonical_project_id": "project-2",
        }
    )
    payload["facts"].append(neighbor_canonical)
    board.set("task-1", "world_state", payload)

    runtime = WorldStateManager(board, "task-1")
    assert [item["fact"] for item in runtime.get_all_facts()] == ["Legacy visible"]
    assert [item["fact"] for item in runtime.serialize()["facts"]] == ["Legacy visible"]
    adapter = LegacyWorldProjectionAdapter(board, SCOPE, "task-1")
    bind(board)
    adapter.clear(SCOPE)
    assert WorldStateManager(board, "task-1").get_all_facts() == []
    assert adapter.actual_records(SCOPE) == ()
    neighbor_scope = ProjectionScope("tenant-1", "project-2")
    assert [
        record.record_id
        for record in LegacyWorldProjectionAdapter(
            board, neighbor_scope, "task-1"
        ).actual_records(neighbor_scope)
    ] == ["old-neighbor"]


def test_handover_partial_canonical_markers_fail_closed_for_all_operations():
    import pytest

    for marker, value in (
        ("canonical_tenant_id", "tenant-1"),
        ("stream_position", 7),
        ("revision_id", "revision-1"),
    ):
        board = FakeBlackboard()
        recorder = HandoverProjectionAdapter(board, SCOPE, "task-1").recorder
        note = message("handover_context").payload["revision"]["metadata"]["handover_candidate"]
        recorder.capture_committed(
            section=2,
            subsection=3,
            output_sha256="legacy-hash",
            prompt_messages_hash="legacy-prompt",
            commit_idempotency_key="legacy-commit",
            handover_note=note,
            observation=observation_from_note(note),
        )
        payload = board.get("task-1", "subsection_handover_history_v1")
        record = next(iter(payload["records"].values()))
        record[marker] = value
        board.set("task-1", "subsection_handover_history_v1", payload)
        adapter = HandoverProjectionAdapter(board, SCOPE, "task-1")
        for operation in (
            lambda: adapter.actual_records(SCOPE),
            lambda: adapter.apply(message("handover_context")),
            lambda: adapter.clear(SCOPE),
        ):
            with pytest.raises(ValueError, match="malformed|marker|identity"):
                operation()


def test_handover_partial_markers_inside_canonical_namespace_block_all_operations():
    import pytest

    for operation_name in ("actual", "apply", "clear"):
        board = FakeBlackboard()
        adapter = HandoverProjectionAdapter(board, SCOPE, "task-1")
        adapter.apply(message("handover_context"))
        namespace = adapter.recorder._canonical_namespace("tenant-1", "project-1")
        record_id, payload = next(iter(board.hashes[namespace].items()))
        del payload["revision_id"]
        operation = {
            "actual": lambda: adapter.actual_records(SCOPE),
            "apply": lambda: adapter.apply(message("handover_context")),
            "clear": lambda: adapter.clear(SCOPE),
        }[operation_name]
        with pytest.raises(ValueError, match="malformed|marker|identity"):
            operation()
        assert record_id in board.hashes[namespace]


def test_handover_old_envelope_canonical_record_is_not_checkpoint_ghost():
    from app.writing.subsection_handover_persistence import history_for_checkpoint

    board = FakeBlackboard()
    recorder = HandoverProjectionAdapter(board, SCOPE, "task-1").recorder
    note = message("handover_context").payload["revision"]["metadata"]["handover_candidate"]
    recorder.capture_committed(
        section=2,
        subsection=3,
        output_sha256="legacy-visible",
        prompt_messages_hash="legacy-prompt",
        commit_idempotency_key="legacy-commit",
        handover_note=note,
        observation=observation_from_note(note),
    )
    payload = board.get("task-1", "subsection_handover_history_v1")
    old_id, old_canonical = next(iter(payload["records"].items()))
    old_canonical = deepcopy(old_canonical)
    old_canonical.update(
        {
            "record_id": old_id + ":canonical",
            "canonical_tenant_id": "tenant-1",
            "canonical_project_id": "project-1",
            "stream_position": 2,
            "revision_id": "revision-old",
        }
    )
    payload["records"][old_canonical["record_id"]] = old_canonical
    board.set("task-1", "subsection_handover_history_v1", payload)

    checkpoint = history_for_checkpoint(board, "task-1")
    assert list(checkpoint["records"]) == [old_id]
    adapter = HandoverProjectionAdapter(board, SCOPE, "task-1")
    bind(board)
    adapter.clear(SCOPE)
    assert history_for_checkpoint(board, "task-1") is None
    assert adapter.actual_records(SCOPE) == ()
