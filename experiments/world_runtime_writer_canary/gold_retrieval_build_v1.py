"""Build the WR4 offline gold-retrieval evaluation set (v1).

Purpose
-------
Freeze a hash-bound, chunk-level gold set for the fixed RAG corpus
(task 07d1391e, 149 chunks).  The set has two tiers:

- Tier A: the existing 10 author-labeled queries
  (``tests/rag_annotation_07d1391e.json``), re-bound to corpus chunks and
  given deterministic fact-evidence phrases.
- Tier B: 8 WR-only key queries derived from the WR3.9 key-level semantic
  matrix, each bound to exact corpus text evidence (chunk hash + spans).

The builder is deterministic and offline: it only reads the frozen corpus
snapshot and the existing annotation, verifies every evidence phrase exists in
the claimed chunks, then writes the fixture and a freeze manifest.  No LLM
calls, no Chroma writes, no production imports.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORPUS_SNAPSHOT = FIXTURES / "gold_retrieval_corpus_snapshot_v1.json"
LEGACY_ANNOTATION = ROOT / "tests" / "rag_annotation_07d1391e.json"
OUTPUT_FIXTURE = FIXTURES / "wr4_gold_retrieval_v1.json"
MANIFEST = FIXTURES / "wr4_gold_retrieval_v1.freeze_manifest.json"

TASK_ID = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"
SCHEMA_VERSION = "wr4-gold-retrieval-v1"
BUILD_VERSION = "wr4-gold-retrieval-builder-v1"

CHARACTER_NAMES = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    task = snapshot["tasks"][TASK_ID]
    return task["rows"]


def rows_by_key(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = f"S{row['section']}:{row['title']}"
        by_key.setdefault(key, []).append(row)
    return by_key


def rows_by_section(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    by_section: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_section.setdefault(int(row["section"]), []).append(row)
    return by_section


def spans_for(phrase: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every span of ``phrase`` inside ``rows``."""
    spans = []
    for row in rows:
        text = row["text"]
        start = text.find(phrase)
        while start != -1:
            spans.append(
                {
                    "phrase": phrase,
                    "chunk_hash": row["content_hash"],
                    "section": row["section"],
                    "subsection": row["subsection"],
                    "title": row["title"],
                    "start": start,
                    "end": start + len(phrase),
                    "excerpt": text[max(0, start - 24): start + len(phrase) + 24],
                }
            )
            start = text.find(phrase, start + 1)
    return spans


# ---------------------------------------------------------------------------
# Tier B specification: WR-only key queries with evidence phrases.
#
# wr_keys follow the WR3.9 key-level semantic matrix triple shape
# (entity_group, subject, predicate) where known; otherwise the closest
# key-level tuple for this corpus.  Phrases must appear verbatim in the listed
# gold sections; the builder fails closed otherwise.
# ---------------------------------------------------------------------------

TIER_B_SPECS: list[dict[str, Any]] = [
    {
        "query_index": "W1",
        "query_intent": ["event", "scene", "character"],
        "section": 4,
        "subsection": 2,
        "query": (
            "野面包店的营业安排：是不是只有周六才开门？周一到周五周野也做面包吗？"
            "天然酵母要养几天，和只开周六有什么关系"
        ),
        "wr_keys": [
            ["continuity_state", "bakery:wild-bread", "open_days"],
            ["world_clock", "weekday", ""],
        ],
        "gold_sections": [3, 4],
        "gold_chunks": ["S3:暗夜蹲守", "S3:暖黄初现", "S4:破晓等候"],
        "must_recall_facts": [
            "野面包店只有周六开门，周一到周五做面包但不卖",
            "天然酵母需要养五天，周六才够活性",
        ],
        "fact_evidence": {
            "野面包店只有周六开门，周一到周五做面包但不卖": [
                "周六才开门",
                "周一到周五也做，只是不卖",
                "周六才给别人",
            ],
            "天然酵母需要养五天，周六才够活性": [
                "天然酵母得养五天",
                "周六才够活性",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "W2",
        "query_intent": ["event", "scene"],
        "section": 4,
        "subsection": 1,
        "query": (
            "周野每天凌晨的时间安排：几点开始揉面、几点开窗通风、几点整形、"
            "几点进烤箱？卷帘门几点拉开"
        ),
        "wr_keys": [
            ["world_clock", "time", ""],
            ["continuity_state", "bakery:wild-bread", "storefront_open_time"],
        ],
        "gold_sections": [2, 3],
        "gold_chunks": ["S2:周六蹲守", "S3:暗夜蹲守"],
        "must_recall_facts": [
            "周野每天凌晨三点半揉面，四点半开窗通风，五点整形，六点进烤箱",
            "周六凌晨三点三十分整卷帘门拉开",
        ],
        "fact_evidence": {
            "周野每天凌晨三点半揉面，四点半开窗通风，五点整形，六点进烤箱": [
                "三点半揉面，四点半开窗通风，五点整形，六点进烤箱",
            ],
            "周六凌晨三点三十分整卷帘门拉开": [
                "三点三十分整",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "W3",
        "query_intent": ["event", "scene"],
        "section": 7,
        "subsection": 1,
        "query": (
            "当前时间点面包店的营业状态：卷帘门是拉着还是开着？门口有没有告示？"
            "店里是否还在正常营业"
        ),
        "wr_keys": [
            ["continuity_state", "bakery:wild-bread", "operation_state"],
        ],
        "gold_sections": [5, 6, 7, 14],
        "gold_chunks": ["S5:沉默的重量", "S6:面包无声", "S14:围堵与告示"],
        "must_recall_facts": [
            "卷帘门拉下后操作间只剩压缩机低鸣，柜台上有'面包是给人吃的'限购告示",
            "围堵事件后店门口卷帘门拉着，告示贴在玻璃上",
        ],
        "fact_evidence": {
            "卷帘门拉下后操作间只剩压缩机低鸣，柜台上有'面包是给人吃的'限购告示": [
                "卷帘门拉下后，操作间只剩压缩机低鸣",
                "面包是给人吃的，不是给人拍的",
            ],
            "围堵事件后店门口卷帘门拉着，告示贴在玻璃上": [
                "店门口卷帘门拉着，告示还贴在玻璃上",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "W4",
        "query_intent": ["character", "scene"],
        "section": 5,
        "subsection": 3,
        "query": (
            "操作间是否可以进入？外人能不能进操作间；操作间的灯光/环境状态"
        ),
        "wr_keys": [
            ["location_state", "bakery:wild-bread:kitchen", "access_state"],
            ["location_state", "bakery:wild-bread:kitchen", "light"],
        ],
        "gold_sections": [2, 4, 5, 6],
        "gold_chunks": ["S2:周六蹲守", "S4:破晓等候", "S5:沉默的重量", "S6:面包无声"],
        "must_recall_facts": [
            "周野只主动邀请特定的人进操作间，操作间平时不对外开放",
            "操作间灯光/环境：日光灯跳两下后暖黄的光漫上人行道",
        ],
        "fact_evidence": {
            "周野只主动邀请特定的人进操作间，操作间平时不对外开放": [
                "周野从操作间出来，递过一块面包",
                "她站起来，走到操作间门口",
            ],
            "操作间灯光/环境：日光灯跳两下后暖黄的光漫上人行道": [
                "日光灯跳了两下，暖黄的光漫上人行道",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "W5",
        "query_intent": ["character", "event"],
        "section": 7,
        "subsection": 3,
        "query": (
            "角色知情状态：顾衍知不知道林晚是删帖的博主？季晴知不知道林晚删帖？"
            "周野是否已经知道林晚在拍他"
        ),
        "wr_keys": [
            ["character_state", "character:gu-yan", "article_knowledge"],
            ["character_state", "character:ji-qing", "article_knowledge"],
            ["character_state", "character:zhou-ye", "photograph_knowledge"],
        ],
        "gold_sections": [3, 7, 14],
        "gold_chunks": ["S3:直面晨光", "S7:面粉里的温度", "S14:围堵与告示"],
        "must_recall_facts": [
            "顾衍认出林晚是删帖的博主并聊起周野往事",
            "周野发现林晚在拍他",
        ],
        "fact_evidence": {
            "顾衍认出林晚是删帖的博主并聊起周野往事": [
                "他母亲教的",
                "他改做面包",
            ],
            "周野发现林晚在拍他": [
                "你在拍我。",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "W6",
        "query_intent": ["character", "scene"],
        "section": 2,
        "subsection": 3,
        "query": "周野在哪里揉面？林晚住在哪里？两人的空间位置关系",
        "wr_keys": [
            ["character_state", "character:zhou-ye", "location"],
            ["character_state", "character:lin-wan", "location"],
        ],
        "gold_sections": [1, 2, 4],
        "gold_chunks": ["S1:第一卷", "S2:周六蹲守", "S4:破晓等候"],
        "must_recall_facts": [
            "周野在野面包店操作间揉面",
            "林晚住在面包店附近合租房",
        ],
        "fact_evidence": {
            "周野在野面包店操作间揉面": [
                "转身进了操作间",
                "操作间不大。周野舀面粉",
            ],
            "林晚住在面包店附近合租房": [
                "林晚在合租房的隔断墙这边翻了个身",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "W7",
        "query_intent": ["character", "event"],
        "section": 5,
        "subsection": 3,
        "query": (
            "周野的职业背景与转行原因：以前做什么？住院是怎么回事？"
            "林晚当前是否处于离职状态"
        ),
        "wr_keys": [
            ["character_state", "character:zhou-ye", "employment"],
            ["character_state", "company:lin-wan", "resignation_acknowledged"],
        ],
        "gold_sections": [1, 2, 4, 5],
        "gold_chunks": ["S1:第一卷", "S2:周三夜归", "S4:破晓等候", "S5:沉默的重量"],
        "must_recall_facts": [
            "周野以前是建筑师，住院三个月后出院不再画图",
            "林晚裸辞，正在重启生活记录",
        ],
        "fact_evidence": {
            "周野以前是建筑师，住院三个月后出院不再画图": [
                "你以前是建筑师",
                "住院三个月，出院就不画图了",
            ],
            "林晚裸辞，正在重启生活记录": [
                "裸辞第四十三",
                "二十版文案",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "W8",
        "query_intent": ["character"],
        "section": 17,
        "subsection": 3,
        "query": "林晚的职业身份与状态变化：从被工作吞噬到辞职记录生活，现在的身份",
        "wr_keys": [
            ["character_state", "employment:lin-wan", "status"],
        ],
        "gold_sections": [1, 2, 17],
        "gold_chunks": ["S1:第一卷", "S2:周三夜归", "S17:面包婚礼"],
        "must_recall_facts": [
            "林晚曾因工作被吞噬，二十版文案只换来一张小黑板照片",
            "林晚辞职后不后悔，身份转为记录者/写作者",
        ],
        "fact_evidence": {
            "林晚曾因工作被吞噬，二十版文案只换来一张小黑板照片": [
                "被工作吞了",
                "二十版文案",
            ],
            "林晚辞职后不后悔，身份转为记录者/写作者": [
                "辞职那天VP问我后不后悔，我说不后悔",
            ],
        },
        "requires_causal_retrieval": True,
    },
]

# Gold corrections applied to Tier B before sealing (recorded, not silent).
# W6: the evidence for "周野在操作间揉面" lives in the current subsection
# (S2.3) and a future section (S4.1); the retrieval contract excludes both.
# Corrected to prior-context gold [1] with verbatim S1.1 evidence.
TIER_B_OVERRIDES: dict[str, dict[str, Any]] = {
    "W6": {
        "reason": (
            "evidence for '周野在操作间' was in current subsection S2.3 and "
            "future S4.1, which the retrieval contract excludes; corrected to "
            "prior-context gold section 1 with verbatim S1.1 evidence"
        ),
        "gold_sections": [1],
        "gold_chunks": ["S1:第一卷"],
        "must_recall_facts": [
            "周野在野面包店做面包",
            "林晚住在面包店附近合租房",
        ],
        "fact_evidence": {
            "周野在野面包店做面包": ["周野把发酵好的面团取出来"],
            "林晚住在面包店附近合租房": [
                "林晚在合租房的隔断墙这边翻了个身"
            ],
        },
    }
}


# ---------------------------------------------------------------------------
# Tier A evidence: keyed by query_index -> fact index -> phrase list.
# Phrases are verified against chunks in the entry's gold sections.
# ---------------------------------------------------------------------------

TIER_A_EVIDENCE: dict[int, list[list[str]]] = {
    1: [
        ["墙上贴满了她拍的照片", "按时间线"],
        ["这不是偷窥，是看见"],
    ],
    2: [
        ["点赞破万", "十几个人举着手机"],
        ["那篇图文。彻底删了", "你教，我学"],
    ],
    3: [
        ["被工作吞了", "合租房", "二十版文案"],
        ["小黑板", "100个生活切片"],
    ],
    4: [
        ["三十万", "季晴", "吴阿姨"],
        ["第七周周六", "所有人都在"],
    ],
    5: [
        ["他揉面时总哼一首童谣", "他母亲教的"],
        ["童谣", "母亲"],
        ["从取景器后面走出来"],
    ],
    6: [
        ["评论区叠了两百层"],
        ["下次别删了。拍就拍吧"],
        ["面包书"],
    ],
    7: [
        ["补光灯的白光", "摆姿势"],
        ["我小红书发的那篇"],
        ["那篇图文。彻底删了"],
    ],
    8: [
        ["咸是慢慢渗出来的", "咸味藏在麦香后面"],
        ["咸味藏在麦香后面", "专注"],
        ["边界在取景框里"],
    ],
    9: [
        ["天然酵母得养五天"],
        ["你以前是建筑师", "后来住了三个月院"],
        ["外壳", "软"],
    ],
    10: [
        ["那袋吐司", "白吐司"],
        ["洋玩意儿"],
        ["做馒头"],
    ],
}

# Evidence-based corrections to the legacy annotation's gold_sections.
# The legacy gold for these queries omitted the section that actually contains
# the must-recall fact evidence (verified verbatim in the corpus snapshot).
GOLD_SECTION_OVERRIDES: dict[int, list[int]] = {
    5: [4, 5, 6, 7],
    6: [4, 5, 6, 7],
}


def build_entry(
    *,
    rows: list[dict[str, Any]],
    by_key: dict[str, list[dict[str, Any]]],
    by_section: dict[int, list[dict[str, Any]]],
    legacy: dict[str, Any],
    tier: str,
    evidence: list[list[str]],
) -> dict[str, Any]:
    query_index = int(legacy["query_index"])
    override = GOLD_SECTION_OVERRIDES.get(query_index)
    gold_sections = (
        list(override)
        if override is not None
        else [int(section) for section in legacy["gold_sections"]]
    )
    gold_chunk_keys = [str(key) for key in legacy.get("gold_chunks", [])]
    anchor_rows: list[dict[str, Any]] = []
    for key in gold_chunk_keys:
        matched = by_key.get(key, [])
        if not matched:
            raise ValueError(f"query {query_index}: gold chunk key not found: {key}")
        anchor_rows.extend(matched)
    gold_rows = [row for section in gold_sections for row in by_section.get(section, [])]

    fact_evidence: dict[str, list[dict[str, Any]]] = {}
    facts = [str(fact) for fact in legacy["must_recall_facts"]]
    if len(evidence) != len(facts):
        raise ValueError(
            f"query {query_index}: evidence list length {len(evidence)} != "
            f"facts length {len(facts)}"
        )
    for fact, phrases in zip(facts, evidence):
        spans: list[dict[str, Any]] = []
        for phrase in phrases:
            spans.extend(spans_for(phrase, gold_rows))
        if not spans:
            raise ValueError(
                f"query {query_index}: no evidence found for fact: {fact} "
                f"(phrases={phrases})"
            )
        fact_evidence[fact] = spans

    entry: dict[str, Any] = {
        "query_index": f"T{query_index}",
        "tier": tier,
        "source_query_index": query_index,
        "source": "tests/rag_annotation_07d1391e.json",
        "query_intent": [str(intent) for intent in legacy["query_intent"]],
        "section": int(legacy["section"]),
        "subsection": int(legacy["subsection"]),
        "query": str(legacy["query"]),
        "gold_sections": gold_sections,
        "gold_sections_source": (
            "evidence_corrected_override"
            if override is not None
            else "legacy_annotation"
        ),
        "gold_chunk_keys": gold_chunk_keys,
        "gold_anchor_hashes": sorted({row["content_hash"] for row in anchor_rows}),
        "gold_chunk_hashes": sorted({row["content_hash"] for row in gold_rows}),
        "must_recall_facts": facts,
        "fact_evidence": fact_evidence,
        "requires_causal_retrieval": bool(legacy["requires_causal_retrieval"]),
        "gold_anchor_exhaustive": False,
        "gold_sections_exhaustive": False,
    }
    return entry


def build_tier_b_entry(
    *,
    rows: list[dict[str, Any]],
    by_key: dict[str, list[dict[str, Any]]],
    by_section: dict[int, list[dict[str, Any]]],
    spec: dict[str, Any],
) -> dict[str, Any]:
    override = TIER_B_OVERRIDES.get(spec["query_index"])
    gold_sections = (
        [int(section) for section in override["gold_sections"]]
        if override is not None
        else [int(section) for section in spec["gold_sections"]]
    )
    gold_chunk_keys = (
        [str(key) for key in override["gold_chunks"]]
        if override is not None
        else [str(key) for key in spec["gold_chunks"]]
    )
    anchor_rows: list[dict[str, Any]] = []
    for key in gold_chunk_keys:
        matched = by_key.get(key, [])
        if not matched:
            raise ValueError(
                f"{spec['query_index']}: gold chunk key not found: {key}"
            )
        anchor_rows.extend(matched)
    gold_rows = [row for section in gold_sections for row in by_section.get(section, [])]

    facts = (
        [str(fact) for fact in override["must_recall_facts"]]
        if override is not None
        else [str(fact) for fact in spec["must_recall_facts"]]
    )
    evidence_map = (
        dict(override["fact_evidence"])
        if override is not None
        else dict(spec["fact_evidence"])
    )
    if set(evidence_map) != set(facts):
        raise ValueError(
            f"{spec['query_index']}: evidence keys must equal fact keys"
        )
    fact_evidence: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        spans: list[dict[str, Any]] = []
        for phrase in evidence_map[fact]:
            spans.extend(spans_for(phrase, gold_rows))
        if not spans:
            raise ValueError(
                f"{spec['query_index']}: no evidence found for fact: {fact} "
                f"(phrases={evidence_map[fact]})"
            )
        fact_evidence[fact] = spans

    entry = {
        "query_index": spec["query_index"],
        "tier": "wr_key_evidence",
        "source_query_index": None,
        "source": "wr39_key_level_semantic_matrix + corpus evidence (builder v1)",
        "query_intent": [str(intent) for intent in spec["query_intent"]],
        "section": int(spec["section"]),
        "subsection": int(spec["subsection"]),
        "query": str(spec["query"]),
        "wr_keys": [list(key) for key in spec["wr_keys"]],
        "gold_sections": gold_sections,
        "gold_chunk_keys": gold_chunk_keys,
        "gold_anchor_hashes": sorted({row["content_hash"] for row in anchor_rows}),
        "gold_chunk_hashes": sorted({row["content_hash"] for row in gold_rows}),
        "must_recall_facts": facts,
        "fact_evidence": fact_evidence,
        "requires_causal_retrieval": bool(spec["requires_causal_retrieval"]),
        "gold_anchor_exhaustive": True,
        "gold_sections_exhaustive": False,
    }
    if override is not None:
        entry["gold_override_reason"] = override["reason"]
        entry["gold_sections_source"] = "evidence_corrected_override"
    return entry


def main() -> None:
    snapshot = _load_json(CORPUS_SNAPSHOT)
    annotation = _load_json(LEGACY_ANNOTATION)
    if annotation.get("task_id") != TASK_ID:
        raise ValueError("legacy annotation task_id does not match corpus task")
    if snapshot["schema_version"] != "wr4-corpus-snapshot-v1":
        raise ValueError("unexpected corpus snapshot schema")

    task = snapshot["tasks"][TASK_ID]
    rows = task["rows"]
    by_key = rows_by_key(rows)
    by_section = rows_by_section(rows)

    entries: list[dict[str, Any]] = []
    for legacy in annotation["entries"]:
        evidence = TIER_A_EVIDENCE[int(legacy["query_index"])]
        entries.append(
            build_entry(
                rows=rows,
                by_key=by_key,
                by_section=by_section,
                legacy=legacy,
                tier="legacy_author_labeled",
                evidence=evidence,
            )
        )
    for spec in TIER_B_SPECS:
        entries.append(
            build_tier_b_entry(
                rows=rows,
                by_key=by_key,
                by_section=by_section,
                spec=spec,
            )
        )
    entries.sort(key=lambda entry: entry["query_index"])

    fixture = {
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILD_VERSION,
        "built_at": "2026-08-07",
        "k": int(annotation.get("k", 5)),
        "corpus": {
            "task_id": TASK_ID,
            "snapshot_file": CORPUS_SNAPSHOT.name,
            "snapshot_sha256": _sha256_file(CORPUS_SNAPSHOT),
            "corpus_hash": task["corpus_hash"],
            "chunk_count": task["chunk_count"],
        },
        "character_names": list(CHARACTER_NAMES),
        "amendments": [
            {
                "query_index": "W6",
                "type": "gold_correction",
                "reason": TIER_B_OVERRIDES["W6"]["reason"],
            }
        ],
        "tiers": {
            "legacy_author_labeled": 10,
            "wr_key_evidence": len(TIER_B_SPECS),
        },
        "entries": entries,
    }
    rendered = json.dumps(fixture, ensure_ascii=False, indent=2)
    OUTPUT_FIXTURE.write_text(rendered, encoding="utf-8")
    manifest = {
        "schema_version": "wr4-gold-retrieval-freeze-manifest-v1",
        "fixture": OUTPUT_FIXTURE.name,
        "fixture_sha256": _sha256_text(rendered),
        "corpus_snapshot": CORPUS_SNAPSHOT.name,
        "corpus_snapshot_sha256": _sha256_file(CORPUS_SNAPSHOT),
        "legacy_annotation": LEGACY_ANNOTATION.name,
        "legacy_annotation_sha256": _sha256_file(LEGACY_ANNOTATION),
        "entry_count": len(entries),
        "tier_a_count": 10,
        "tier_b_count": len(TIER_B_SPECS),
        "llm_calls": 0,
        "production_authorized": False,
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "fixture": str(OUTPUT_FIXTURE),
                "fixture_sha256": manifest["fixture_sha256"],
                "entries": len(entries),
                "tier_a": 10,
                "tier_b": len(TIER_B_SPECS),
                "corpus_hash": task["corpus_hash"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
