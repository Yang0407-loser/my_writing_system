import hashlib
import inspect

from tests.benchmarks import phase4r_r5_boundary_validator as validator_module
from tests.benchmarks.phase4r_r5_boundary_validator import BoundaryValidator, Contract


def contract(query_index):
    return Contract(
        query_index=query_index,
        section=1,
        subsection=1,
        intent="test",
        spec_hash="a" * 64,
        source_refs=({"source_id": "test", "text_hash": "b" * 64, "role": "contract"},),
    )


def validate(query_index, text):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return BoundaryValidator().validate(contract(query_index), "candidate_01", text, digest)


def test_q7_current_actions_pass():
    result = validate(7, "林晚按下删除。她推开操作间的门。\n\n“周野，那些照片和视频全删了。”")
    assert all(item["passed"] for item in result["required_event_results"])
    assert {item["observed_state"] for item in result["required_event_results"]} == {"current"}


def test_q7_future_and_past_backup_do_not_count_as_current_completion():
    future = validate(7, "林晚按下删除。她想，下个周六再去直面周野。")
    assert future["required_event_results"][1]["observed_state"] == "future"
    assert future["required_event_results"][1]["passed"] is False

    backup = validate(7, "帖子五天前当晚就删了。草稿箱还留着备份，她现在按下删除。")
    deletion = backup["required_event_results"][0]
    assert deletion["observed_state"] == "conflicted_past_original_and_current_backup"
    assert deletion["passed"] is False


def test_q8_detects_actual_or_planned_advance_but_ignores_negation():
    actual = validate(8, "她尝到微咸，反思分享边界。随后按下快门，提出第三个问题。")
    assert {item["event_id"] for item in actual["boundary_violations"]} == {"ask_zhou", "current_photograph"}

    planned = validate(8, "她尝到咸味，明白尊重的边界。明天周六来当店员。")
    assert {item["event_id"] for item in planned["boundary_violations"]} >= {"return_to_store", "store_participation"}

    negated = validate(8, "她尝到微咸，反思分享边界。她没有删帖，也没有按下快门。")
    assert negated["boundary_violations"] == []


def test_q4_relative_assertion_is_an_exploratory_warning_with_trace():
    result = validate(4, "面包婚礼开始，大家见证仪式。物业老刘说，我爹去年走了，留下半袋黑麦粉。")
    assert result["unsupported_fact_warnings"]
    warning = result["unsupported_fact_warnings"][0]
    assert warning["evidence_spans"][0]["end"] > warning["evidence_spans"][0]["start"]
    assert warning["source_refs"]


def test_prediction_module_has_no_evaluation_answer_dependencies():
    source = inspect.getsource(validator_module)
    forbidden = (
        "blind_review.completed.json",
        "evaluation.private.json",
        "defect_evidence",
        "preference",
        "hard_violations",
        "continuity_defects",
        "event_order_defects",
    )
    assert all(name not in source for name in forbidden)
