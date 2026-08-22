import hashlib

from app.writing.contracts import PromptArtifact
from app.writing.generation_controller import GenerationController
from app.writing.prompt_builder import messages_hash
from app.writing.state_committer import StateCommitter
from app.writing.writer_execution_contract import WriterExecutionContractController


class FixedLLM:
    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return "fixed prose."


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


def prompt_artifact():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "legacy"},
    ]
    return PromptArtifact(
        messages=messages,
        messages_hash=messages_hash(messages),
        content_hash=hashlib.sha256("system\nlegacy".encode()).hexdigest(),
        estimated_tokens=3,
        prompt_version="v1",
    )


def generate(messages):
    llm = FixedLLM()
    controller = GenerationController(
        llm,
        character_violation_checker=lambda *_: [],
        fallback_splitter=lambda text: [text],
    )
    result = controller.generate(
        messages=messages,
        call_max_tokens=2048,
        stream_callback=None,
        section_num=2,
        sub_num=1,
        mandatory_events_text="（本节无硬性事件约束）",
        characters=None,
        previous_texts=None,
        prev_sub_text="",
        target_goal="",
    )
    return result, llm.calls


def test_canary_changes_only_input_and_not_generation_or_commit_contract():
    legacy = prompt_artifact()
    event = {
        "source_id": "outline:S2.1:key_point:1",
        "text": "complete event",
        "text_hash": hashlib.sha256(b"complete event").hexdigest(),
    }
    applied = WriterExecutionContractController(mode="canary").apply(
        legacy,
        task_id="task-canary",
        section=2,
        current_subsection={
            "subsection": 1,
            "title": "Action",
            "description": "Complete action",
            "key_points": ["complete event"],
        },
        next_subsection={
            "subsection": 2,
            "title": "Leave",
            "description": "Leave",
            "key_points": ["leave"],
        },
        is_last_subsection=False,
        required_events=[event],
        target_characters=1000,
    )
    legacy_generation, legacy_calls = generate(legacy.messages)
    canary_generation, canary_calls = generate(applied.prompt.messages)

    assert legacy_generation.draft == canary_generation.draft == "fixed prose."
    assert len(legacy_calls) == len(canary_calls) == 1
    assert legacy_calls[0][1] == canary_calls[0][1] == {
        "temperature": 0.5,
        "max_tokens": 2048,
        "top_p": 0.9,
    }
    assert legacy_calls[0][0][0] == canary_calls[0][0][0]

    context = ContextManager()
    commit = StateCommitter().commit_subsection(
        idempotency_key="task-canary:2:1",
        source_hash=applied.prompt.messages_hash,
        draft=canary_generation.draft,
        validation_complete=True,
        vector_store=VectorStore(),
        context_manager=context,
        blackboard=None,
        task_id="task-canary",
        section=2,
        subsection=1,
        title="Action",
        topic="Topic",
    )
    assert commit.idempotency_key == "task-canary:2:1"
    assert commit.checkpoint_version == StateCommitter.CHECKPOINT_VERSION
    assert commit.output_hash == canary_generation.output_hash
    assert context.items == [("fixed prose.", 2)]
