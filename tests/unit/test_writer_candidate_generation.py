from __future__ import annotations

from unittest.mock import MagicMock

from app.canonical.contracts import CanonicalStateSnapshot
from app.canonical.hashing import sha256_text
from app.writing import GenerationController, PromptBuilder
from app.writing.subsection_generator import SubsectionGenerator
from tests.unit.test_writing_pipeline import FakeLLM, prepared_input


def _snapshot(version_id: str = "state-input") -> CanonicalStateSnapshot:
    return CanonicalStateSnapshot.create(
        version_id=version_id,
        project_id="project-1",
        schema_version="canonical-state-v0",
        state_json={"foundation_state_v0": {}},
    )


def _controller(llm):
    return GenerationController(
        llm,
        character_violation_checker=lambda _text, _characters: [],
        fallback_splitter=lambda text: [text],
    )


def _generate(monkeypatch, *, snapshot=None, handover=None, validation=None):
    monkeypatch.setattr(
        "app.writing.generation_controller.settings.WRITER_EXPAND_THRESHOLD", 0.0
    )
    llm = FakeLLM(["正文完成。"])
    observation = {"executed": True, "execution_status": "success"}
    extractor = MagicMock(return_value=(handover or {"new_facts": []}, observation))
    validator = MagicMock(return_value=validation or {"complete": True, "warnings": []})
    generator = SubsectionGenerator(
        generation_controller=_controller(llm),
        handover_extractor=extractor,
        post_validator=validator,
    )
    prepared = prepared_input(target_words=4)
    candidate = generator.generate_subsection_candidate(
        prepared=prepared,
        canonical_state_snapshot=snapshot or _snapshot(),
        tenant_id="tenant-1",
        project_id="project-1",
        document_id="document-1",
        subsection_id="subsection-1",
        ordinal=1,
        title="测试小节",
        topic="测试",
        base_revision_number=0,
        mandatory_events_text="（本节无硬性事件约束）",
        current_subsection={"subsection": 1, "title": "测试小节"},
        next_subsection={"subsection": 2, "title": "下一小节"},
    )
    return candidate, prepared, llm, extractor, validator


def test_candidate_seam_preserves_prompt_draft_validation_and_handover(monkeypatch):
    handover = {
        "new_facts": [
            {"subject": "门", "predicate": "state", "value": "open"}
        ]
    }
    candidate, prepared, llm, extractor, validator = _generate(
        monkeypatch,
        handover=handover,
        validation={"complete": True, "warnings": ["soft warning"]},
    )
    expected_prompt = PromptBuilder().build(prepared)

    assert llm.calls[0][0] == expected_prompt.messages
    assert candidate.prompt_hash == expected_prompt.messages_hash
    assert candidate.draft == "正文完成。"
    assert candidate.draft_hash == sha256_text("正文完成。")
    assert candidate.validation.warnings == ("soft warning",)
    assert candidate.handover_candidate == handover
    assert candidate.generation_metadata["handover_observation"] == {
        "executed": True,
        "execution_status": "success",
    }
    assert len(candidate.world_mutations) == 1
    extractor.assert_called_once()
    validator.assert_called_once_with("正文完成。")


def test_candidate_uses_injected_state_version_without_latest_head_lookup(monkeypatch):
    snapshot = _snapshot("state-that-was-actually-loaded")
    candidate, *_ = _generate(monkeypatch, snapshot=snapshot)

    assert candidate.base_state_version_id == "state-that-was-actually-loaded"


def test_candidate_seam_has_no_runtime_store_mutations(monkeypatch):
    mutation_spies = {
        name: MagicMock(side_effect=AssertionError(f"unexpected {name}"))
        for name in (
            "world_state.add_fact",
            "event_graph.add_event",
            "vector_store.add_text",
            "context_manager.add_subsection",
            "blackboard.set",
            "blackboard.xadd_event",
        )
    }

    candidate, *_ = _generate(monkeypatch)

    assert candidate.draft
    assert all(spy.call_count == 0 for spy in mutation_spies.values())


def test_writer_facade_exposes_candidate_seam_without_changing_run_contract(
    monkeypatch,
):
    from app.agents.writer import Writer

    expected = object()
    delegated = MagicMock(return_value=expected)
    monkeypatch.setattr(
        "app.agents.writer.SubsectionGenerator.generate_subsection_candidate",
        delegated,
    )
    writer = object.__new__(Writer)
    writer.llm = MagicMock()

    result = writer.generate_subsection_candidate(marker="request")

    assert result is expected
    assert delegated.call_args.kwargs == {"marker": "request"}
