import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "phase4-batch35-guard-empirical-audit.json"


def load_report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_batch35_uses_exactly_four_existing_real_budgeted_outputs():
    report = load_report()
    assert report["new_generation_calls"] == 0
    assert report["historical_budgeted_writer_outputs_reused"] == 4
    assert [item["query_index"] for item in report["samples"]] == [4, 6, 7, 8]
    assert all(item["budgeted_output_sha256"] for item in report["samples"])
    assert report["all_budgeted_hashes_match_batch3"] is True


def test_batch35_distinguishes_net_regression_from_shared_defect():
    report = load_report()
    assert report["empirical_net_regression_scene_count"] == 3
    assert report["shared_defect_not_attributable_to_broker_scene_count"] == 1
    regressions = {item["query_index"] for item in report["samples"] if item["empirical_net_regression"]}
    assert regressions == {4, 6, 7}
    q8 = next(item for item in report["samples"] if item["query_index"] == 8)
    assert q8["budgeted_minus_legacy_issue_delta"]["causality_defects"] == 0


def test_batch35_does_not_promote_guard_heuristics_to_architecture_fact():
    report = load_report()
    unsupported = report["conclusions"]["not_supported"]
    assert "all 19 protected older items are necessary" in unsupported
    assert "all whole-item selection strategies are infeasible" in unsupported
    assert report["production_behavior_changed"] is False
    assert report["generated_prose_committed"] is False
