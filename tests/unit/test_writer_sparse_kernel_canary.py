from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from experiments.writer_sparse_kernel_canary.builder import (
    build,
    build_requests,
)
from experiments.writer_sparse_kernel_canary.runner import (
    GenerationAlreadyAttemptedError,
    finalize,
    mark_attempted,
    reserve,
)
from experiments.writer_sparse_kernel_canary.review import (
    SparseKernelBlindReview,
    validate_review_against_public,
)


def test_queue_is_two_scenes_three_arms_two_repeats():
    queue = build_requests()
    assert len(queue) == 12
    assert {item["scene_id"] for item in queue} == {"SC9", "SC12"}
    assert {item["arm"] for item in queue} == {"A", "B", "C"}
    assert {item["repeat"] for item in queue} == {1, 2}
    assert len({item["generation_id"] for item in queue}) == 12


def test_b_and_c_share_locked_choice_but_not_representation():
    queue = build_requests()
    by_block = {}
    for item in queue:
        by_block.setdefault(item["canary_block_id"], {})[item["arm"]] = item
    for arms in by_block.values():
        b = json.loads(arms["B"]["messages"][1]["content"])["arm_guidance"]
        c = json.loads(arms["C"]["messages"][1]["content"])["arm_guidance"]
        assert b["locked_choice"] == c["kernel"]["irreversible_micro_choice"]
        assert "ordered_realization_plan" in b
        assert "kernel" in c
        assert "ordered_realization_plan" not in c


def test_all_arms_share_identical_common_scene_brief():
    queue = build_requests()
    by_block = {}
    for item in queue:
        payload = json.loads(item["messages"][1]["content"])
        by_block.setdefault(item["canary_block_id"], []).append(
            payload["common_scene_brief"]
        )
    assert all(values[0] == values[1] == values[2] for values in by_block.values())


def test_provider_config_is_identical_and_has_no_retries():
    queue = build_requests()
    specs = [item["provider_config"] for item in queue]
    assert all(spec == specs[0] for spec in specs)
    assert specs[0]["transport_max_retries"] == 0
    assert specs[0]["thinking"] == "disabled"
    assert specs[0]["json_mode"] is False


def test_build_creates_twelve_pending_single_attempt_rows(tmp_path: Path):
    output = tmp_path / "output"
    manifest = build(output, tmp_path / "report.md")
    assert manifest["generation_requests"] == 12
    with sqlite3.connect(output / "private/generation-ledger.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM generation_queue").fetchone()[0] == 12
        assert db.execute(
            "SELECT COUNT(*) FROM generation_queue WHERE status='pending' AND attempt_count=0"
        ).fetchone()[0] == 12
        assert db.execute("SELECT COUNT(*) FROM generation_attempts").fetchone()[0] == 0


def test_reserved_request_cannot_be_reserved_twice(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    item = build_requests()[0]
    ledger = output / "private/generation-ledger.sqlite"
    reserve(ledger, item)
    mark_attempted(ledger, item["generation_id"])
    with pytest.raises(GenerationAlreadyAttemptedError):
        reserve(ledger, item)
    finalize(
        ledger,
        item["generation_id"],
        "failed",
        error_type="SyntheticFailure",
        error_message_sha256="0" * 64,
    )
    with pytest.raises(GenerationAlreadyAttemptedError):
        reserve(ledger, item)


def test_build_refuses_to_reset_existing_ledger(tmp_path: Path):
    output = tmp_path / "output"
    build(output, tmp_path / "report.md")
    with pytest.raises(FileExistsError):
        build(output, tmp_path / "report.md")


def test_blind_review_schema_separates_quality_metrics_without_total_score():
    schema = SparseKernelBlindReview.model_json_schema()
    text = json.dumps(schema)
    assert "naturalness" in text
    assert "mechanicalness" in text
    assert "single_total_score" not in text
    assert "authorization" not in text
