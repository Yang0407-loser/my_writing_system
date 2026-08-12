from __future__ import annotations

from fakeredis import FakeRedis

from app.blackboard import Blackboard
from app.canonical.commit_service import CanonicalCommitService
from app.canonical.database import build_session_factory
from app.canonical.projection_ports import ProjectionScope
from app.canonical.projection_replay import CanonicalProjectionReplay
from app.projections.analytics import AnalyticsProjectionAdapter
from app.projections.factory import build_projection_adapters
from app.projections.markdown_export import MarkdownExportProjectionAdapter
from app.projections.redis_stream import RedisStreamProjectionAdapter
from app.projections.task_preview import TaskPreviewProjectionAdapter
from tests.unit.canonical.test_commit_service import _prepared


pytest_plugins = ("tests.unit.canonical.test_commit_service",)


SCOPE = ProjectionScope("tenant-1", "project-1")


def _board(redis=None):
    board = Blackboard.__new__(Blackboard)
    board._redis = redis or FakeRedis()
    return board


def _messages(session):
    CanonicalCommitService(session, SCOPE.tenant_id, SCOPE.project_id).commit(
        _prepared(session), "nonblocking-adapter"
    )
    replay = CanonicalProjectionReplay(session)
    return {
        projector_id: next(replay.iter_messages(SCOPE, projector_id, 0, 1))
        for projector_id in (
            "redis_stream",
            "task_preview",
            "markdown_export",
            "analytics",
        )
    }


def _assert_duplicate_clear_replay(adapter, message):
    first = adapter.apply(message)
    second = adapter.apply(message)
    assert first == second
    before = adapter.actual_records(SCOPE)
    assert before == adapter.expected_records((message,))
    adapter.clear(SCOPE)
    assert adapter.actual_records(SCOPE) == ()
    adapter.apply(message)
    assert adapter.actual_records(SCOPE) == before


def test_nonblocking_adapters_have_distinct_rebuildable_records(
    canonical_session, tmp_path
):
    messages = _messages(canonical_session)
    factory = build_session_factory(canonical_session.get_bind())
    board = _board()
    adapters = {
        "redis_stream": RedisStreamProjectionAdapter(board, SCOPE, "task-1"),
        "task_preview": TaskPreviewProjectionAdapter(board, SCOPE, "task-1"),
        "markdown_export": MarkdownExportProjectionAdapter(
            factory, SCOPE, "task-1", root=str(tmp_path / "markdown")
        ),
        "analytics": AnalyticsProjectionAdapter(factory, SCOPE, "task-1"),
    }

    for projector_id, adapter in adapters.items():
        _assert_duplicate_clear_replay(adapter, messages[projector_id])

    record_ids = {
        adapter.actual_records(SCOPE)[0].record_id for adapter in adapters.values()
    }
    assert len(record_ids) == 4


def test_task_preview_rejects_same_position_conflict(canonical_session):
    message = _messages(canonical_session)["task_preview"]
    adapter = TaskPreviewProjectionAdapter(_board(), SCOPE, "task-1")
    adapter.apply(message)
    conflict = message.model_copy(
        update={
            "payload": {
                **message.payload,
                "revision": {
                    **message.payload["revision"],
                    "content": "tampered",
                },
            }
        }
    )
    try:
        adapter.apply(conflict)
    except ValueError as exc:
        assert "conflict" in str(exc)
    else:
        raise AssertionError("same-position preview conflict was accepted")


def test_markdown_actual_records_detect_body_tampering(canonical_session, tmp_path):
    message = _messages(canonical_session)["markdown_export"]
    adapter = MarkdownExportProjectionAdapter(
        build_session_factory(canonical_session.get_bind()),
        SCOPE,
        "task-1",
        root=str(tmp_path),
    )
    adapter.apply(message)
    expected = adapter.expected_records((message,))
    _, path, _ = adapter._paths()
    path.write_text("tampered", encoding="utf-8")
    assert adapter.actual_records(SCOPE) != expected


def test_redis_stream_clear_is_scope_isolated(canonical_session):
    message = _messages(canonical_session)["redis_stream"]
    redis = FakeRedis()
    board = _board(redis)
    adapter = RedisStreamProjectionAdapter(board, SCOPE, "task-1")
    other_scope = ProjectionScope("tenant-1", "project-other")
    other = RedisStreamProjectionAdapter(board, other_scope, "task-1")
    other_message = message.model_copy(
        update={
            "project_id": other_scope.project_id,
            "projection_event_id": "event-other",
            "commit_id": "commit-other",
            "revision_id": "revision-other",
        }
    )
    adapter.apply(message)
    other.apply(other_message)
    adapter.clear(SCOPE)
    assert adapter.actual_records(SCOPE) == ()
    assert other.actual_records(other_scope)


def test_factory_builds_scanner_ready_routing_adapters(canonical_session, tmp_path):
    messages = _messages(canonical_session)
    factory = build_session_factory(canonical_session.get_bind())
    adapters = build_projection_adapters(
        factory,
        blackboard=_board(),
        vector_store=object(),
        markdown_root=str(tmp_path),
    )
    assert set(adapters) == {
        "legacy_world_event",
        "handover_context",
        "chroma_story_chunks",
        "redis_stream",
        "task_preview",
        "markdown_export",
        "analytics",
    }
    receipt = adapters["task_preview"].apply(messages["task_preview"])
    assert receipt.projector_id == "task_preview"
