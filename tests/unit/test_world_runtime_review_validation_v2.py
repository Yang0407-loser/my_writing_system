import copy
import hashlib
import json

from experiments.world_runtime_writer_canary.review_validation_v2 import (
    validate_review_result,
)


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _fixture():
    package = {
        "candidate_count": 2,
        "arms_hidden": True,
        "candidates": [
            {"candidate_id": "C01", "scene_id": "s1", "text": "甲开门。乙进入。"},
            {"candidate_id": "C02", "scene_id": "s1", "text": "甲提交，随后已发布。"},
        ],
    }
    event = {
        "outcome": 2,
        "bridge": 2,
        "evidence": 2,
        "illegal_transition": "no",
        "evidence_excerpt": "甲开门……乙进入",
        "notes": "",
    }
    review = {
        "candidate_id": "C01",
        "scene_id": "s1",
        "events": {
            name: copy.deepcopy(event)
            for name in (
                "enter_workshop",
                "publish_article",
                "share_with_jiqing",
                "deliver_resignation",
            )
        },
        "unsourced_settings": [],
        "overall": {
            "world_consistency": 4,
            "required_outcome_complete": 2,
            "required_bridge_complete": 2,
            "evidence_sufficient": 2,
            "unsourced_setting_severity": 0,
            "prose_naturalness": 4,
            "instructional_feel": 2,
        },
    }
    review2 = copy.deepcopy(review)
    review2["candidate_id"] = "C02"
    review2["events"] = {
        name: {**copy.deepcopy(event), "evidence_excerpt": "甲提交……已发布"}
        for name in review2["events"]
    }
    result = {
        "reviewer": {
            "reviewer_id": "model-01",
            "reviewer_type": "model",
            "independent_review": True,
            "read_other_reviews": False,
            "read_blind_key": False,
            "read_machine_evaluation": False,
            "blindness_compromised": False,
        },
        "source": {"package_sha256": "", "candidate_count": 2},
        "reviews": [review, review2],
        "scene_rankings": [
            {
                "scene_id": "s1",
                "consistency_ranking": ["C01", "C02"],
                "prose_ranking": ["C02", "C01"],
                "best_balance": "C01",
            }
        ],
        "review_complete": True,
    }
    return package, result


def test_valid_model_review_is_not_human_vote_eligible(tmp_path):
    package, result = _fixture()
    package_path = tmp_path / "package.json"
    result_path = tmp_path / "result.json"
    _write_json(package_path, package)
    result["source"]["package_sha256"] = hashlib.sha256(package_path.read_bytes()).hexdigest()
    _write_json(result_path, result)

    receipt = validate_review_result(result_path, package_path)

    assert receipt.valid is True
    assert receipt.human_vote_eligible is False
    assert receipt.issues == ()


def test_rejects_unsupported_excerpt_and_duplicate_ranking(tmp_path):
    package, result = _fixture()
    package_path = tmp_path / "package.json"
    result_path = tmp_path / "result.json"
    _write_json(package_path, package)
    result["source"]["package_sha256"] = hashlib.sha256(package_path.read_bytes()).hexdigest()
    result["reviews"][0]["events"]["enter_workshop"]["evidence_excerpt"] = "正文不存在"
    result["scene_rankings"][0]["prose_ranking"] = ["C01", "C01"]
    _write_json(result_path, result)

    receipt = validate_review_result(result_path, package_path)

    assert receipt.valid is False
    assert any("unsupported by candidate text" in issue for issue in receipt.issues)
    assert any("prose_ranking" in issue for issue in receipt.issues)


def test_blindness_compromise_invalidates_submission(tmp_path):
    package, result = _fixture()
    package_path = tmp_path / "package.json"
    result_path = tmp_path / "result.json"
    _write_json(package_path, package)
    result["source"]["package_sha256"] = hashlib.sha256(package_path.read_bytes()).hexdigest()
    result["reviewer"]["blindness_compromised"] = True
    _write_json(result_path, result)

    receipt = validate_review_result(result_path, package_path)

    assert receipt.valid is False
    assert "reviewer.blindness_compromised: must be false" in receipt.issues
