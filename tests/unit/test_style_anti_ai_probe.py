from __future__ import annotations

import json
from pathlib import Path
import pytest

from experiments.style_anti_ai_probe.builder import CONFIG, build, build_requests, load_json, validate_config
from experiments.style_anti_ai_probe.runner import basic_text_checks


def test_config_freezes_eight_text_probe() -> None:
    config = load_json(CONFIG)
    validate_config(config)
    assert set(config["arms"]) == {"W", "WA"}
    assert len(config["scenes"]) == 2
    assert config["repeats_per_scene"] == 2


def test_queue_has_four_paired_blocks() -> None:
    queue = build_requests()
    assert len(queue) == 8
    assert len({item["block_id"] for item in queue}) == 4
    for block_id in {item["block_id"] for item in queue}:
        members = [item for item in queue if item["block_id"] == block_id]
        assert {item["arm"] for item in members} == {"W", "WA"}
        payloads = [json.loads(item["messages"][1]["content"]) for item in members]
        assert payloads[0]["fixed_scene_contract"] == payloads[1]["fixed_scene_contract"]
        assert payloads[0]["commercial_narrative_policy"] == payloads[1]["commercial_narrative_policy"]
        assert sum("language_realization_policy" in p for p in payloads) == 1


def test_repeats_use_identical_frozen_requests() -> None:
    queue = build_requests()
    hashes = [item["request_sha256"] for item in queue]
    assert len(set(hashes)) == 4
    assert all(hashes.count(value) == 2 for value in set(hashes))


def test_builder_refuses_ledger_reset(tmp_path: Path) -> None:
    output, report = tmp_path / "output", tmp_path / "report.md"
    assert build(output, report)["generation_requests"] == 8
    with pytest.raises(FileExistsError):
        build(output, report)


def test_length_is_diagnostic_not_truncation() -> None:
    result = basic_text_checks("正文。", "stop")
    assert result["nonempty"] is True
    assert result["within_target_band_1000_1600"] is False
    assert result["truncation_detected"] is False
