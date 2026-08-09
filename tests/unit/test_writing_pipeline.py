import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.writer import Writer
from app.writing import (
    GenerationController,
    MandatoryEventPolicy,
    PromptBuilder,
    StateCommitter,
    SubsectionInput,
    SubsectionPipeline,
)
from app.writing.prompt_builder import messages_hash


def prepared_input(**overrides):
    values = {
        "task_id": "task-1",
        "section": 2,
        "subsection": 1,
        "outline_target": "第二节测试",
        "target_words": 1000,
        "generation_settings": {"temperature": 0.5, "top_p": 0.9},
        "prepared_context_fields": {
            "mandatory_events": "（本节无硬性事件约束）",
            "character_constraints": "",
            "style_constraints": "",
            "narrative_integrity_constraints": "",
            "beat_reminder": "",
            "progress_context": "",
            "rules_context": "",
            "topic": "测试",
            "world_setting": "",
            "section": 2,
            "subsection": 1,
            "subsection_title": "测试小节",
            "section_outline": "第二节测试",
            "key_points": "完成测试",
            "sub_description": "完成测试",
            "narrative_density_instruction": "中等密度",
            "style_examples": "",
            "emotion_intensity": 50,
            "sentence_preference": "balanced",
            "sensory_density": "medium",
            "dialogue_ratio": 20,
            "ranked_events": "（无特殊事件）",
            "world_facts": "（无）",
            "world_contradictions": "（无）",
            "character_context": "（无）",
            "arc_context": "（无）",
            "handover_context": "（无）",
            "summary_context": "（故事开头）",
            "retrieved_context": "（无相关段落）",
            "target_words": 1000,
            "style_structured": "",
        },
        "source_manifest": [{"source_id": "test:fixture", "text_hash": "abc"}],
    }
    values.update(overrides)
    return SubsectionInput(**values)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.responses.pop(0)

    def chat_completion_stream(self, messages, **kwargs):
        raise RuntimeError("stream unavailable")


def controller(llm, mandatory_event_policy=None):
    return GenerationController(
        llm,
        character_violation_checker=lambda _text, _characters: [],
        fallback_splitter=lambda text: [text],
        mandatory_event_policy=mandatory_event_policy,
    )


def test_prompt_builder_is_deterministic_and_traceable():
    prepared = prepared_input()
    first = PromptBuilder().build(prepared, token_by_source={"outline": 12})
    second = PromptBuilder().build(prepared, token_by_source={"outline": 12})

    assert first == second
    assert first.messages_hash == messages_hash(first.messages)
    assert first.estimated_tokens > 0
    assert first.token_by_source == {"outline": 12}
    assert first.source_manifest == prepared.source_manifest


def test_prompt_builder_uses_section_one_template_without_handover_heading():
    prepared = prepared_input(section=1, subsection=1)
    artifact = PromptBuilder().build(prepared)
    assert "## 前面章节的交接笔记" not in artifact.messages[1]["content"]


def test_generation_controller_preserves_initial_parameters():
    llm = FakeLLM(["正文完成。"])
    artifact = controller(llm).generate(
        messages=[{"role": "user", "content": "写作"}],
        call_max_tokens=900,
        stream_callback=None,
        section_num=2,
        sub_num=1,
        mandatory_events_text="（本节无硬性事件约束）",
    )
    assert artifact.draft == "正文完成。"
    assert llm.calls[0][1] == {"temperature": 0.5, "max_tokens": 900, "top_p": 0.9}
    assert artifact.generation_attempts[0]["reason"] == "initial"


def test_generation_controller_retries_missing_mandatory_event():
    llm = FakeLLM(["没有目标事件。", "林晚删帖。"])
    task_id = "11111111-1111-4111-8111-111111111111"
    policy = MandatoryEventPolicy(mode="retry", retry_task_ids=task_id)
    artifact = controller(llm, policy).generate(
        messages=[{"role": "user", "content": "写作"}],
        call_max_tokens=900,
        stream_callback=None,
        section_num=2,
        sub_num=1,
        mandatory_events_text="1. 【必须】林晚删帖",
        task_id=task_id,
    )
    assert artifact.draft == "林晚删帖。"
    assert [item[1]["temperature"] for item in llm.calls] == [0.5, 0.3]
    assert artifact.generation_attempts[-1]["reason"] == "mandatory_events"


def test_generation_controller_stream_fallback_preserves_callbacks():
    llm = FakeLLM(["回退正文。"])
    events = []
    artifact = controller(llm).generate(
        messages=[{"role": "user", "content": "写作"}],
        call_max_tokens=900,
        stream_callback=lambda *args: events.append(args),
        section_num=2,
        sub_num=1,
        mandatory_events_text="（本节无硬性事件约束）",
    )
    assert artifact.draft == "回退正文。"
    assert events[0][-1] == "section_start"
    assert events[-1] == ("回退正文。", 2, 1, "token")


def test_generation_controller_condenses_with_existing_parameters(monkeypatch):
    monkeypatch.setattr("app.writing.generation_controller.settings.WRITER_EXPAND_THRESHOLD", 0.0)
    monkeypatch.setattr("app.writing.generation_controller.settings.WRITER_CONDENSE_MODE", "legacy")
    llm = FakeLLM(["精简完成。"])
    artifact = controller(llm).adjust_length(
        "很长的正文。" * 20,
        target_words=10,
        call_max_tokens=800,
        stream_callback=None,
        section_num=2,
        sub_num=1,
    )
    assert artifact.draft == "精简完成。"
    assert llm.calls[-1][1] == {"temperature": 0.3, "max_tokens": 800}


class FakeVectorStore:
    def __init__(self):
        self.calls = []

    def add_text(self, **kwargs):
        self.calls.append(("add_text", kwargs))

    def enforce_task_limit(self, task_id):
        self.calls.append(("enforce_task_limit", task_id))


class FakeContextManager:
    def __init__(self):
        self.calls = []

    def add_subsection(self, text, section):
        self.calls.append((text, section))


def commit_once(committer, *, validation_complete=True):
    vector = FakeVectorStore()
    context = FakeContextManager()
    blackboard = MagicMock()
    stream = MagicMock()
    artifact = committer.commit_subsection(
        idempotency_key="task-1:2:1",
        source_hash="source-hash",
        draft="正文完成。",
        validation_complete=validation_complete,
        vector_store=vector,
        context_manager=context,
        blackboard=blackboard,
        task_id="task-1",
        section=2,
        subsection=1,
        title="测试",
        topic="主题",
        stream_callback=stream,
        token_usage_provider=lambda: {"input": 1},
    )
    return artifact, vector, context, blackboard, stream


def test_state_committer_preserves_order_and_is_idempotent():
    committer = StateCommitter()
    first, vector, context, blackboard, stream = commit_once(committer)
    second, vector2, context2, blackboard2, stream2 = commit_once(committer)

    assert first.committed_fields == [
        "vector_store.chunks",
        "vector_store.task_limit",
        "context_manager.subsection",
        "blackboard.token_usage",
        "stream.section_end",
    ]
    assert vector.calls[-1] == ("enforce_task_limit", "task-1")
    assert context.calls == [("正文完成。", 2)]
    blackboard.set.assert_called_once_with("task-1", "token_usage", {"input": 1})
    stream.assert_called_once_with("正文完成。", 2, 1, "section_end")
    assert second.skipped_as_duplicate is True
    assert vector2.calls == []
    assert context2.calls == []
    blackboard2.set.assert_not_called()
    stream2.assert_not_called()


def test_state_committer_rejects_commit_before_validation():
    committer = StateCommitter()
    with pytest.raises(ValueError, match="before validation"):
        commit_once(committer, validation_complete=False)


def test_state_committer_exposes_partial_failure_without_fake_rollback():
    committer = StateCommitter()
    vector = FakeVectorStore()
    vector.add_text = MagicMock(side_effect=RuntimeError("write failed"))
    with pytest.raises(RuntimeError, match="write failed"):
        committer.commit_subsection(
            idempotency_key="task-1:2:1",
            source_hash="source-hash",
            draft="正文完成。",
            validation_complete=True,
            vector_store=vector,
            context_manager=FakeContextManager(),
            blackboard=MagicMock(),
            task_id="task-1",
            section=2,
            subsection=1,
            title="测试",
            topic="主题",
        )
    assert committer.last_artifact.rollback_information["automatic_rollback"] is False


def test_state_committer_centralizes_handover_world_event_and_checkpoint_effects():
    committer = StateCommitter()
    event_graph = MagicMock()
    event_graph.update_arc_status.return_value = 1
    world_state = MagicMock()
    world_state.add_fact.return_value = "fact-1"
    note = {
        "foreshadowing": "伏笔",
        "character_state": "人物状态",
        "open_threads": "待承接",
        "found_contradictions": [{"target_section": 1}],
        "new_facts": ["新事实"],
        "arc_progress": {"character-1": "done"},
    }
    effects = committer.commit_handover_effects(
        idempotency_key="effects",
        handover_note=note,
        event_graph=event_graph,
        world_state=world_state,
        world_state_enabled=True,
        task_id="task-1",
        section=2,
        subsection=1,
        logger=MagicMock(),
    )
    parts = []
    backrefs = []
    committer.commit_local_handover(
        idempotency_key="local",
        handover_note=note,
        section_handover_parts=parts,
        backref=note["found_contradictions"],
        backref_suggestions=backrefs,
    )
    chain = []
    section_note, section_artifact = committer.commit_section_handover(
        idempotency_key="section",
        section=2,
        section_handover_parts=parts,
        handover_notes=chain,
    )
    blackboard = MagicMock()
    checkpoint = committer.save_checkpoint(
        blackboard, "task-1", {"current_section": 2, "phase": "writing"}
    )

    assert effects.committed_fields == [
        "event_graph.arc_progress:character-1", "world_state.fact:fact-1",
    ]
    event_graph.update_arc_status.assert_called_once_with("character-1", "done")
    world_state.add_fact.assert_called_once()
    assert parts == [note]
    assert backrefs == [{"target_section": 1}]
    assert chain == [section_note]
    assert section_artifact.committed_fields == ["handover.chain"]
    assert checkpoint.committed_fields == ["blackboard.checkpoint"]
    blackboard.save_checkpoint.assert_called_once()


def test_subsection_pipeline_rejects_out_of_order_transitions():
    pipeline = SubsectionPipeline(prepared_input(), trace_id="trace-1")
    with pytest.raises(RuntimeError, match="invalid subsection phase transition"):
        pipeline.record_validation({"complete": True})


def test_writer_public_signatures_remain_frozen():
    assert str(inspect.signature(Writer.run)) == (
        "(self, topic: str, style: dict, outline: list[dict], vector_store, blackboard, task_id: str, "
        "characters: list[dict] | None = None, character_arcs: list[dict] | None = None, "
        "stream_callback: Callable | None = None, interactive: bool = False, "
        "on_section_done: Callable | None = None, world_setting: str = '', prev_draft: str = '', "
        "prev_handover_list: list[dict] | None = None, existing_draft: dict[str, str] | None = None, "
        "existing_section_texts: dict[int, str] | None = None, "
        "world_state: app.world_state.WorldStateManager | None = None, "
        "event_graph: app.narrative_event.EventGraph | None = None, resume_context: dict | None = None, "
        "constraints: list[dict] | None = None, rules_context: str = '', subplot_context: str = '', "
        "relation_context: str = '', improvement_context: str = '', experience_context: str = '', "
        "narrative_beats: list[dict] | None = None, reference_text: str = '') -> dict"
    )
    assert str(inspect.signature(Writer.revise_subsection)) == (
        "(self, original_text: str, instruction: str) -> str"
    )


def test_boundary_modules_do_not_import_runtime_story_stores():
    root = Path(__file__).resolve().parents[2] / "app" / "writing"
    forbidden = {
        "blackboard", "vector_store", "context_manager", "world_state", "narrative_event",
        "rule_store", "foreshadowing_store", "character_relation_store",
    }
    for filename in ("prompt_builder.py", "generation_controller.py"):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        imports = {
            alias.name.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert imports.isdisjoint(forbidden), f"{filename} imports a runtime story store"


def test_writer_run_remains_a_compatible_facade(monkeypatch):
    monkeypatch.setattr("app.agents.writer.settings.ENABLE_RAG", False)
    monkeypatch.setattr("app.agents.writer.settings.ENABLE_WORLD_STATE", False)
    monkeypatch.setattr("app.agents.writer.settings.ENABLE_STYLE_BEHAVIOR", False)
    monkeypatch.setattr("app.agents.writer.settings.WRITER_EXPAND_THRESHOLD", 0.0)
    monkeypatch.setattr("app.agents.writer.settings.WRITER_REVIEW_TRIGGER_SUBS", 999)
    monkeypatch.setattr("app.agents.writer.settings.WRITER_REVIEW_TRIGGER_CHARS", 999999)
    monkeypatch.setattr("app.agents.writer.foreshadowing_store.build_foreshadowing_context", lambda *_: "")
    monkeypatch.setattr("app.faction_store.build_faction_context", lambda *_: "")
    monkeypatch.setattr("app.map_manager.build_location_context", lambda *_: "")
    monkeypatch.setattr("app.item_manager.build_item_context", lambda *_: "")
    original_arcs = [{"character_id": "c1", "current_state": "old"}]
    monkeypatch.setattr(
        "app.agents.writer.CharacterManager.update_states",
        lambda _self, _characters, arcs, _text, _section: [
            {**arcs[0], "current_state": "updated"}
        ],
    )

    class Blackboard:
        def __init__(self):
            self.values = {}
            self.checkpoints = []

        def get(self, _task_id, field):
            return self.values.get(field)

        def set(self, _task_id, field, value):
            self.values[field] = value

        def xadd_event(self, *_args, **_kwargs):
            return None

        def save_checkpoint(self, task_id, state):
            self.checkpoints.append((task_id, state))

    writer = Writer()
    writer.llm = FakeLLM([
        "完成测试，正文完成。",
        '{"foreshadowing":"","character_state":"","open_threads":"",'
        '"found_contradictions":[],"new_facts":[],"arc_progress":{}}',
    ])
    vector = FakeVectorStore()
    blackboard = Blackboard()
    result = writer.run(
        topic="测试主题",
        style={},
        outline=[{
            "section": 1,
            "title": "第一节",
            "key_points": [],
            "subsections": [{
                "subsection": 1,
                "title": "开始",
                "description": "完成测试",
                "key_points": ["完成测试"],
                "target_words": 100,
                "status": "queued",
            }],
        }],
        vector_store=vector,
        blackboard=blackboard,
        task_id="task-1",
        characters=[{"id": "c1", "name": "Character"}],
        character_arcs=original_arcs,
        rules_context="测试规则",
    )

    assert "完成测试，正文完成。" in result["draft"]
    assert result["context_state"]["buffer"] == ["完成测试，正文完成。"]
    assert vector.calls[-1] == ("enforce_task_limit", "task-1")
    assert blackboard.checkpoints[-1][1]["current_section"] == 1
    assert result["character_arcs"][0]["current_state"] == "updated"
    assert blackboard.checkpoints[-1][1]["character_arcs"] == result["character_arcs"]
    assert original_arcs[0]["current_state"] == "old"
    assert len(result["narrative_reality_warnings"]) == 1
    assert result["narrative_reality_warnings"][0]["production_effect"] is False
    assert (
        blackboard.values["narrative_reality_warnings_v0"]
        == result["narrative_reality_warnings"]
    )
