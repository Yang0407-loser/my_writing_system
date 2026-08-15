"""A deterministic Celery-shaped writer used by HTTP and browser E2E tests."""

from dataclasses import dataclass
from threading import Event, Lock, Thread, current_thread
from types import SimpleNamespace
from uuid import uuid4

from app.blackboard import Blackboard
from app.models import TaskState, TaskStatus, WriteRequest, WriteResponse
from app.task_store import TaskStore


TOKENS = ("雨落在旧站台。", "她终于等到回信。")
DRAFT = "".join(TOKENS)


@dataclass
class _Run:
    task_id: str
    kwargs: dict
    resume_state: dict | None = None
    step: int = 0
    result: dict | None = None
    error: Exception | None = None
    waiting_for_approval: bool = False


class _AsyncResult:
    def __init__(self, run: _Run | None):
        self._run = run

    def ready(self):
        return bool(self._run and (self._run.result is not None or self._run.error))

    def successful(self):
        return bool(self._run and self._run.result is not None)

    @property
    def result(self):
        return self._run.result if self._run else None

    @property
    def info(self):
        return self._run.error if self._run else None


class DeterministicWriter:
    """Explicit, observable writer transitions with a small Celery adapter."""

    def __init__(self, board: Blackboard, auto_run: bool = False, step_delay_ms: int = 250):
        self.board = board
        self.step_delay_ms = step_delay_ms
        self._runs: dict[str, _Run] = {}
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        if auto_run:
            self.start()

    def _register(self, task_id: str, kwargs: dict) -> None:
        resume_state = None
        if kwargs.get("resume") and kwargs.get("resume_from_task_id"):
            checkpoint = self.board.load_checkpoint(kwargs["resume_from_task_id"])
            resume_state = TaskState.model_validate(checkpoint).model_dump(mode="json")
        with self._lock:
            self._runs[task_id] = _Run(
                task_id=task_id,
                kwargs=kwargs,
                resume_state=resume_state,
            )

    @staticmethod
    def _validated_kwargs(kwargs: dict) -> dict:
        request = WriteRequest.model_validate(kwargs)
        return {**kwargs, **request.model_dump(mode="json")}

    @staticmethod
    def _submission_result(task_id: str, kwargs: dict) -> SimpleNamespace:
        response = WriteResponse(
            task_id=task_id,
            status="pending",
            workspace_task_id=str(kwargs.get("workspace_task_id") or task_id),
        ).model_dump(mode="json")
        return SimpleNamespace(id=task_id, **response)

    def apply_async(self, kwargs: dict, task_id: str):
        kwargs = self._validated_kwargs(dict(kwargs))
        self._register(task_id, kwargs)
        return self._submission_result(task_id, kwargs)

    def delay(self, **kwargs):
        task_id = str(uuid4())
        kwargs = self._validated_kwargs(dict(kwargs))
        self._register(task_id, kwargs)
        return self._submission_result(task_id, kwargs)

    def AsyncResult(self, task_id: str):
        with self._lock:
            return _AsyncResult(self._runs.get(task_id))

    @staticmethod
    def _workspace_task_id(run: _Run) -> str:
        return str(
            run.kwargs.get("workspace_task_id")
            or run.kwargs.get("resume_from_task_id")
            or run.task_id
        )

    def _set_runtime_status(self, run: _Run, status: str, **extra: object) -> dict:
        workspace_task_id = self._workspace_task_id(run)
        payload = TaskStatus(
            task_id=run.task_id,
            status=status,
            workspace_task_id=workspace_task_id,
            active_task_id=run.task_id,
            **extra,
        ).model_dump(mode="json", exclude_none=True)
        for key, value in payload.items():
            if key != "task_id":
                self.board.set(run.task_id, key, value)
        if workspace_task_id != run.task_id:
            self.board.set(workspace_task_id, "workspace_task_id", workspace_task_id)
            self.board.set(workspace_task_id, "active_task_id", run.task_id)
            self.board.set(workspace_task_id, "status", status)
            for key in ("draft", "review"):
                if key in payload:
                    self.board.set(workspace_task_id, key, payload[key])
        return payload

    def _await_outline_approval(self, run: _Run) -> str:
        checkpoint = TaskState(
            task_id=run.task_id,
            phase="outline",
            status="awaiting_outline_approval",
            config_topic=str(run.kwargs.get("topic", "")),
            config_reference_text=str(run.kwargs.get("reference_text", "")),
            config_target_words=int(run.kwargs.get("target_words_per_section", 10000)),
            config_character_text=str(run.kwargs.get("character_text", "")),
            config_interactive=True,
            characters=list(run.kwargs.get("characters", [])),
        ).model_dump(mode="json")
        self.board.save_checkpoint(run.task_id, checkpoint)
        self._set_runtime_status(run, "awaiting_outline_approval")
        self.board.xadd_event(
            run.task_id,
            {"event": "awaiting_decision", "phase": "outline"},
        )
        run.waiting_for_approval = True
        return "awaiting_outline_approval"

    def _complete(self, run: _Run) -> str:
        payload = self._set_runtime_status(
            run,
            "completed",
            draft=DRAFT,
            review={"global_score": 8},
            topic=str(run.kwargs.get("topic", "")),
            reference_text=str(run.kwargs.get("reference_text", "")),
        )
        workspace_task_id = self._workspace_task_id(run)
        with TaskStore() as store:
            workspace = store.get_workspace(workspace_task_id) or {}
            store.save_workspace(
                workspace_task_id,
                {
                    **workspace,
                    "active_task_id": run.task_id,
                    "topic": run.kwargs.get("topic", ""),
                    "reference_text": run.kwargs.get("reference_text", ""),
                    "draft_backup": DRAFT,
                    "status": "completed",
                },
            )
        run.result = payload
        run.step = 6
        return "completed"

    def _initialize_replacement(self, run: _Run) -> None:
        if run.resume_state is None:
            return
        self.board.save_checkpoint(
            run.task_id,
            {**run.resume_state, "task_id": run.task_id},
        )
        original_task_id = run.kwargs.get("resume_from_task_id")
        workspace_task_id = self._workspace_task_id(run)
        if original_task_id:
            self.board.set(original_task_id, "active_task_id", run.task_id)
        self.board.set(workspace_task_id, "workspace_task_id", workspace_task_id)
        self.board.set(workspace_task_id, "active_task_id", run.task_id)

    def advance(self, task_id: str) -> str:
        with self._lock:
            run = self._runs[task_id]
            if run.error:
                raise run.error
            if run.result is not None:
                return "completed"
            if run.waiting_for_approval:
                return "awaiting_outline_approval"
            if run.step == 0 and run.kwargs.get("interactive") and not run.kwargs.get("resume"):
                return self._await_outline_approval(run)
            if run.step == 0:
                self._initialize_replacement(run)
                self._set_runtime_status(run, "running")
                self.board.xadd_event(run.task_id, {"event": "section_start", "section": 1, "subsection": 1})
                run.step = 1
                return "section_start"
            if run.step == 1:
                self.board.xadd_event(
                    run.task_id,
                    {"event": "token", "section": 1, "subsection": 1, "token": TOKENS[0]},
                )
                run.step = 2
                return "token_1"
            if run.step == 2:
                self.board.xadd_event(
                    run.task_id,
                    {"event": "token", "section": 1, "subsection": 1, "token": TOKENS[1]},
                )
                run.step = 3
                return "token_2"
            if run.step == 3:
                self.board.xadd_event(
                    run.task_id,
                    {"event": "section_end", "section": 1, "subsection": 1, "text": DRAFT},
                )
                run.step = 4
                return "section_end"
            if run.step == 4:
                self.board.xadd_event(
                    run.task_id,
                    {"event": "done", "draft": DRAFT, "review": {"global_score": 8}},
                )
                run.step = 5
                return "done"
            return self._complete(run)

    def complete(self, task_id: str) -> None:
        for _ in range(8):
            with self._lock:
                run = self._runs[task_id]
                if run.error:
                    raise run.error
            label = self.advance(task_id)
            if label == "completed":
                return
        with self._lock:
            run = self._runs[task_id]
            if run.error:
                raise run.error
        raise RuntimeError(f"deterministic writer did not complete within eight transitions: {task_id}")

    def _run_automatically(self) -> None:
        while not self._stop_event.wait(self.step_delay_ms / 1000):
            with self._lock:
                task_ids = [
                    task_id
                    for task_id, run in self._runs.items()
                    if run.result is None and run.error is None and not run.waiting_for_approval
                ]
            for task_id in task_ids:
                try:
                    self.advance(task_id)
                except Exception as error:
                    with self._lock:
                        run = self._runs.get(task_id)
                        if run is not None:
                            run.error = error

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(target=self._run_automatically, daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=2)
