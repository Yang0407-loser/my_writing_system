import hashlib
import inspect
from unittest.mock import MagicMock

from app.agents.writer import Writer
from app.writing.shadow_post_write_extraction import (
    InMemoryPostWriteExtractionSink,
    ShadowPostWriteExtractionRunner,
)
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


def test_shadow_extraction_error_after_commit_cannot_change_saved_text():
    text = "已经提交的正文。"
    committer = StateCommitter()
    context = ContextManager()
    artifact = committer.commit_subsection(
        idempotency_key="task:1:1", source_hash="messages-hash", draft=text,
        validation_complete=True, vector_store=VectorStore(), context_manager=context,
        blackboard=MagicMock(), task_id="task", section=1, subsection=1,
        title="标题", topic="主题",
    )

    class FailingExtractor:
        def extract(self, **kwargs):
            raise RuntimeError("boom")

    sink = InMemoryPostWriteExtractionSink()
    record = ShadowPostWriteExtractionRunner(
        enabled=True, extractor=FailingExtractor(), sink=sink,
    ).observe_committed(
        task_id="task", section=1, subsection=1, text=text,
        output_hash=artifact.output_hash, source_manifest=[],
    )

    assert record["status"] == "shadow_error"
    assert context.drafts == [(text, 1)]
    assert artifact.output_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert artifact.checkpoint_version == StateCommitter.CHECKPOINT_VERSION


def test_writer_hook_is_after_commit_and_does_not_replace_legacy_extractors():
    source = inspect.getsource(Writer.run)
    commit = source.index("commit_artifact = state_committer.commit_subsection(")
    shadow = source.index("shadow_post_write_extractor.observe_committed(")
    relation = source.index("extract_relations_from_text")
    experience = source.index("extract_from_section")
    assert commit < shadow < relation < experience
    assert "handover_note = self._extract_handover(" in source
    assert "cm_char.update_states(" in source


def test_default_off_builder_does_not_construct_shared_extractor(monkeypatch):
    monkeypatch.setattr("app.agents.writer.settings.WRITER_POST_WRITE_EXTRACTION_MODE", "off")
    writer = Writer.__new__(Writer)
    writer.llm = object()
    runner = writer._build_shadow_post_write_extraction_runner(
        blackboard=MagicMock(), task_id="task",
    )
    assert runner.enabled is False
    assert runner.extractor is None


def test_writer_freezes_known_context_before_legacy_handover_effects():
    source = inspect.getsource(Writer.run)
    context = source.index("post_write_extraction_context = self._build_post_write_extraction_context(")
    handover = source.index("handover_note = self._extract_handover(")
    effects = source.index("state_committer.commit_handover_effects(")
    shadow = source.index("shadow_post_write_extractor.observe_committed(")
    assert context < handover < effects < shadow
