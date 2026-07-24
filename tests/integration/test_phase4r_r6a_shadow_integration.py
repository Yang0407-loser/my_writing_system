import hashlib
from unittest.mock import MagicMock

from app.writing.contracts import SceneSpec, SourceEvidence, StateAssertion
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


def scene_spec(subsection):
    evidence = SourceEvidence(
        evidence_id=f"e{subsection}",
        source_id=f"outline:1.{subsection}",
        source_type="current_outline",
        text_hash=str(subsection) * 64,
        excerpt="短证据",
    )
    assertion = StateAssertion(
        assertion_id=f"a{subsection}",
        subject="writer",
        predicate="future_event_status",
        value="不得推进下一小节事件",
        status="unknown",
        evidence_ids=[evidence.evidence_id],
    )
    return SceneSpec(
        scene_id=f"scene-{subsection}",
        task_id="task",
        section=1,
        subsection=subsection,
        evidence=[evidence],
        unknowns_and_conflicts=[assertion],
        source_hash="c" * 64,
        spec_hash=str(subsection) * 64,
        estimated_tokens=10,
    )


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


def test_each_committed_subsection_uses_only_its_explicit_scene_spec_after_commit():
    context = ContextManager()
    committer = StateCommitter()
    sink = InMemoryShadowValidationSink()
    provider_calls = []
    runner = ShadowBoundaryValidationRunner(
        enabled=True,
        sink=sink,
        scene_spec_provider=lambda *args: provider_calls.append(args),
    )

    for subsection, text in ((1, "第一小节正文。"), (2, "第二小节正文。")):
        artifact = committer.commit_subsection(
            idempotency_key=f"task:1:{subsection}",
            source_hash=f"messages-{subsection}",
            draft=text,
            validation_complete=True,
            vector_store=VectorStore(),
            context_manager=context,
            blackboard=MagicMock(),
            task_id="task",
            section=1,
            subsection=subsection,
            title=f"标题{subsection}",
            topic="主题",
        )
        assert context.drafts[-1] == (text, 1)
        runner.observe_committed(
            task_id="task",
            section=1,
            subsection=subsection,
            text=text,
            output_hash=artifact.output_hash,
            source_manifest=[],
            scene_spec=scene_spec(subsection),
        )

    assert provider_calls == []
    assert [record["subsection"] for record in sink.records] == [1, 2]
    assert [record["scene_spec_hash"] for record in sink.records] == ["1" * 64, "2" * 64]
    assert all(record["scene_spec_delivery"] == "explicit_artifact" for record in sink.records)
    assert all(record["production_effect"] is False for record in sink.records)
