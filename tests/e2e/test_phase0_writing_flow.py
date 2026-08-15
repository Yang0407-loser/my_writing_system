from app.models import TaskState
from tests.e2e.support.deterministic_writer import DRAFT


def test_automatic_writing_cursor_order_recovery_and_exports(e2e_client):
    client, writer, board = e2e_client

    created = client.post("/tasks").json()
    workspace_id = created["workspace_task_id"]
    client.patch(
        f"/tasks/{workspace_id}/workspace",
        json={
            "topic": "雨夜来信",
            "world_setting": "近未来海港城",
            "story_synopsis": "失联记者寄回最后一封信",
            "reference_text": "冷静、克制的叙述参考",
            "target_words_per_section": 500,
        },
    ).raise_for_status()
    client.post(
        f"/tasks/{workspace_id}/outline",
        json={
            "nodes": [
                {
                    "id": "root-1",
                    "parentId": None,
                    "title": "第一章",
                    "description": "雨夜开场",
                },
                {
                    "id": "leaf-1",
                    "parentId": "root-1",
                    "title": "旧站台",
                    "description": "收到回信",
                },
            ]
        },
    ).raise_for_status()
    started = client.post(
        "/write?mode=celery",
        json={
            "task_id": workspace_id,
            "topic": "雨夜来信",
            "reference_text": "冷静、克制的叙述参考",
            "target_words_per_section": 500,
        },
    ).json()
    assert started["task_id"] == workspace_id
    assert started["workspace_task_id"] == workspace_id

    assert writer.advance(workspace_id) == "section_start"
    assert writer.advance(workspace_id) == "token_1"
    prefix = client.get(f"/stream/{workspace_id}?last_id=0-0&count=50").json()
    assert [event[1]["event"] for event in prefix["events"]] == [
        "section_start",
        "token",
    ]
    mid_cursor = prefix["last_id"]
    prefix_ids = {event[0] for event in prefix["events"]}

    assert [writer.advance(workspace_id) for _ in range(3)] == [
        "token_2",
        "section_end",
        "done",
    ]
    suffix = client.get(
        f"/stream/{workspace_id}?last_id={mid_cursor}&count=50"
    ).json()
    assert [event[1]["event"] for event in suffix["events"]] == [
        "token",
        "section_end",
        "done",
    ]
    assert prefix_ids.isdisjoint({event[0] for event in suffix["events"]})
    assert suffix["events"][0][1]["token"] == "她终于等到回信。"

    assert suffix["events"][-1][1]["event"] == "done"
    before_completed = client.get(f"/status/{workspace_id}").json()
    assert before_completed["status"] == "running"
    assert writer.advance(workspace_id) == "completed"
    assert client.get(f"/status/{workspace_id}").json()["status"] == "completed"
    final_cursor = suffix["last_id"]
    assert client.get(
        f"/stream/{workspace_id}?last_id={final_cursor}&count=50"
    ).json()["events"] == []

    board.delete(workspace_id)
    board.delete_checkpoint(workspace_id)
    board.stream_delete(workspace_id)
    workspace = client.get(f"/tasks/{workspace_id}/workspace").json()
    assert workspace["data_source"] == "durable_workspace"
    assert workspace["workspace_task_id"] == workspace_id
    assert workspace["active_task_id"] == workspace_id
    assert workspace["draft_backup"] == DRAFT
    projects = client.get("/projects").json()["projects"]
    assert any(
        project["workspace_task_id"] == workspace_id
        and project["draft_backup"] == DRAFT
        for project in projects
    )

    for export_format in ("md", "txt", "json"):
        export_response = client.post(
            f"/tasks/{workspace_id}/exports", json={"format": export_format}
        )
        assert export_response.status_code == 200, export_response.text
        record = export_response.json()
        download = client.get(
            f"/tasks/{workspace_id}/exports/{record['export_id']}/download"
        )
        assert download.status_code == 200
        if export_format == "json":
            assert download.json()["draft"] == DRAFT
        else:
            assert DRAFT in download.text


def test_interactive_approval_replaces_task_and_preserves_workspace(e2e_client):
    client, writer, board = e2e_client

    created = client.post("/tasks").json()
    workspace_id = created["workspace_task_id"]
    client.patch(
        f"/tasks/{workspace_id}/workspace",
        json={
            "topic": "雨夜来信",
            "world_setting": "近未来海港城",
            "story_synopsis": "失联记者寄回最后一封信",
            "reference_text": "冷静、克制的叙述参考",
            "target_words_per_section": 500,
        },
    ).raise_for_status()
    client.post(
        f"/tasks/{workspace_id}/outline",
        json={
            "nodes": [
                {
                    "id": "root-1",
                    "parentId": None,
                    "title": "第一章",
                    "description": "雨夜开场",
                },
                {
                    "id": "leaf-1",
                    "parentId": "root-1",
                    "title": "旧站台",
                    "description": "收到回信",
                },
            ]
        },
    ).raise_for_status()
    started = client.post(
        "/write?mode=interactive",
        json={
            "task_id": workspace_id,
            "topic": "雨夜来信",
            "reference_text": "冷静、克制的叙述参考",
            "target_words_per_section": 500,
        },
    ).json()
    old_task_id = started["task_id"]
    assert writer.advance(old_task_id) == "awaiting_outline_approval"
    old_status = client.get(f"/status/{old_task_id}").json()
    assert old_status["status"] == "awaiting_outline_approval"
    assert TaskState.model_validate(board.load_checkpoint(old_task_id))

    decision = client.post(
        f"/tasks/{old_task_id}/decide?phase=outline&action=approve"
    ).json()
    new_task_id = decision["new_task_id"]
    assert new_task_id != old_task_id
    assert decision["workspace_task_id"] == workspace_id
    assert client.get(f"/status/{old_task_id}").json()["active_task_id"] == new_task_id
    workspace = client.get(f"/tasks/{workspace_id}/workspace").json()
    assert workspace["active_task_id"] == new_task_id

    writer.complete(new_task_id)
    replacement_checkpoint = TaskState.model_validate(
        board.load_checkpoint(new_task_id)
    )
    assert replacement_checkpoint.task_id == new_task_id
    assert replacement_checkpoint.config_topic == "雨夜来信"
    assert replacement_checkpoint.config_reference_text == "冷静、克制的叙述参考"
    assert replacement_checkpoint.config_target_words == 500
    replacement = client.get(f"/stream/{new_task_id}?last_id=0-0&count=50").json()
    assert replacement["events"][0][1]["event"] == "section_start"
    assert replacement["events"][-1][1]["event"] == "done"
    assert replacement["last_id"] != "0-0"
    assert client.get(f"/status/{new_task_id}").json()["status"] == "completed"
