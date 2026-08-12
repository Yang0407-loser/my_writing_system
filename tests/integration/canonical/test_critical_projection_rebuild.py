from __future__ import annotations

from app.canonical.hashing import sha256_text
from app.canonical.projection_ports import ProjectionMessage, ProjectionScope
from app.projections.chroma_story import ChromaStoryProjectionAdapter
from app.vector_store import VectorStore


class TinyEmbeddingProvider:
    def embed(self, text):
        return self.embed_batch([text])[0]

    def embed_batch(self, texts):
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    @property
    def dimension(self):
        return 3

    @property
    def model_name(self):
        return "test-tiny-v1"


def _message(scope, task_id, commit_id, position, draft):
    return ProjectionMessage(
        projection_event_id=f"event-{commit_id}",
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        commit_id=commit_id,
        revision_id=f"revision-{commit_id}",
        state_version_id=f"state-{commit_id}",
        projector_id="chroma_story_chunks",
        projector_version="v1",
        barrier_kind="critical",
        event_type="canonical.subsection.committed",
        stream_position=position,
        payload={
            "revision": {
                "content": draft,
                "content_hash": sha256_text(draft),
                "metadata": {
                    "task_id": task_id,
                    "section": 1,
                    "subsection": 1,
                    "title": "Persistent",
                    "topic": "Rebuild",
                },
            }
        },
    )


def _open_store(monkeypatch, path):
    monkeypatch.setattr("app.vector_store.settings.CHROMA_DATA_PATH", str(path))
    monkeypatch.setattr(
        "app.vector_store.get_embedding_provider", lambda: TinyEmbeddingProvider()
    )
    return VectorStore()


def test_chroma_reopen_clear_and_replay_converges_without_cross_scope_delete(
    tmp_path, monkeypatch
):
    path = tmp_path / "critical-chroma"
    scope = ProjectionScope("tenant-reopen", "project-reopen")
    other_scope = ProjectionScope("tenant-reopen", "project-other")
    target = _message(scope, "task-reopen", "commit-target", 3, "One.\n\nTwo.")
    other = _message(other_scope, "task-other", "commit-other", 4, "Keep this.")

    store = _open_store(monkeypatch, path)
    adapter = ChromaStoryProjectionAdapter(store, scope, "task-reopen", chunk_size=8, overlap=0)
    other_adapter = ChromaStoryProjectionAdapter(
        store, other_scope, "task-other", chunk_size=8, overlap=0
    )
    adapter.apply(target)
    other_adapter.apply(other)
    before = adapter.actual_records(scope)
    other_before = other_adapter.actual_records(other_scope)
    del adapter, other_adapter, store

    reopened = _open_store(monkeypatch, path)
    adapter = ChromaStoryProjectionAdapter(
        reopened, scope, "task-reopen", chunk_size=8, overlap=0
    )
    other_adapter = ChromaStoryProjectionAdapter(
        reopened, other_scope, "task-other", chunk_size=8, overlap=0
    )
    assert adapter.actual_records(scope) == before
    adapter.clear(scope)
    assert adapter.actual_records(scope) == ()
    assert other_adapter.actual_records(other_scope) == other_before
    adapter.apply(target)
    del adapter, other_adapter, reopened

    replay_reopened = _open_store(monkeypatch, path)
    replay_adapter = ChromaStoryProjectionAdapter(
        replay_reopened, scope, "task-reopen", chunk_size=8, overlap=0
    )
    assert replay_adapter.actual_records(scope) == before
