from __future__ import annotations

from fakeredis import FakeRedis

from app.blackboard import Blackboard
from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_session_factory
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_replay import CanonicalProjectionReplay
from app.projections.analytics import AnalyticsProjectionAdapter
from app.projections.markdown_export import MarkdownExportProjectionAdapter
from app.projections.redis_stream import RedisStreamProjectionAdapter
from app.projections.task_preview import TaskPreviewProjectionAdapter
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.unit.canonical.test_commit_service",)


def test_nonblocking_sinks_reopen_clear_and_replay_from_canon(
    canonical_session, tmp_path
):
    scope = ProjectionScope("tenant-1", "project-1")
    CanonicalCommitService(canonical_session, scope.tenant_id, scope.project_id).commit(
        _prepared(canonical_session), "nonblocking-reopen"
    )
    replay = CanonicalProjectionReplay(canonical_session)
    messages = {
        projector_id: next(replay.iter_messages(scope, projector_id, 0, 1))
        for projector_id in (
            "redis_stream",
            "task_preview",
            "markdown_export",
            "analytics",
        )
    }
    factory = build_session_factory(canonical_session.get_bind())
    redis = FakeRedis()

    def open_adapters():
        board = Blackboard.__new__(Blackboard)
        board._redis = redis
        return {
            "redis_stream": RedisStreamProjectionAdapter(board, scope, "task-1"),
            "task_preview": TaskPreviewProjectionAdapter(board, scope, "task-1"),
            "markdown_export": MarkdownExportProjectionAdapter(
                factory, scope, "task-1", root=str(tmp_path / "markdown")
            ),
            "analytics": AnalyticsProjectionAdapter(factory, scope, "task-1"),
        }

    first = open_adapters()
    for projector_id, adapter in first.items():
        adapter.apply(messages[projector_id])
    before = {
        projector_id: adapter.actual_records(scope)
        for projector_id, adapter in first.items()
    }

    reopened = open_adapters()
    assert {
        projector_id: adapter.actual_records(scope)
        for projector_id, adapter in reopened.items()
    } == before

    for adapter in reopened.values():
        adapter.clear(scope)
        assert adapter.actual_records(scope) == ()
    for projector_id, adapter in reopened.items():
        adapter.apply(messages[projector_id])
        assert adapter.actual_records(scope) == before[projector_id]
