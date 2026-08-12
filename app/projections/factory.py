"""Construct all production projection adapters from durable dependencies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..blackboard import Blackboard
from ..canonical.projection_ports import ProjectionScope
from ..canonical.projection_registry import DEFAULT_PROJECTOR_REGISTRY
from ..canonical.projection_replay import CanonicalProjectionReplay
from ..vector_store import VectorStore
from .analytics import AnalyticsProjectionAdapter
from .chroma_story import ChromaStoryProjectionAdapter
from .handover import HandoverProjectionAdapter
from .legacy_world import LegacyWorldProjectionAdapter
from .markdown_export import MarkdownExportProjectionAdapter
from .redis_stream import RedisStreamProjectionAdapter
from .task_preview import TaskPreviewProjectionAdapter


class _RoutingProjectionAdapter:
    def __init__(self, spec, adapter_factory, session_factory) -> None:
        self.spec = spec
        self._adapter_factory = adapter_factory
        self._session_factory = session_factory

    @staticmethod
    def _identity(message):
        revision = message.payload.get("revision")
        metadata = revision.get("metadata") if isinstance(revision, dict) else None
        task_id = str(metadata.get("task_id") or "") if isinstance(metadata, dict) else ""
        if not task_id:
            raise ValueError("projection message canonical task_id is required")
        return ProjectionScope(message.tenant_id, message.project_id), task_id

    def apply(self, message):
        scope, task_id = self._identity(message)
        return self._adapter_factory(scope, task_id).apply(message)

    def expected_records(self, messages: Iterable):
        messages = tuple(messages)
        if not messages:
            return ()
        scope, task_id = self._identity(messages[0])
        if any(self._identity(message) != (scope, task_id) for message in messages):
            raise ValueError("projection replay batch spans multiple scopes or tasks")
        return self._adapter_factory(scope, task_id).expected_records(messages)

    def _task_id_for_scope(self, scope: ProjectionScope) -> str:
        with self._session_factory() as session:
            messages = tuple(
                CanonicalProjectionReplay(session).iter_messages(
                    scope, self.spec.projector_id, 0, 2**63 - 1
                )
            )
        task_ids = {self._identity(message)[1] for message in messages}
        if len(task_ids) != 1:
            raise ValueError("projection scope must resolve to exactly one canonical task")
        return task_ids.pop()

    def actual_records(self, scope: ProjectionScope):
        task_id = self._task_id_for_scope(scope)
        return self._adapter_factory(scope, task_id).actual_records(scope)

    def clear(self, scope: ProjectionScope) -> None:
        self._adapter_factory(scope, self._task_id_for_scope(scope)).clear(scope)


def build_projection_adapters(
    session_factory,
    *,
    scope: ProjectionScope | None = None,
    task_id: str | None = None,
    blackboard=None,
    vector_store=None,
    markdown_root: str | None = None,
) -> Mapping[str, object]:
    if (scope is None) != (task_id is None):
        raise ValueError("scope and task_id must be provided together")
    board = blackboard or Blackboard()
    vectors = vector_store or VectorStore()
    factories = {
        "legacy_world_event": lambda item_scope, item_task: LegacyWorldProjectionAdapter(
            board, item_scope, item_task
        ),
        "handover_context": lambda item_scope, item_task: HandoverProjectionAdapter(
            board, item_scope, item_task
        ),
        "chroma_story_chunks": lambda item_scope, item_task: ChromaStoryProjectionAdapter(
            vectors, item_scope, item_task
        ),
        "redis_stream": lambda item_scope, item_task: RedisStreamProjectionAdapter(
            board, item_scope, item_task
        ),
        "task_preview": lambda item_scope, item_task: TaskPreviewProjectionAdapter(
            board, item_scope, item_task
        ),
        "markdown_export": lambda item_scope, item_task: MarkdownExportProjectionAdapter(
            session_factory, item_scope, item_task, root=markdown_root
        ),
        "analytics": lambda item_scope, item_task: AnalyticsProjectionAdapter(
            session_factory, item_scope, item_task
        ),
    }
    if scope is None:
        return {
            projector_id: _RoutingProjectionAdapter(
                DEFAULT_PROJECTOR_REGISTRY.get(projector_id), factory, session_factory
            )
            for projector_id, factory in factories.items()
        }
    return {projector_id: factory(scope, task_id) for projector_id, factory in factories.items()}
