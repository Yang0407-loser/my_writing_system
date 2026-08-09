import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "state-frame-batch1-baseline.json"


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_state_frame_baseline_is_offline_and_traceable():
    report = _report()
    assert report["mode"] == "offline_contract_only"
    assert report["writer_generation_calls"] == 0
    assert report["llm_calls"] == 0
    assert report["runtime_evaluation_fields_used"] == []
    assert report["summary"]["case_count"] == 4
    assert report["summary"]["all_sources_traceable"] is True
    assert all(not item["contains_story_text"] for item in report["cases"])


def test_state_frame_preserves_unknowns_and_excludes_other_responsibilities():
    report = _report()
    assert report["summary"]["all_unknowns_preserved"] is True
    assert report["summary"]["plans_and_hard_rules_excluded"] is True
    by_id = {item["case_id"]: item for item in report["cases"]}
    assert by_id["time_and_unknown"]["counts"]["temporal"] == 1
    assert by_id["persistent_and_relationship"]["counts"]["relationship"] == 1
    assert by_id["open_and_conflict"]["counts"]["open_loops"] == 1


def test_production_writer_only_uses_state_frame_as_compatibility_persistence():
    tree = ast.parse((ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8"))
    imports = "\n".join(
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )
    assert "StateFrameHistoryRecorder" in imports
    assert "StateFrameCompiler" not in imports
    assert "StateFrameService" not in imports
