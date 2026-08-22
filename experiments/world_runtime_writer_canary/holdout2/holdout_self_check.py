"""Self-check for the procedural holdout gold (batch 2)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parent
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_PATH = OUT_DIR / "wr4_metadata_holdout_gold_v1.json"
SEAL_PATH = OUT_DIR / "wr4_metadata_holdout_gold_v1.seal.json"
SNAPSHOT_PATH = FIXTURES / "wr4_metadata_holdout_corpus_snapshot_v1.json"
TASK_ID = "20f02dc7-dc64-4233-bd6c-06a6d8647dbe"


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    errors: list[str] = []
    fixture = _load(FIXTURE_PATH)
    seal = _load(SEAL_PATH)
    snapshot = _load(SNAPSHOT_PATH)
    task = snapshot["tasks"][TASK_ID]
    rows = task["rows"]
    by_hash = {row["content_hash"]: row for row in rows}

    if seal.get("fixture_sha256") != hashlib.sha256(
        FIXTURE_PATH.read_bytes()
    ).hexdigest():
        errors.append("seal fixture_sha256 does not match fixture raw bytes")
    if fixture.get("schema_version") != "wr4-metadata-holdout-gold-v1":
        errors.append("unexpected schema_version")
    if fixture["corpus"]["corpus_hash"] != task["corpus_hash"]:
        errors.append("corpus hash mismatch")
    if fixture["corpus"]["chunk_count"] != task["chunk_count"]:
        errors.append("chunk count mismatch")
    if seal.get("corpus_snapshot_sha256") != hashlib.sha256(
        SNAPSHOT_PATH.read_bytes()
    ).hexdigest():
        errors.append("snapshot sha256 mismatch")

    entries = fixture["entries"]
    if not 12 <= len(entries) <= 16:
        errors.append(f"entry count {len(entries)} outside 12..16")
    tier_counts = {
        tier: sum(1 for entry in entries if entry["tier"] == tier)
        for tier in ("continuity_fact", "wr_key_evidence")
    }
    if tier_counts["continuity_fact"] < 6 or tier_counts["wr_key_evidence"] < 5:
        errors.append(f"tier counts too small: {tier_counts}")
    if sum(1 for entry in entries if entry.get("requires_causal_retrieval")) < 2:
        errors.append("causal entries < 2")
    if len({entry["query"] for entry in entries}) != len(entries):
        errors.append("duplicate queries")

    for entry in entries:
        query_index = entry["query_index"]
        cur_s = int(entry["section"])
        cur_ss = int(entry["subsection"])
        if set(entry["gold_anchor_hashes"]) - set(by_hash):
            errors.append(f"{query_index}: anchor hash missing")
        if set(entry["gold_chunk_hashes"]) - set(by_hash):
            errors.append(f"{query_index}: chunk hash missing")
        if not entry["must_recall_facts"]:
            errors.append(f"{query_index}: no facts")
        for fact, spans in entry["fact_evidence"].items():
            if not spans:
                errors.append(f"{query_index}: fact without spans")
            for span in spans:
                row = by_hash.get(span["chunk_hash"])
                if row is None:
                    errors.append(f"{query_index}: span chunk missing")
                    continue
                if row["text"][span["start"]:span["end"]] != span["phrase"]:
                    errors.append(f"{query_index}: span not verbatim")
                if span["excerpt"] != row["text"][
                    max(0, span["start"] - 24): span["end"] + 24
                ]:
                    errors.append(f"{query_index}: excerpt mismatch")
        for section in entry["gold_sections"]:
            if section > cur_s:
                errors.append(f"{query_index}: future gold section {section}")
            if section == cur_s:
                anchor_subs = {
                    int(by_hash[hash_]["subsection"])
                    for hash_ in entry["gold_anchor_hashes"]
                    if by_hash[hash_]["section"] == cur_s
                }
                if anchor_subs and max(anchor_subs) >= cur_ss:
                    errors.append(f"{query_index}: same-section evidence not prior")

    if errors:
        print("FAIL")
        for error in errors:
            print(" -", error)
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "entries": len(entries),
                "tier_counts": tier_counts,
                "fixture_sha256": seal["fixture_sha256"],
                "corpus_hash": task["corpus_hash"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
