import json
from pathlib import Path


REPORT = Path("reports/phase4r-batch-r3-package-manifest.json")
BATCH2 = Path("reports/phase4-batch2-generation-quality-ab.json")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_r3_package_is_prepared_but_not_generated():
    report = load(REPORT)
    assert report["status"] == "prepared_not_generated"
    assert report["target_queries"] == [4, 6, 7, 8]
    assert report["arms"] == ["legacy_full", "budgeted_broker", "broker_scene_spec"]
    assert report["planned_generation_calls"] == 12
    assert report["writer_generation_calls"] == 0
    assert report["llm_calls"] == 0
    assert report["production_messages_hash_unchanged"] is True
    assert report["runtime_evaluation_fields_used"] == []
    assert report["private_runtime_dir_gitignored"] is True
    assert report["anonymous_candidate_order"] is True


def test_frozen_a_b_hashes_and_scene_sources_are_complete():
    report = load(REPORT)
    batch2 = {int(item["query_index"]): item for item in load(BATCH2)["samples"]}
    for query in report["queries"]:
        index = int(query["query_index"])
        arms = query["arms"]
        assert arms["legacy_full"]["messages_hash"] == batch2[index]["legacy_messages_hash"]
        assert arms["budgeted_broker"]["messages_hash"] == batch2[index]["broker_messages_hash"]
        assert arms["broker_scene_spec"]["messages_hash"] not in {
            arms["legacy_full"]["messages_hash"], arms["budgeted_broker"]["messages_hash"]
        }
        assert arms["broker_scene_spec"]["scene_spec_source_manifest"]
        assert all(item["source_id"] and item["text_hash"] for item in arms["broker_scene_spec"]["scene_spec_source_manifest"])
        assert all("text" not in item for arm in arms.values() for item in arm["context_items"])


def test_c_keeps_more_than_twenty_percent_input_reduction_and_report_has_no_private_payload():
    report = load(REPORT)
    totals = report["estimated_input_tokens"]
    assert 1 - totals["broker_scene_spec"] / totals["legacy_full"] >= 0.20
    raw = REPORT.read_text(encoding="utf-8")
    for forbidden in ('"messages"', '"query"', '"output"', '"candidate_for_arm"', '"private_mapping"'):
        assert forbidden not in raw
