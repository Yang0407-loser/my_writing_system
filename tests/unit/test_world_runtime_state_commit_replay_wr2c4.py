import json

from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.state_commit_replay_wr2c4 import (
    replay_manifest,
)


_TYPES = (
    "storefront_public_sale", "storefront_public_handoff", "knowledge_state",
    "resignation_acknowledgement", "unsourced_project_fact", "object_state",
    "repeated_completed_event", "employment_state", "publication_state",
    "resignation_delivery", "resignation_personal_record", "clock_state",
    "location_state",
)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _empty_response():
    return json.dumps({
        "judgments": [
            {
                "change_type": change_type,
                "occurred": False,
                "after_value": None,
                "mode": "actual",
                "epistemic": "asserted",
                "evidence": [],
            }
            for change_type in _TYPES
        ]
    }, ensure_ascii=False)


def _knowledge_response():
    return json.dumps({
        "judgments": [
            {
                "change_type": "knowledge_state",
                "occurred": True,
                "after_value": "perceived",
                "mode": "actual",
                "epistemic": "asserted",
                "evidence": [
                    {"excerpt": "林晚把整份文档发进工作群", "occurrence": 1},
                    {"excerpt": "阿吴随即在群里引用正文那句“五点到了”", "occurrence": 1},
                ],
            }
        ]
    }, ensure_ascii=False)


def test_replay_manifest_commits_and_skips_without_provider(tmp_path):
    states = wr1r._artifacts()[1]
    knowledge_text = "林晚把整份文档发进工作群。阿吴随即在群里引用正文那句“五点到了”，问她是不是写错。"
    empty_text = "顾客举起付款码，周野摇头没收费，面包仍放在柜台上。"
    manifest = {
        "samples": [
            {
                "sample_id": "SYN-01",
                "scene_id": "adversarial-unpublished-knowledge",
                "state_variant": "before",
                "base_revision": states["before"].revision,
                "text": knowledge_text,
            },
            {
                "sample_id": "SYN-02",
                "scene_id": "adversarial-storefront-hours",
                "state_variant": "before",
                "base_revision": states["before"].revision,
                "text": empty_text,
            },
        ]
    }
    runtime = tmp_path / "runtime"
    _write(runtime / "private/locked-manifest.json", manifest)
    _write_text(runtime / "private/outputs/SYN-01.json", _knowledge_response())
    _write_text(runtime / "private/outputs/SYN-02.json", _empty_response())

    records = replay_manifest(
        source="test",
        runtime_dir=runtime,
        committer=WorldRuntimeStateCommitter(),
        states=states,
    )
    by_id = {record["sample_id"]: record for record in records}
    assert by_id["SYN-01"]["status"] == "committed"
    assert by_id["SYN-01"]["after_revision"] == 8
    assert by_id["SYN-01"]["idempotent_replay"] is True
    assert by_id["SYN-02"]["status"] == "no_commit_no_accepted"
