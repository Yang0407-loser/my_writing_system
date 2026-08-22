"""Integrity tests for the WR4 offline gold-retrieval fixture (v1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "experiments" / "world_runtime_writer_canary" / "fixtures"
FIXTURE = FIXTURES / "wr4_gold_retrieval_v1.json"
SNAPSHOT = FIXTURES / "gold_retrieval_corpus_snapshot_v1.json"
MANIFEST = FIXTURES / "wr4_gold_retrieval_v1.freeze_manifest.json"
TASK_ID = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"


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
    assert fixture["schema_version"] == "wr4-gold-retrieval-v1"
    assert fixture["corpus"]["task_id"] == TASK_ID
    assert len(fixture["entries"]) == 18
    tier_a = [entry for entry in fixture["entries"] if entry["tier"] == "legacy_author_labeled"]
    tier_b = [entry for entry in fixture["entries"] if entry["tier"] == "wr_key_evidence"]
    assert len(tier_a) == 10
    assert len(tier_b) == 8
    assert all(entry["wr_keys"] for entry in tier_b)


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


def test_w6_override_is_recorded_and_prior_context() -> None:
    fixture = _load(FIXTURE)
    w6 = next(
        entry for entry in fixture["entries"] if entry["query_index"] == "W6"
    )
    assert w6["gold_sections"] == [1]
    assert w6["gold_sections_source"] == "evidence_corrected_override"
    assert "gold_override_reason" in w6
    assert any(
        amendment["query_index"] == "W6"
        and amendment["type"] == "gold_correction"
        for amendment in fixture.get("amendments", [])
    )
