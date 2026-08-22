"""WR2-B sealed holdout contract tests (preflight).

These tests verify the sealed holdout package and its lock, the frozen source
hashes, and the runner import safety.  They never call the frozen extractor
or validator; the sealed holdout is executed exactly once by the runner.
"""

import hashlib
import importlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANARY_DIR = ROOT / "experiments" / "world_runtime_writer_canary"
RUNTIME = ROOT / ".world_runtime_wr2b_sealed_holdout_runtime"
HOLDOUT_PATH = RUNTIME / "private" / "sealed-holdout-v1.json"
LOCK_PATH = RUNTIME / "holdout-lock.json"
LEDGER_PATH = RUNTIME / "attempt-ledger.json"
PREFLIGHT_PATH = RUNTIME / "preflight-audit.json"
RESULT_PATH = ROOT / "reports" / "world-runtime-wr2b-sealed-holdout-result-2026-08-04.json"

SOURCE_FILES = {
    "ontology_validator": "delta_shadow_wr2b.py",
    "layered_extractor": "layered_extractor_wr2b.py",
    "development_runner": "development_wr2b.py",
}

ALLOWED_SCENES = {
    "adversarial-storefront-hours",
    "adversarial-unpublished-knowledge",
    "adversarial-object-and-repeat",
    "adversarial-employment-transition",
}
ALLOWED_VARIANTS = {"before", "after", "after_augmented"}
VALIDATION_VALUES = {"valid", "invalid", "unresolved"}
ALL_13_TYPES = {
    "storefront_public_sale", "storefront_public_handoff", "knowledge_state",
    "resignation_acknowledgement", "unsourced_project_fact", "object_state",
    "repeated_completed_event", "employment_state", "publication_state",
    "resignation_delivery", "resignation_personal_record", "clock_state", "location_state",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_holdout() -> dict:
    return json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))


def _load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def test_three_source_hashes_match_lock():
    lock = _load_lock()
    for key, filename in SOURCE_FILES.items():
        assert _sha256(CANARY_DIR / filename) == lock["sources"][key]["sha256"], key


def test_holdout_hash_matches_lock():
    lock = _load_lock()
    assert _sha256(HOLDOUT_PATH) == lock["holdout_sha256"]


def test_holdout_schema_and_scene_variants():
    holdout = _load_holdout()
    cases = holdout["cases"]
    for case in cases:
        for field in ("case_id", "class", "scene_id", "state_variant", "text", "changes"):
            assert field in case, f"{case.get('case_id')} missing {field}"
        assert case["scene_id"] in ALLOWED_SCENES
        assert case["state_variant"] in ALLOWED_VARIANTS
        assert case["text"].strip()
        for change in case["changes"]:
            for field in ("change_type", "subject", "predicate", "after_value", "mechanism", "expected_validation"):
                assert field in change, f"{case['case_id']} change missing {field}"
            assert change["expected_validation"] in VALIDATION_VALUES


def test_case_ids_unique_and_formatted():
    cases = _load_holdout()["cases"]
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case_id in ids:
        assert re.fullmatch(r"HLD2-[A-Z0-9]+-\d{3}", case_id), case_id


def test_count_requirements():
    cases = _load_holdout()["cases"]
    changes = [change for case in cases for change in case["changes"]]
    valid = sum(1 for change in changes if change["expected_validation"] == "valid")
    invalid = sum(1 for change in changes if change["expected_validation"] == "invalid")
    unresolved = sum(1 for change in changes if change["expected_validation"] == "unresolved")
    empty = sum(1 for case in cases if not case["changes"])
    assert 36 <= len(cases) <= 44
    assert len(changes) >= 28
    assert valid >= 10
    assert invalid >= 10
    assert unresolved >= 4
    assert empty >= 10


def test_all_13_types_covered():
    cases = _load_holdout()["cases"]
    covered = {change["change_type"] for case in cases for change in case["changes"]}
    assert covered == ALL_13_TYPES


def test_sentence_body_and_multi_candidate_requirements():
    cases = _load_holdout()["cases"]
    three_to_six = [
        case["case_id"]
        for case in cases
        if 3 <= len([p for p in re.split(r"[。！？!?]", case["text"]) if p.strip()]) <= 6
    ]
    assert len(three_to_six) >= 12
    multi = [case["case_id"] for case in cases if len(case["changes"]) >= 2]
    assert len(multi) >= 6


def test_ledger_not_consumed_or_exactly_once():
    if RESULT_PATH.exists():
        ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        assert int(ledger["attempt_count_total"]) == 1
    else:
        if LEDGER_PATH.exists():
            ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
            assert int(ledger["attempt_count_total"]) < 1
        assert not PREFLIGHT_PATH.exists() or json.loads(
            PREFLIGHT_PATH.read_text(encoding="utf-8")
        )["status"] != "ready"


def test_runner_import_does_not_execute():
    preflight_existed_before = PREFLIGHT_PATH.exists()
    module = importlib.import_module(
        "experiments.world_runtime_writer_canary.sealed_holdout_wr2b"
    )
    assert hasattr(module, "run")
    # importing the runner must not execute it (no preflight/ledger written by import)
    if not preflight_existed_before:
        assert not PREFLIGHT_PATH.exists()
        assert not LEDGER_PATH.exists()
