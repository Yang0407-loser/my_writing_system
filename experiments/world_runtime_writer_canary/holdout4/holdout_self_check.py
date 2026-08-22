# -*- coding: utf-8 -*-
"""holdout4 gold self-check: schema, hashes, verbatim spans, prior-context,
writing-point coverage, canonical query_intent, zero overlap vs holdout3, seal."""
import hashlib
import json
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
GOLD_PATH = os.path.join(DIR, "wr4_metadata_holdout_gold_v1.json")
SEAL_PATH = os.path.join(DIR, "wr4_metadata_holdout_gold_v1.seal.json")
SNAPSHOT = os.path.join(
    DIR, "..", "..", "..",
    "experiments", "world_runtime_writer_canary",
    "fixtures", "wr4_metadata_holdout_corpus_snapshot_v1.json",
)
HOLD3_GOLD = os.path.join(DIR, "..", "holdout3", "wr4_metadata_holdout_gold_v1.json")
TASK_ID = "20f02dc7-dc64-4233-bd6c-06a6d8647dbe"
EXPECTED_SNAPSHOT_SHA = "6e52a0f738fb89f0b43fdc8207b63abd4e052f9694b4747c560b7b3b8b8a309a"
EXPECTED_CORPUS_HASH = "05420ab03eb3ec11ac42f07b334ca434ba6a79b9250cbec505f2857b99317878"
CANONICAL_INTENTS = {"character", "event", "foreshadowing", "scene"}

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


with open(SNAPSHOT, "rb") as f:
    snapshot_raw = f.read()
snapshot_sha = hashlib.sha256(snapshot_raw).hexdigest()
check("snapshot file sha256", snapshot_sha == EXPECTED_SNAPSHOT_SHA, snapshot_sha)

snapshot = json.loads(snapshot_raw)
task = snapshot["tasks"][TASK_ID]
rows = task["rows"]
by_hash = {r["content_hash"]: r for r in rows}

with open(GOLD_PATH, "rb") as f:
    gold_raw = f.read()
gold = json.loads(gold_raw)
with open(SEAL_PATH, "rb") as f:
    seal_raw = f.read()
seal = json.loads(seal_raw)
with open(HOLD3_GOLD, "r", encoding="utf-8") as f:
    hold3 = json.load(f)

check("schema_version", gold.get("schema_version") == "wr4-metadata-holdout-gold-v1")
check("author", gold.get("author") == "independent-holdout-author-human-batch4")
check("k=5", gold.get("k") == 5)
check("corpus task_id", gold.get("corpus", {}).get("task_id") == TASK_ID)
check("corpus hash", gold.get("corpus", {}).get("corpus_hash") == EXPECTED_CORPUS_HASH)
check("corpus snapshot sha", gold.get("corpus", {}).get("snapshot_sha256") == EXPECTED_SNAPSHOT_SHA)
check("corpus chunk_count", gold.get("corpus", {}).get("chunk_count") == len(rows), len(rows))
check("character_names", gold.get("character_names") == ["林晚", "周野", "季晴", "顾衍", "吴阿姨"])

entries = gold["entries"]
check("entry count 14", len(entries) == 14, len(entries))
tier_counts = {}
for e in entries:
    tier_counts[e["tier"]] = tier_counts.get(e["tier"], 0) + 1
check("tier counts", tier_counts == {"continuity_fact": 8, "wr_key_evidence": 6}, tier_counts)
check("query_index J1..J14", [e["query_index"] for e in entries] == [f"J{i}" for i in range(1, 15)])
check("causal >= 2", sum(1 for e in entries if e["requires_causal_retrieval"]) >= 2,
      sum(1 for e in entries if e["requires_causal_retrieval"]))

wps = [(e["section"], e["subsection"]) for e in entries]
early = [w for w in wps if 2 <= w[0] <= 6]
mid = [w for w in wps if 7 <= w[0] <= 13]
late = [w for w in wps if 14 <= w[0] <= 18]
check("writing point coverage 前/中/后", bool(early) and bool(mid) and bool(late),
      f"early={len(early)} mid={len(mid)} late={len(late)}")

section_chunks = {}
for r in rows:
    section_chunks.setdefault(r["section"], []).append(r["content_hash"])

own_queries = set()
own_phrases = set()

for e in entries:
    q = e["query_index"]
    cur = (e["section"], e["subsection"])
    check(f"{q} required fields", all(k in e for k in (
        "query_index", "tier", "query", "query_intent", "section", "subsection",
        "requires_causal_retrieval", "gold_sections", "gold_anchor_hashes",
        "gold_chunk_hashes", "must_recall_facts", "fact_evidence")))
    qi = e.get("query_intent")
    check(f"{q} canonical query_intent",
          isinstance(qi, list) and len(qi) >= 1 and len(set(qi)) == len(qi)
          and all(x in CANONICAL_INTENTS for x in qi), qi)
    check(f"{q} query non-empty", isinstance(e["query"], str) and len(e["query"]) >= 20)
    check(f"{q} 2-3 facts", 2 <= len(e["must_recall_facts"]) <= 3, len(e["must_recall_facts"]))
    check(f"{q} fact_evidence keys match", sorted(e["fact_evidence"].keys()) == sorted(e["must_recall_facts"]))
    own_queries.add(e["query"])
    if e["tier"] == "wr_key_evidence":
        check(f"{q} wr_keys present", isinstance(e.get("wr_keys"), list) and len(e["wr_keys"]) >= 1)
        for wk in e.get("wr_keys", []):
            check(f"{q} wr_key triple", isinstance(wk, list) and len(wk) == 3 and all(isinstance(x, str) for x in wk), wk)
    max_sub = {}
    for fact, spans in e["fact_evidence"].items():
        check(f"{q} fact has spans", isinstance(spans, list) and len(spans) >= 1)
        for sp in spans:
            row = by_hash.get(sp["chunk_hash"])
            check(f"{q} span chunk exists", row is not None, sp["chunk_hash"][:12])
            if row is None:
                continue
            text = row["text"]
            phrase = sp["phrase"]
            own_phrases.add(phrase)
            ok_len = 8 <= len(phrase) <= 80
            ok_newline = "\n" not in phrase
            ok_start = isinstance(sp["start"], int) and isinstance(sp["end"], int) and sp["start"] >= 0
            ok_exact = ok_start and sp["end"] == sp["start"] + len(phrase) and text[sp["start"]:sp["end"]] == phrase
            ok_excerpt = sp.get("excerpt") == text[max(0, sp["start"] - 24):sp["end"] + 24]
            check(f"{q} span verbatim/excerpt", ok_len and ok_newline and ok_exact and ok_excerpt,
                  f"{sp['chunk_hash'][:12]} {phrase[:20]!r}")
            check(f"{q} span prior-context",
                  row["section"] < cur[0] or (row["section"] == cur[0] and row["subsection"] < cur[1]),
                  f"S{row['section']}.{row['subsection']} vs writing S{cur[0]}.{cur[1]}")
            max_sub[row["section"]] = max(max_sub.get(row["section"], -1), row["subsection"])
            check(f"{q} anchor in gold_anchor_hashes", sp["chunk_hash"] in e["gold_anchor_hashes"])
            check(f"{q} anchor in gold_chunk_hashes", sp["chunk_hash"] in e["gold_chunk_hashes"])
    anchor_secs = set()
    for ch in e["gold_anchor_hashes"]:
        row = by_hash.get(ch)
        if row:
            anchor_secs.add(row["section"])
    check(f"{q} gold_sections derived from anchors", set(e["gold_sections"]) == anchor_secs,
          f"gold={e['gold_sections']} anchors={sorted(anchor_secs)}")
    for s in e["gold_sections"]:
        ok = s < cur[0] or (s == cur[0] and max_sub.get(s, -1) < cur[1])
        check(f"{q} gold_section {s} prior", ok)
    expected_chunks = set()
    for s in e["gold_sections"]:
        expected_chunks.update(section_chunks.get(s, []))
    check(f"{q} gold_chunk_hashes complete", set(e["gold_chunk_hashes"]) == expected_chunks,
          f"{len(e['gold_chunk_hashes'])} vs {len(expected_chunks)}")

# Zero overlap vs holdout3
h3_queries = {x["query"] for x in hold3["entries"]}
h3_phrases = {
    sp["phrase"]
    for x in hold3["entries"]
    for spans in x["fact_evidence"].values()
    for sp in spans
}
check("no query overlap vs holdout3", not (own_queries & h3_queries))
check("no phrase overlap vs holdout3", not (own_phrases & h3_phrases),
      f"overlap={len(own_phrases & h3_phrases)}")

# Seal
check("seal schema", seal.get("schema_version") == "wr4-metadata-holdout-seal-v1")
check("seal fixture name", seal.get("fixture") == "wr4_metadata_holdout_gold_v1.json")
check("seal fixture_sha256 = bytes", seal.get("fixture_sha256") == hashlib.sha256(gold_raw).hexdigest(),
      seal.get("fixture_sha256"))
check("seal corpus task", seal.get("corpus_task_id") == TASK_ID)
check("seal corpus hash", seal.get("corpus_hash") == EXPECTED_CORPUS_HASH)
check("seal snapshot sha", seal.get("corpus_snapshot_sha256") == EXPECTED_SNAPSHOT_SHA)
check("seal entry_count", seal.get("entry_count") == 14)
check("seal tier_counts", seal.get("tier_counts") == {"continuity_fact": 8, "wr_key_evidence": 6})
check("seal llm_calls 0", seal.get("llm_calls") == 0)
check("seal production_authorized false", seal.get("production_authorized") is False)
check("seal author", seal.get("author") == "independent-holdout-author-human-batch4")

print()
if failures:
    print(f"FAILED: {len(failures)} checks failed")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
