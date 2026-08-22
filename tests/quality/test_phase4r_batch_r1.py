import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "phase4r-batch-r1-writer-hash-audit.json"
BATCH2 = ROOT / "reports" / "phase4-batch2-generation-quality-ab.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_phase4r_r1_freezes_all_ten_writer_message_hashes():
    report = load(REPORT)
    batch2 = load(BATCH2)
    expected = {
        int(item["query_index"]): item["legacy_messages_hash"]
        for item in batch2["samples"]
    }
    assert report["query_count"] == 10
    assert report["llm_calls"] == 0
    assert report["writer_generation_calls"] == 0
    assert report["acceptance"]["all_r1_hash_gates"] is True
    assert {
        int(item["query_index"]): item["messages_hash"]
        for item in report["samples"]
    } == expected


def test_phase4r_r1_report_contains_no_prompt_or_story_text():
    report = load(REPORT)

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    assert keys(report).isdisjoint({
        "messages", "prompt_text", "story_text", "prepared_context_fields",
    })
    assert all(item["source_manifest_count"] > 0 for item in report["samples"])
