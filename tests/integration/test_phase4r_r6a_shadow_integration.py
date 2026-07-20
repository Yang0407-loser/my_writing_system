import hashlib
from unittest.mock import MagicMock

from app.writing.shadow_validation import InMemoryShadowValidationSink, ShadowBoundaryValidationRunner
from app.writing.state_committer import StateCommitter


class VectorStore:
    def add_text(self, **kwargs):
        pass

    def enforce_task_limit(self, task_id):
        pass


class ContextManager:
    def __init__(self):
        self.drafts = []

    def add_subsection(self, text, section):
        self.drafts.append((text, section))


def test_shadow_error_after_commit_cannot_rollback_or_change_checkpoint_contract():
    text = "已经提交的正文。"
    committer = StateCommitter()
    context = ContextManager()
    artifact = committer.commit_subsection(
        idempotency_key="task:1:1", source_hash="messages-hash", draft=text,
        validation_complete=True, vector_store=VectorStore(), context_manager=context,
        blackboard=MagicMock(), task_id="task", section=1, subsection=1,
        title="标题", topic="主题",
    )

    sink = InMemoryShadowValidationSink()
    runner = ShadowBoundaryValidationRunner(
        enabled=True, sink=sink,
        scene_spec_provider=lambda *_: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    record = runner.observe_committed(
        task_id="task", section=1, subsection=1, text=text,
        output_hash=artifact.output_hash, source_manifest=[],
    )

    assert context.drafts == [(text, 1)]
    assert artifact.checkpoint_version == StateCommitter.CHECKPOINT_VERSION
    assert artifact.idempotency_key == "task:1:1"
    assert artifact.output_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert record["validation_status"] == "shadow_error"
    duplicate = committer.commit_subsection(
        idempotency_key="task:1:1", source_hash="messages-hash", draft=text,
        validation_complete=True, vector_store=VectorStore(), context_manager=context,
        blackboard=MagicMock(), task_id="task", section=1, subsection=1,
        title="标题", topic="主题",
    )
    assert duplicate.skipped_as_duplicate is True
    assert context.drafts == [(text, 1)]
