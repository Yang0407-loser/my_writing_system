import hashlib

from app.writing.contracts import PromptArtifact
from app.writing.generation_controller import GenerationController
from app.writing.prompt_builder import messages_hash
from app.writing.scene_spec_provider import SceneSpecCanaryController
from app.writing.state_committer import StateCommitter


class FixedLLM:
    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return "固定正文。"


class VectorStore:
    def add_text(self, **kwargs):
        return None

    def enforce_task_limit(self, task_id):
        return 0


class ContextManager:
    def __init__(self):
        self.items = []

    def add_subsection(self, text, section):
        self.items.append((text, section))


def artifact():
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "legacy"}]
    return PromptArtifact(
        messages=messages, messages_hash=messages_hash(messages),
        content_hash=hashlib.sha256("system\nlegacy".encode()).hexdigest(),
        estimated_tokens=3, prompt_version="v1",
    )


def generate(messages):
    llm = FixedLLM()
    controller = GenerationController(
        llm, character_violation_checker=lambda *_: [], fallback_splitter=lambda text: [text]
    )
    result = controller.generate(
        messages=messages, call_max_tokens=2048, stream_callback=None,
        section_num=2, sub_num=1, mandatory_events_text="（本节无硬性事件约束）",
        characters=None, previous_texts=None, prev_sub_text="", target_goal="",
    )
    return result, llm.calls


def test_canary_changes_only_input_messages_not_generation_or_commit_contract():
    legacy = artifact()
    controller = SceneSpecCanaryController(mode="canary", canary_task_ids="task-canary")
    applied = controller.apply(
        legacy, task_id="task-canary", section=2,
        current_subsection={"subsection": 1, "title": "行动", "key_points": ["回应"]},
        next_subsection={"subsection": 2, "title": "离开", "key_points": ["离开"]},
        is_last_subsection=False,
    )
    legacy_generation, legacy_calls = generate(legacy.messages)
    canary_generation, canary_calls = generate(applied.prompt.messages)

    assert legacy_generation.draft == canary_generation.draft == "固定正文。"
    assert legacy_calls[0][1] == canary_calls[0][1] == {
        "temperature": 0.5, "max_tokens": 2048, "top_p": 0.9,
    }
    assert legacy_calls[0][0][0] == canary_calls[0][0][0]

    context = ContextManager()
    commit = StateCommitter().commit_subsection(
        idempotency_key="task-canary:2:1", source_hash=applied.prompt.messages_hash,
        draft=canary_generation.draft, validation_complete=True,
        vector_store=VectorStore(), context_manager=context, blackboard=None,
        task_id="task-canary", section=2, subsection=1, title="行动", topic="主题",
    )
    assert commit.idempotency_key == "task-canary:2:1"
    assert commit.checkpoint_version == StateCommitter.CHECKPOINT_VERSION
    assert commit.output_hash == canary_generation.output_hash
    assert context.items == [("固定正文。", 2)]
