import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "phase4-batch3-continuity-risk-guard-shadow.json"


def load_report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_batch3_is_offline_shadow_and_uses_frozen_inputs():
    report = load_report()

    assert report["offline_llm_calls"] == 0
    assert report["writer_generation_calls"] == 0
    assert report["production_behavior_changed"] is False
    assert report["context_manager_contract_changed"] is False
    assert report["writer_prompt_changed"] is False
    assert report["rag_or_model_changed"] is False
    assert report["evaluation_loaded_after_all_runtime_selections"] is True
    assert len(report["samples"]) == 10
    assert all(set(item["retrieval_filter"]) == {"task_id"} for item in report["samples"])
    assert all(len(item["retrieval_source_ids"]) == 5 for item in report["samples"])


def test_batch3_protects_required_items_but_fails_token_gate():
    report = load_report()

    assert report["token_stats"] == {
        "legacy_full": {"mean": 12406.4, "min": 10511, "max": 14480},
        "budgeted_broker": {"mean": 8390.4, "min": 7342, "max": 9348},
        "risk_guarded_broker": {"mean": 11871.6, "min": 10511, "max": 13304},
    }
    assert report["risk_guarded_reduction_vs_legacy"] == 0.0431
    assert report["risk_guarded_budget_overflow_count"] == 10
    assert report["gates"]["risk_guarded_reduction_at_least_20_percent"] is False
    assert all(
        value is True
        for key, value in report["gates"].items()
        if key != "risk_guarded_reduction_at_least_20_percent"
    )
    assert report["all_mechanical_gates_passed"] is False


def test_q04_q06_q07_and_q08_diagnostics_are_protected_with_evidence():
    report = load_report()

    for query in ("q04", "q06", "q07", "q08"):
        rows = report["diagnostic_queries"][query]
        assert rows
        for row in rows:
            assert row["protected"] is True
            assessment = row["risk_assessment"]
            assert assessment["protect"] is True
            assert assessment["reason"]
            assert all(risk["risk_type"] and risk["evidence"] for risk in assessment["risks"])


def test_every_c_decision_is_traceable_and_evaluation_free():
    report = load_report()
    forbidden = {"must_recall_facts", "gold_sections", "human_relevant", "supports_which_fact"}

    for sample in report["samples"]:
        assert sample["writer_production_prompt_hash_unchanged"] is True
        run = sample["profiles"]["risk_guarded_broker"]
        for item in run["items"]:
            assert item["source_id"] and item["text_hash"] and item["injection_position"]
            assert item["keep_reason"] if item["keep"] else item["drop_reason"]
            assert "text" not in item
            assert not (forbidden & item.keys())
            assessment = item.get("continuity_risk_assessment")
            if assessment:
                assert not (forbidden & assessment.keys())
                assert all(len(risk["evidence"]) <= 80 for risk in assessment["risks"])


def test_failed_conservative_guard_stops_without_deciding_architecture():
    decision = load_report()["decision"]

    assert decision == {
        "production_promotion": False,
        "generation_validation_started": False,
        "phase5_started": False,
        "status": "conservative_any_signal_guard_failed_stop",
        "recommendation": "do_not_decide_architecture_until_guard_rule_necessity_is_empirically_tested",
    }
