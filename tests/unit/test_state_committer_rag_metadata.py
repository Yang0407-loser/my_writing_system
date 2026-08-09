"""StateCommitter WR3.5 rag_metadata hook: additive and default-off."""

from __future__ import annotations

from app.writing.state_committer import StateCommitter


class _FakeVectorStore:
    def __init__(self) -> None:
        self.added: list[tuple[str, dict]] = []

    def add_text(self, text: str, metadata: dict) -> str:
        self.added.append((text, dict(metadata)))
        return f"id-{len(self.added)}"

    def enforce_task_limit(self, task_id: str) -> int:
        return 0


class _FakeContextManager:
    def add_subsection(self, draft: str, section: int) -> None:
        pass


class _FakeBlackboard:
    def set(self, task_id: str, key: str, value) -> None:
        pass


def _commit(rag_metadata=None):
    committer = StateCommitter()
    store = _FakeVectorStore()
    artifact = committer.commit_subsection(
        idempotency_key="test:1:1",
        source_hash="source-hash",
        draft="第一段正文。\n\n第二段正文。",
        validation_complete=True,
        vector_store=store,
        context_manager=_FakeContextManager(),
        blackboard=_FakeBlackboard(),
        task_id="task-1",
        section=1,
        subsection=1,
        title="小节标题",
        topic="主题",
        rag_metadata=rag_metadata,
    )
    return artifact, store


def test_rag_metadata_is_additive_when_provided() -> None:
    _, store = _commit(
        rag_metadata={
            "characters": ["林晚", "周野"],
            "locations": ["操作间"],
            "weekday": ["周六"],
            "metadata_source": "world_runtime_wr3.5",
        }
    )
    assert store.added
    for _, metadata in store.added:
        assert metadata["characters"] == ["林晚", "周野"]
        assert metadata["locations"] == ["操作间"]
        assert metadata["weekday"] == ["周六"]
        assert metadata["metadata_source"] == "world_runtime_wr3.5"
        assert metadata["task_id"] == "task-1"
        assert metadata["section"] == 1
        assert metadata["title"] == "小节标题"


def test_legacy_metadata_unchanged_without_rag_metadata() -> None:
    _, store = _commit()
    assert store.added
    for _, metadata in store.added:
        assert set(metadata) == {
            "task_id", "section", "subsection", "title", "topic",
        }
