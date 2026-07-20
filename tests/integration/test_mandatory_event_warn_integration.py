from app.writing.generation_controller import GenerationController
from app.writing.mandatory_event_policy import MandatoryEventPolicy
from app.writing.state_committer import StateCommitter


TASK_ID = "11111111-1111-4111-8111-111111111111"


class FixedLLM:
    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return "不含规定事件的原始正文。"


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


def test_warn_only_generation_commits_original_output_once():
    llm = FixedLLM()
    controller = GenerationController(
        llm,
        character_violation_checker=lambda *_: [],
        fallback_splitter=lambda text: [text],
        mandatory_event_policy=MandatoryEventPolicy("warn"),
    )
    generation = controller.generate(
        messages=[{"role": "user", "content": "写作"}],
        call_max_tokens=900,
        stream_callback=None,
        section_num=2,
        sub_num=1,
        mandatory_events_text="1. 【必须】林晚删帖",
        task_id=TASK_ID,
    )

    context = ContextManager()
    commit = StateCommitter().commit_subsection(
        idempotency_key=f"{TASK_ID}:2:1",
        source_hash="prompt-hash",
        draft=generation.draft,
        validation_complete=True,
        vector_store=VectorStore(),
        context_manager=context,
        blackboard=None,
        task_id=TASK_ID,
        section=2,
        subsection=1,
        title="测试",
        topic="测试",
    )

    assert len(llm.calls) == 1
    assert generation.generation_attempts[0]["reason"] == "initial"
    assert controller.last_mandatory_observation["would_have_retried"] is True
    assert controller.last_mandatory_observation["actual_retry_count"] == 0
    assert commit.output_hash == generation.output_hash
    assert commit.checkpoint_version == StateCommitter.CHECKPOINT_VERSION
    assert context.items == [(generation.draft, 2)]
