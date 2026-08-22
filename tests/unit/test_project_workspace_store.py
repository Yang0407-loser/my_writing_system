from app.task_store import TaskStore


def test_workspace_round_trip_includes_outline_and_draft(tmp_path):
    path = tmp_path / "tasks.db"

    with TaskStore(str(path)) as store:
        store.save_workspace(
            "workspace-1",
            {
                "active_task_id": "run-2",
                "topic": "测试项目",
                "style_profile": {"preset_name": "冷系"},
                "target_words_per_section": 3600,
                "outline": [{"id": "chapter-1", "title": "第一章"}],
                "draft_backup": '[{"section":1,"text":"正文"}]',
                "exports": [{"export_id": "export-1", "format": "md"}],
                "status": "writing",
            },
        )

    with TaskStore(str(path)) as reopened:
        project = reopened.get_workspace("workspace-1")
        projects = reopened.list_workspaces()

    assert project["active_task_id"] == "run-2"
    assert project["style_profile"] == {"preset_name": "冷系"}
    assert project["outline"][0]["title"] == "第一章"
    assert project["draft_backup"].startswith("[")
    assert project["exports"][0]["export_id"] == "export-1"
    assert projects[0]["workspace_task_id"] == "workspace-1"


def test_workspace_archive_is_excluded_by_default(tmp_path):
    with TaskStore(str(tmp_path / "tasks.db")) as store:
        store.save_workspace("workspace-1", {"archived": True})

        assert store.list_workspaces() == []
        assert store.list_workspaces(include_archived=True)[0]["archived"] is True


def test_workspace_can_be_found_by_active_task(tmp_path):
    with TaskStore(str(tmp_path / "tasks.db")) as store:
        store.save_workspace(
            "workspace-1", {"active_task_id": "run-2", "topic": "项目"}
        )

        assert store.find_workspace_for_task("run-2")["workspace_task_id"] == "workspace-1"


def test_draft_versions_round_trip_in_reverse_chronological_order(tmp_path):
    path = tmp_path / "tasks.db"

    with TaskStore(str(path)) as store:
        baseline = store.add_draft_version(
            "workspace-1",
            active_task_id="run-2",
            section=1,
            subsection=2,
            content="旧正文",
            source="baseline",
        )
        revised = store.add_draft_version(
            "workspace-1",
            active_task_id="run-2",
            section=1,
            subsection=2,
            content="新正文",
            source="ai_revision",
            instruction="增强冲突",
            parent_version_id=baseline["version_id"],
        )

        versions = store.list_draft_versions(
            "workspace-1", section=1, subsection=2
        )
        loaded = store.get_draft_version(revised["version_id"])

    assert [version["content"] for version in versions] == ["新正文", "旧正文"]
    assert loaded["instruction"] == "增强冲突"
    assert loaded["parent_version_id"] == baseline["version_id"]
