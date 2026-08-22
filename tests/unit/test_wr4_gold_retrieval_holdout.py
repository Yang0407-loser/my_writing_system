"""Integrity tests for the WR4 sealed unseen holdout (v1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "experiments" / "world_runtime_writer_canary" / "fixtures"
FIXTURE = FIXTURES / "wr4_gold_retrieval_holdout_v1.json"
SNAPSHOT = FIXTURES / "gold_retrieval_corpus_snapshot_v1.json"
MANIFEST = FIXTURES / "wr4_gold_retrieval_holdout_v1.freeze_manifest.json"
TRAINING = FIXTURES / "wr4_gold_retrieval_v1.json"
RUNTIME = ROOT / ".world_runtime_wr4_sealed_holdout_runtime"
SEALED = RUNTIME / "private" / "sealed-holdout-v1.json"
LOCK = RUNTIME / "holdout-lock.json"
LOCKED_MANIFEST = RUNTIME / "private" / "locked-manifest.json"
EVALUATION = RUNTIME / "evaluation.json"
TASK_ID = "3a4e561a-2d5d-4679-9da0-892a8a2b52e3"


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _corpus_digest(rows: list[dict]) -> str:
    ordered = sorted(
        rows,
        key=lambda row: (
            int(row["section"]),
            int(row["subsection"]),
            str(row["title"]),
            str(row["content_hash"]),
        ),
    )
    hashes = [row["content_hash"] for row in ordered]
    return _sha256_text(json.dumps(hashes, ensure_ascii=False))


def test_fixture_and_manifest_exist() -> None:
    assert FIXTURE.is_file()
    assert SNAPSHOT.is_file()
    assert MANIFEST.is_file()


def test_fixture_schema_and_tier_counts() -> None:
    fixture = _load(FIXTURE)
    assert fixture["schema_version"] == "wr4-gold-retrieval-holdout-v1"
    assert fixture["corpus"]["task_id"] == TASK_ID
    assert len(fixture["entries"]) == 20
    story = [entry for entry in fixture["entries"] if entry["tier"] == "story_fact"]
    wr = [entry for entry in fixture["entries"] if entry["tier"] == "wr_key_evidence"]
    assert len(story) == 12
    assert len(wr) == 8
    assert all(entry["wr_keys"] for entry in wr)


def test_gold_sections_are_strictly_prior_context() -> None:
    fixture = _load(FIXTURE)
    for entry in fixture["entries"]:
        current = int(entry["section"])
        assert all(int(section) < current for section in entry["gold_sections"]), (
            entry["query_index"]
        )


def test_fixture_corpus_hash_matches_snapshot() -> None:
    fixture = _load(FIXTURE)
    snapshot = _load(SNAPSHOT)
    rows = snapshot["tasks"][TASK_ID]["rows"]
    expected = _corpus_digest(rows)
    assert expected == fixture["corpus"]["corpus_hash"]
    assert len(rows) == fixture["corpus"]["chunk_count"]


def test_gold_hashes_exist_in_snapshot() -> None:
    fixture = _load(FIXTURE)
    snapshot = _load(SNAPSHOT)
    rows = snapshot["tasks"][TASK_ID]["rows"]
    known = {row["content_hash"] for row in rows}
    for entry in fixture["entries"]:
        assert set(entry["gold_anchor_hashes"]) <= known, entry["query_index"]
        assert set(entry["gold_chunk_hashes"]) <= known, entry["query_index"]
        assert set(entry["gold_anchor_hashes"]) <= set(entry["gold_chunk_hashes"]), (
            entry["query_index"]
        )


def test_evidence_spans_are_verbatim() -> None:
    fixture = _load(FIXTURE)
    snapshot = _load(SNAPSHOT)
    by_hash = {
        row["content_hash"]: row
        for row in snapshot["tasks"][TASK_ID]["rows"]
    }
    for entry in fixture["entries"]:
        for fact in entry["must_recall_facts"]:
            spans = entry["fact_evidence"].get(fact, [])
            assert spans, (entry["query_index"], fact)
            for span in spans:
                row = by_hash[span["chunk_hash"]]
                phrase = span["phrase"]
                start = span["start"]
                end = span["end"]
                assert row["text"][start:end] == phrase, (
                    entry["query_index"],
                    phrase,
                )
                assert span["excerpt"] == row["text"][
                    max(0, start - 24): start + len(phrase) + 24
                ]


def test_manifest_binds_fixture_hash() -> None:
    fixture = _load(FIXTURE)
    manifest = _load(MANIFEST)
    assert manifest["fixture"] == FIXTURE.name
    assert manifest["fixture_sha256"] == _sha256_text(
        FIXTURE.read_text(encoding="utf-8")
    )
    assert manifest["entry_count"] == len(fixture["entries"])
    assert manifest["llm_calls"] == 0
    assert manifest["production_authorized"] is False


def test_sealed_runtime_is_bound() -> None:
    assert SEALED.is_file()
    assert LOCK.is_file()
    assert LOCKED_MANIFEST.is_file()
    fixture = _load(FIXTURE)
    lock = _load(LOCK)
    assert lock["sealed_sha256"] == _sha256_text(
        SEALED.read_text(encoding="utf-8")
    )
    assert lock["sealed_sha256"] == _sha256_text(
        FIXTURE.read_text(encoding="utf-8")
    )
    locked = _load(LOCKED_MANIFEST)
    assert locked["sealed_sha256"] == lock["sealed_sha256"]
    assert locked["sample_count"] == len(fixture["entries"])


def test_no_gold_leakage_from_v1_training() -> None:
    fixture = _load(FIXTURE)
    training = _load(TRAINING)
    assert training["corpus"]["task_id"] != TASK_ID
    train_queries = {entry["query"] for entry in training["entries"]}
    train_phrases = {
        str(span.get("phrase", ""))
        for entry in training["entries"]
        for spans in entry["fact_evidence"].values()
        for span in spans
    }
    for entry in fixture["entries"]:
        assert entry["query"] not in train_queries, entry["query_index"]
        for spans in entry["fact_evidence"].values():
            for span in spans:
                assert str(span.get("phrase", "")) not in train_phrases, (
                    entry["query_index"],
                    span.get("phrase"),
                )


def test_evaluation_exists_and_is_sealed_bound() -> None:
    assert EVALUATION.is_file()
    evaluation = _load(EVALUATION)
    lock = _load(LOCK)
    assert evaluation["sealed_sha256"] == lock["sealed_sha256"]
    assert evaluation["profile"]["llm_calls"] == 0
    assert evaluation["profile"]["chroma_writes"] == 0
    assert evaluation["profile"]["production_switched"] is False
    assert evaluation["sealed_holdout_gate_passed"] is False
    assert evaluation["decision"] == "wr4_holdout_failed_no_rerun"
