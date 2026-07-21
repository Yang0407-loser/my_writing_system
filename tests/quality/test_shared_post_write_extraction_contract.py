import inspect
import json
from pathlib import Path

from app.agents.writer import Writer
from app.writing.post_write_extraction import SharedPostWriteExtractor


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "shared-post-write-extraction-shadow-integration.json"


def test_report_keeps_shadow_and_production_boundaries_explicit():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["status"] == "engineering_shadow_ready"
    assert report["scope"]["production_extractors_replaced"] == 0
    assert report["scope"]["authoritative_store_writes_added"] == 0
    assert report["integration"]["default"] == "off"
    assert report["integration"]["allowed_modes"] == ["off", "shadow"]
    assert report["integration"]["production_effect"] is False
    assert report["expected_shadow_cost"]["production_savings_claimed"] is False
    assert report["promotion_gate"]["one_real_shadow_task_only"] is True


def test_writer_runs_shadow_only_after_commit_and_preserves_legacy_extractors():
    source = inspect.getsource(Writer.run)
    assert source.index("commit_artifact = state_committer.commit_subsection(") < source.index(
        "shadow_post_write_extractor.observe_committed("
    )
    for legacy in (
        "handover_note = self._extract_handover(",
        "cm_char.update_states(",
        "extract_relations_from_text",
        "extract_from_section",
    ):
        assert legacy in source


def test_extractor_has_no_authoritative_store_imports_or_test_imports():
    source = inspect.getsource(SharedPostWriteExtractor)
    for forbidden in (
        "event_store", "world_state", "character_relation_store",
        "foreshadowing_store", "from tests", "import tests",
    ):
        assert forbidden not in source


def test_public_report_has_no_private_payload_keys():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    keys = set()

    def collect(value):
        if isinstance(value, dict):
            keys.update(str(key).lower() for key in value)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(report)
    assert keys.isdisjoint({"prompt", "messages", "full_text", "state_values", "api_key"})
