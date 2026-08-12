from __future__ import annotations

from copy import deepcopy

from app.canonical.hashing import sha256_text
from app.canonical.projection_ports import ProjectionMessage, ProjectionScope
from app.projections.chroma_story import ChromaStoryProjectionAdapter
from app.projections.handover import HandoverProjectionAdapter
from app.projections.legacy_world import LegacyWorldProjectionAdapter
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
                    "content": "Other content",
                    "content_hash": sha256_text("Other content"),
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
    adapter.clear(SCOPE)
    assert board.get("other-task", "subsection_handover_history_v1") == {"sentinel": True}
    other_records = HandoverProjectionAdapter(
        board, ProjectionScope("tenant-1", "project-other"), "task-1"
    ).actual_records(ProjectionScope("tenant-1", "project-other"))
    assert len(other_records) == 1


def test_handover_adapter_claims_matching_legacy_content_record_for_canon_scope():
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
    assert adapter.actual_records(SCOPE) == ()

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
