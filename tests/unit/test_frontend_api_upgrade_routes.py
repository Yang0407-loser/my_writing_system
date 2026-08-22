from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.dependencies as dependencies
import app.routers.analysis as analysis_routes
import app.routers.outline as outline_routes
import app.routers.tasks as task_routes
import app.config as app_config
from app.task_store import TaskStore
from redis.exceptions import ConnectionError as RedisConnectionError


class FakeBoard:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.checkpoints: dict[str, dict] = {}
        self.decisions: list[tuple[str, str, dict]] = []
        self.events: list[tuple[str, dict]] = []

    def set(self, task_id: str, key: str, value: object) -> None:
        self.tasks.setdefault(task_id, {})[key] = value

    def get(self, task_id: str, key: str):
        return self.tasks.get(task_id, {}).get(key)

    def get_all(self, task_id: str) -> dict:
        return dict(self.tasks.get(task_id, {}))

    def save_checkpoint(self, task_id: str, checkpoint: dict) -> None:
        self.checkpoints[task_id] = dict(checkpoint)

    def load_checkpoint(self, task_id: str):
        checkpoint = self.checkpoints.get(task_id)
        return dict(checkpoint) if checkpoint else None

    def push_decision(self, task_id: str, phase: str, decision: dict) -> None:
        self.decisions.append((task_id, phase, decision))

    def xadd_event(self, task_id: str, event: dict) -> None:
        self.events.append((task_id, event))


class UnavailableBoard:
    """Blackboard double that mirrors a Redis connection outage."""

    @staticmethod
    def _unavailable(*_args, **_kwargs):
        raise RedisConnectionError("redis unavailable")

    get = _unavailable
    get_all = _unavailable
    set = _unavailable
    load_checkpoint = _unavailable
    save_checkpoint = _unavailable
    push_notification = _unavailable


def test_draft_task_creates_stable_workspace_anchor(monkeypatch):
    board = FakeBoard()
    monkeypatch.setattr(task_routes, "bb", board)

    result = task_routes.create_draft_task()

    assert result["workspace_task_id"] == result["task_id"]
    assert board.tasks[result["task_id"]] == {
        "status": "draft",
        "workspace_task_id": result["task_id"],
        "active_task_id": result["task_id"],
    }


def test_status_uses_latest_workspace_configuration(monkeypatch):
    board = FakeBoard()
    board.tasks["run-2"] = {
        "status": "writing",
        "workspace_task_id": "workspace-1",
        "active_task_id": "run-2",
    }
    board.tasks["workspace-1"] = {
        "workspace_task_id": "workspace-1",
        "active_task_id": "run-2",
        "topic": "新主题",
        "world_setting": "新世界观",
        "target_words_per_section": 4200,
    }
    monkeypatch.setattr(task_routes, "bb", board)

    result = task_routes.get_task_status("run-2")

    assert result.workspace_task_id == "workspace-1"
    assert result.active_task_id == "run-2"
    assert result.topic == "新主题"
    assert result.world_setting == "新世界观"
    assert result.target_words_per_section == 4200


def test_workspace_patch_updates_active_and_workspace_checkpoints(monkeypatch):
    board = FakeBoard()
    board.tasks["workspace-1"] = {
        "status": "draft",
        "workspace_task_id": "workspace-1",
        "active_task_id": "run-2",
    }
    board.tasks["run-2"] = {
        "status": "writing",
        "workspace_task_id": "workspace-1",
        "active_task_id": "run-2",
    }
    board.checkpoints["run-2"] = {
        "task_id": "run-2",
        "config_topic": "旧主题",
    }
    monkeypatch.setattr(task_routes, "bb", board)

    result = task_routes.patch_task_workspace(
        "workspace-1",
        task_routes.WorkspacePatch(
            topic="新主题",
            style_profile={"preset_name": "冷系"},
            target_words_per_section=3600,
        ),
    )

    assert result["active_task_id"] == "run-2"
    assert board.tasks["workspace-1"]["topic"] == "新主题"
    assert board.checkpoints["run-2"]["config_topic"] == "新主题"
    assert board.checkpoints["run-2"]["config_target_words"] == 3600
    assert board.checkpoints["workspace-1"]["config_style_profile"] == {
        "preset_name": "冷系"
    }


def test_decision_returns_new_active_task_without_changing_workspace(monkeypatch):
    board = FakeBoard()
    board.tasks["run-1"] = {
        "status": "awaiting_outline_approval",
        "workspace_task_id": "workspace-1",
    }
    board.checkpoints["run-1"] = {
        "task_id": "run-1",
        "config_topic": "主题",
        "config_reference_text": "参考",
        "config_target_words": 3000,
    }
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(
        task_routes.writing_task,
        "delay",
        lambda **kwargs: SimpleNamespace(id="run-2", kwargs=kwargs),
    )

    result = task_routes.task_decision(
        "run-1",
        phase="outline",
        action="approve",
    )

    assert result["new_task_id"] == "run-2"
    assert result["workspace_task_id"] == "workspace-1"
    assert board.tasks["workspace-1"]["active_task_id"] == "run-2"
    assert board.tasks["run-2"]["workspace_task_id"] == "workspace-1"


def test_outline_evaluation_and_beacon_save(monkeypatch, tmp_path):
    result = outline_routes.evaluate_outline(
        "workspace-1",
        outline_routes.OutlineEvaluationBody(
            nodes=[
                {"id": "chapter-1", "parent_id": "", "title": "第一章"},
                {
                    "id": "leaf-1",
                    "parent_id": "chapter-1",
                    "title": "做出决定",
                    "description": "因为危机逼近，主角决定离开故乡。",
                    "key_points": ["告别亲友", "踏上旅程"],
                    "target_words": 1800,
                },
            ],
            from_section=1,
            to_section=1,
        ),
    )
    assert result["leaf_count"] == 1
    assert result["causality_score"] == 10
    assert result["logic_score"] == 10

    board = FakeBoard()
    monkeypatch.setattr(dependencies, "bb", board)
    monkeypatch.setattr(outline_routes.settings, "TASK_DB_PATH", str(tmp_path / "tasks.db"))
    saved = outline_routes.save_task_draft_beacon(
        "run-2", outline_routes.DraftBody(draft='[{"section":1}]')
    )
    assert saved == {"status": "saved"}
    assert board.tasks["run-2"]["draft_backup"] == '[{"section":1}]'


def test_export_creates_downloadable_record_from_checkpoint(monkeypatch, tmp_path):
    board = FakeBoard()
    board.tasks["run-2"] = {
        "status": "completed",
        "workspace_task_id": "workspace-1",
        "topic": "导出测试",
    }
    board.checkpoints["run-2"] = {
        "config_topic": "导出测试",
        "section_texts": {2: "第二节正文", 1: "第一节正文"},
    }
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(task_routes, "_export_root", lambda: tmp_path)

    result = task_routes.create_task_export(
        "run-2", task_routes.ExportRequest(format="md")
    )

    export_path = tmp_path / result["filename"]
    assert export_path.is_file()
    text = export_path.read_text(encoding="utf-8")
    assert text.index("第一节正文") < text.index("第二节正文")
    assert board.tasks["run-2"]["exports"][0]["export_id"] == result["export_id"]


def test_revision_helpers_replace_one_subsection_and_merge_snapshots():
    original = "第1节第1小节\n旧A\n\n第1节第2小节\n旧B"
    replaced = task_routes._replace_subsection_text(
        original, section=1, subsection=1, revised="第1节第1小节\n新A"
    )
    assert "新A" in replaced
    assert "旧B" in replaced

    merged = task_routes._merge_draft_snapshot(
        '[{"section":1,"subsection":1,"text":"旧A"},'
        '{"section":1,"subsection":2,"text":"旧B"}]',
        section=1,
        subsection=1,
        text="新A",
    )
    assert merged.count("新A") == 1
    assert "旧B" in merged


def test_revision_preview_does_not_mutate_server_draft(monkeypatch):
    board = FakeBoard()
    board.tasks["run-2"] = {
        "status": "completed",
        "workspace_task_id": "workspace-1",
        "draft": "第1节第1小节\n旧正文",
    }
    board.checkpoints["run-2"] = {
        "section_texts": {"1": "第1节第1小节\n旧正文"}
    }
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(
        "app.agents.writer.Writer.revise_subsection",
        lambda _self, original, instruction: f"{original}\n修订：{instruction}",
    )

    result = task_routes.revise_subsection(
        "run-2",
        task_routes.ReviseRequest(
            section=1,
            subsection=1,
            instruction="增强冲突",
            preview_only=True,
        ),
    )

    assert result["status"] == "preview"
    assert result["original"] == "第1节第1小节\n旧正文"
    assert result["revised"].endswith("修订：增强冲突")
    assert board.tasks["run-2"]["draft"] == "第1节第1小节\n旧正文"
    assert board.checkpoints["run-2"]["section_texts"]["1"].endswith("旧正文")


def test_revision_preview_recovers_from_durable_completed_workspace(
    monkeypatch, tmp_path
):
    path = tmp_path / "tasks.db"
    board = FakeBoard()
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(app_config.settings, "TASK_DB_PATH", str(path))
    monkeypatch.setattr(
        "app.agents.writer.Writer.revise_subsection",
        lambda _self, original, instruction: f"{original}\n修订：{instruction}",
    )
    with TaskStore(str(path)) as store:
        store.save_workspace(
            "workspace-1",
            {
                "active_task_id": "run-2",
                "status": "completed",
                "draft_backup": '[{"section":1,"subsection":1,"text":"持久化正文"}]',
            },
        )

    result = task_routes.revise_subsection(
        "run-2",
        task_routes.ReviseRequest(
            section=1,
            subsection=1,
            instruction="增强冲突",
            preview_only=True,
        ),
    )

    assert result["status"] == "preview"
    assert result["original"] == "持久化正文"
    assert result["revised"].endswith("修订：增强冲突")


def test_accept_revision_creates_versions_and_restore_reapplies_content(
    monkeypatch, tmp_path
):
    path = tmp_path / "tasks.db"
    board = FakeBoard()
    board.tasks["run-2"] = {
        "status": "completed",
        "workspace_task_id": "workspace-1",
        "draft": "第1节第1小节\n旧正文",
        "draft_backup": '[{"section":1,"subsection":1,"text":"旧正文"}]',
    }
    board.checkpoints["run-2"] = {
        "section_texts": {"1": "第1节第1小节\n旧正文"}
    }
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(app_config.settings, "TASK_DB_PATH", str(path))
    with TaskStore(str(path)) as store:
        store.save_workspace(
            "workspace-1",
            {
                "active_task_id": "run-2",
                "status": "completed",
                "draft_backup": '[{"section":1,"subsection":1,"text":"旧正文"}]',
            },
        )

    accepted = task_routes.patch_draft_subsection(
        "run-2",
        1,
        1,
        task_routes.DraftSectionPatch(
            base_text="旧正文",
            text="新正文",
            instruction="增强冲突",
        ),
    )
    versions = task_routes.list_draft_versions("run-2", section=1, subsection=1)
    baseline = next(item for item in versions["versions"] if item["source"] == "baseline")
    restored = task_routes.restore_draft_version("run-2", baseline["version_id"])

    assert accepted["version"]["source"] == "ai_revision"
    assert [item["source"] for item in versions["versions"]] == [
        "ai_revision",
        "baseline",
    ]
    assert restored["text"] == "旧正文"
    with TaskStore(str(path)) as store:
        workspace = store.get_workspace("workspace-1")
        history = store.list_draft_versions("workspace-1", section=1, subsection=1)
    assert "旧正文" in workspace["draft_backup"]
    assert history[0]["source"] == "restore"


def test_accept_revision_rejects_a_stale_preview(monkeypatch, tmp_path):
    path = tmp_path / "tasks.db"
    board = FakeBoard()
    board.tasks["run-2"] = {
        "status": "completed",
        "workspace_task_id": "workspace-1",
        "draft_backup": '[{"section":1,"subsection":1,"text":"服务器新正文"}]',
    }
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(app_config.settings, "TASK_DB_PATH", str(path))
    with TaskStore(str(path)) as store:
        store.save_workspace(
            "workspace-1",
            {
                "active_task_id": "run-2",
                "draft_backup": '[{"section":1,"subsection":1,"text":"服务器新正文"}]',
            },
        )

    with pytest.raises(task_routes.HTTPException) as caught:
        task_routes.patch_draft_subsection(
            "run-2",
            1,
            1,
            task_routes.DraftSectionPatch(
                base_text="过期正文",
                text="候选正文",
                instruction="增强冲突",
            ),
        )

    assert caught.value.status_code == 409
    with TaskStore(str(path)) as store:
        assert store.list_draft_versions("workspace-1") == []


def test_draft_save_resolves_active_task_to_durable_workspace(monkeypatch, tmp_path):
    board = FakeBoard()
    monkeypatch.setattr(dependencies, "bb", board)
    monkeypatch.setattr(outline_routes.settings, "TASK_DB_PATH", str(tmp_path / "tasks.db"))
    with TaskStore(str(tmp_path / "tasks.db")) as store:
        store.save_workspace(
            "workspace-1",
            {"active_task_id": "run-2", "topic": "恢复项目", "status": "writing"},
        )

    outline_routes.save_task_draft(
        "run-2", outline_routes.DraftBody(draft='[{"section":1,"text":"正文"}]')
    )

    with TaskStore(str(tmp_path / "tasks.db")) as store:
        project = store.get_workspace("workspace-1")
        assert store.get_workspace("run-2") is None
    assert "正文" in project["draft_backup"]


def test_export_falls_back_to_durable_workspace(monkeypatch, tmp_path):
    board = FakeBoard()
    monkeypatch.setattr(task_routes, "bb", board)
    monkeypatch.setattr(app_config.settings, "TASK_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(task_routes, "_export_root", lambda: tmp_path)
    with TaskStore(str(tmp_path / "tasks.db")) as store:
        store.save_workspace(
            "workspace-1",
            {
                "active_task_id": "run-2",
                "topic": "持久化导出",
                "draft_backup": "服务器重启后仍可导出",
                "status": "completed",
            },
        )

    result = task_routes.create_task_export(
        "run-2", task_routes.ExportRequest(format="txt")
    )

    assert (tmp_path / result["filename"]).read_text(encoding="utf-8") == (
        "服务器重启后仍可导出"
    )
    with TaskStore(str(tmp_path / "tasks.db")) as store:
        assert store.get_workspace("workspace-1")["exports"][0]["export_id"] == result[
            "export_id"
        ]


def test_status_falls_back_to_durable_workspace_when_redis_is_unavailable(
    monkeypatch, tmp_path
):
    path = tmp_path / "tasks.db"
    monkeypatch.setattr(task_routes, "bb", UnavailableBoard())
    monkeypatch.setattr(app_config.settings, "TASK_DB_PATH", str(path))
    with TaskStore(str(path)) as store:
        store.save_workspace(
            "workspace-1",
            {
                "active_task_id": "run-2",
                "topic": "离线恢复项目",
                "status": "writing",
                "outline": [{"id": "chapter-1", "title": "第一章"}],
                "draft_backup": "已保存正文",
            },
        )

    result = task_routes.get_task_status("run-2")

    assert result.workspace_task_id == "workspace-1"
    assert result.active_task_id == "run-2"
    assert result.status == "writing"
    assert result.topic == "离线恢复项目"
    assert result.outline == [{"id": "chapter-1", "title": "第一章"}]
    assert result.draft == "已保存正文"
    assert result.runtime_available is False
    assert result.data_source == "durable_workspace"


def test_workspace_patch_persists_when_redis_is_unavailable(monkeypatch, tmp_path):
    path = tmp_path / "tasks.db"
    monkeypatch.setattr(task_routes, "bb", UnavailableBoard())
    monkeypatch.setattr(app_config.settings, "TASK_DB_PATH", str(path))
    with TaskStore(str(path)) as store:
        store.save_workspace(
            "workspace-1",
            {"active_task_id": "run-2", "topic": "旧主题", "status": "draft"},
        )

    result = task_routes.patch_task_workspace(
        "workspace-1", task_routes.WorkspacePatch(topic="新主题")
    )

    assert result["workspace_task_id"] == "workspace-1"
    assert result["active_task_id"] == "run-2"
    assert result["topic"] == "新主题"
    with TaskStore(str(path)) as store:
        assert store.get_workspace("workspace-1")["topic"] == "新主题"


def test_outline_and_draft_use_durable_workspace_when_redis_is_unavailable(
    monkeypatch, tmp_path
):
    path = tmp_path / "tasks.db"
    board = UnavailableBoard()
    monkeypatch.setattr(dependencies, "bb", board)
    monkeypatch.setattr(outline_routes.settings, "TASK_DB_PATH", str(path))
    with TaskStore(str(path)) as store:
        store.save_workspace(
            "workspace-1",
            {
                "active_task_id": "run-2",
                "status": "draft",
                "outline": [
                    {
                        "id": "chapter-1",
                        "title": "第一章",
                        "children": [
                            {"id": "leaf-1", "title": "出发", "children": []}
                        ],
                    }
                ],
            },
        )

    loaded = outline_routes.get_outline("run-2")
    assert loaded["tree"][0]["title"] == "第一章"

    saved_outline = outline_routes.save_outline(
        "run-2",
        outline_routes.OutlineNodesBody(
            nodes=[
                {"id": "chapter-2", "parent_id": "", "title": "第二章"},
                {
                    "id": "leaf-2",
                    "parent_id": "chapter-2",
                    "title": "抵达",
                },
            ]
        ),
    )
    assert saved_outline["nodes"][0]["title"] == "第二章"

    outline_routes.save_task_draft(
        "run-2", outline_routes.DraftBody(draft='[{"text":"离线正文"}]')
    )
    assert outline_routes.get_task_draft("run-2") == {
        "draft": '[{"text":"离线正文"}]'
    }

    with TaskStore(str(path)) as store:
        workspace = store.get_workspace("workspace-1")
    assert workspace["outline"][0]["title"] == "第二章"
    assert "离线正文" in workspace["draft_backup"]


def test_export_uses_durable_workspace_when_redis_is_unavailable(
    monkeypatch, tmp_path
):
    path = tmp_path / "tasks.db"
    export_root = tmp_path / "exports"
    export_root.mkdir()
    monkeypatch.setattr(task_routes, "bb", UnavailableBoard())
    monkeypatch.setattr(app_config.settings, "TASK_DB_PATH", str(path))
    monkeypatch.setattr(task_routes, "_export_root", lambda: export_root)
    with TaskStore(str(path)) as store:
        store.save_workspace(
            "workspace-1",
            {
                "active_task_id": "run-2",
                "topic": "离线导出",
                "draft_backup": "持久化正文",
                "status": "completed",
            },
        )

    result = task_routes.create_task_export(
        "run-2", task_routes.ExportRequest(format="txt")
    )

    assert (export_root / result["filename"]).read_text(
        encoding="utf-8"
    ) == "持久化正文"


def test_event_graph_falls_back_to_durable_task_data_when_redis_is_unavailable(
    monkeypatch, tmp_path
):
    path = tmp_path / "tasks.db"
    monkeypatch.setattr(analysis_routes, "bb", UnavailableBoard())
    monkeypatch.setattr(app_config.settings, "TASK_DB_PATH", str(path))
    event = {
        "event_id": "event-1",
        "type": "arc_milestone",
        "description": "主角离开故乡",
        "section": 1,
        "subsection": 1,
        "status": "done",
    }
    with TaskStore(str(path)) as store:
        store.save("run-2", {"status": "completed", "events": [event]})

    result = analysis_routes.get_task_events("run-2")

    assert result["events"] == [event]
    assert result["summary"] == {
        "arc_milestones_total": 1,
        "arc_milestones_done": 1,
        "arc_milestones_deviated": 0,
    }
    assert result["runtime_available"] is False
    assert result["data_source"] == "durable_task_history"
