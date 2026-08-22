"""Procedural blind holdout author (v1) for the metadata retrieval variant.

Deterministic, seeded generator that creates a sealed holdout gold set from
the NEW corpus snapshot only (task 20f02dc7).  It is blind by construction:
pure standard library, fixed seed, no LLM, no Chroma access, and it never
reads the development gold, the previous holdout, or any variant code.

Deliverables (written into holdout2/):
- wr4_metadata_holdout_gold_v1.json
- wr4_metadata_holdout_gold_v1.seal.json   (fixture_sha256 = raw file bytes)
- AUTHOR_REPORT.md
The companion holdout_self_check.py validates the outputs.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "experiments" / "world_runtime_writer_canary" / "fixtures"
OUT_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = FIXTURES / "wr4_metadata_holdout_corpus_snapshot_v1.json"
FIXTURE_PATH = OUT_DIR / "wr4_metadata_holdout_gold_v1.json"
SEAL_PATH = OUT_DIR / "wr4_metadata_holdout_gold_v1.seal.json"
REPORT_PATH = OUT_DIR / "AUTHOR_REPORT.md"

TASK_ID = "20f02dc7-dc64-4233-bd6c-06a6d8647dbe"
SEED = 20260807
AUTHOR = "procedural-author-v1"
CHARACTER_NAMES = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")

SENT_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")

TERM_GROUPS: dict[str, tuple[str, ...]] = {
    "baking": ("揉面", "面团", "面粉", "案板", "烤箱", "发酵"),
    "recording": ("拍", "照片", "相机", "记录", "笔记本", "删帖", "图文"),
    "places": ("面包店", "卷帘门", "操作间", "合租房", "书店"),
    "backstory": ("吐司", "可颂", "菠萝包", "童谣", "母亲", "三十万", "住院"),
}
GROUP_LABELS = {
    "baking": "周野做面包",
    "recording": "林晚的记录与删帖",
    "places": "空间与地点",
    "backstory": "物件与前史",
}

WR_KEY_SPECS: list[dict[str, Any]] = [
    {
        "key": "open_days",
        "terms": ("周六", "周一到周五", "只开", "营业"),
        "topic": "面包店营业日与星期安排",
        "wr_keys": [
            ["continuity_state", "bakery:wild-bread", "open_days"],
            ["world_clock", "weekday", ""],
        ],
        "intent": ["event", "scene"],
        "causal": False,
    },
    {
        "key": "clock",
        "terms": ("三点半", "四点半", "揉面", "进烤箱", "开窗"),
        "topic": "周野凌晨的作息与开店时间",
        "wr_keys": [
            ["world_clock", "time", ""],
            ["continuity_state", "bakery:wild-bread", "storefront_open_time"],
        ],
        "intent": ["event", "scene"],
        "causal": False,
    },
    {
        "key": "operation_state",
        "terms": ("卷帘门", "打烊", "关门", "告示", "限购"),
        "topic": "面包店当前营业状态与门口告示",
        "wr_keys": [
            ["continuity_state", "bakery:wild-bread", "operation_state"],
        ],
        "intent": ["event", "scene"],
        "causal": False,
    },
    {
        "key": "access_light",
        "terms": ("操作间", "门帘", "进来", "日光灯"),
        "topic": "操作间的进出权限与灯光状态",
        "wr_keys": [
            ["location_state", "bakery:wild-bread:kitchen", "access_state"],
            ["location_state", "bakery:wild-bread:kitchen", "light"],
        ],
        "intent": ["character", "scene"],
        "causal": False,
    },
    {
        "key": "knowledge",
        "terms": ("知道", "认出", "告诉", "删帖", "童谣"),
        "topic": "各角色对删帖与周野往事的知情情况",
        "wr_keys": [
            ["character_state", "character:gu-yan", "article_knowledge"],
            ["character_state", "character:ji-qing", "article_knowledge"],
            ["character_state", "character:zhou-ye", "photograph_knowledge"],
        ],
        "intent": ["character", "event"],
        "causal": True,
    },
    {
        "key": "employment",
        "terms": ("建筑师", "住院", "转行", "辞职", "裸辞", "文案"),
        "topic": "周野与林晚的职业背景与现状",
        "wr_keys": [
            ["character_state", "character:zhou-ye", "employment"],
            ["character_state", "employment:lin-wan", "status"],
        ],
        "intent": ["character", "event"],
        "causal": True,
    },
]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sentences(text: str) -> list[str]:
    return [
        match.group(0).strip()
        for match in SENT_RE.finditer(text)
        if 8 <= len(match.group(0).strip()) <= 90
    ]


def span_for(row: dict[str, Any], phrase: str) -> dict[str, Any]:
    start = row["text"].index(phrase)
    return {
        "phrase": phrase,
        "chunk_hash": row["content_hash"],
        "start": start,
        "end": start + len(phrase),
        "excerpt": row["text"][max(0, start - 24): start + len(phrase) + 24],
    }


def pick_evidence(
    rows: list[dict[str, Any]],
    terms: tuple[str, ...],
    rng: random.Random,
    count: int,
    used: set[str],
) -> list[tuple[dict[str, Any], str]]:
    candidates: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        if row["content_hash"] in used:
            continue
        for sentence in sentences(row["text"]):
            if sentence in used:
                continue
            if any(term in sentence for term in terms):
                candidates.append((row, sentence))
    rng.shuffle(candidates)
    picked: list[tuple[dict[str, Any], str]] = []
    seen_hashes: set[str] = set()
    for row, sentence in candidates:
        if len(picked) >= count:
            break
        if row["content_hash"] in seen_hashes:
            continue
        seen_hashes.add(row["content_hash"])
        picked.append((row, sentence))
    if len(picked) < count:
        for row, sentence in candidates:
            if len(picked) >= count:
                break
            if (row["content_hash"], sentence) in [
                (item[0]["content_hash"], item[1]) for item in picked
            ]:
                continue
            picked.append((row, sentence))
    return picked[:count]


def prior_rows(rows: list[dict[str, Any]], section: int, subsection: int) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if int(row["section"]) < section
        or (int(row["section"]) == section and int(row["subsection"]) < subsection)
    ]


def title_for(rows: list[dict[str, Any]], section: int, subsection: int) -> str:
    for row in rows:
        if int(row["section"]) == section and int(row["subsection"]) == subsection:
            return str(row["title"])
    return ""


def build_entry(
    *,
    query_index: str,
    tier: str,
    query: str,
    query_intent: list[str],
    section: int,
    subsection: int,
    wr_keys: list[list[str]] | None,
    facts: list[tuple[dict[str, Any], str]],
    causal: bool,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    fact_evidence: dict[str, list[dict[str, Any]]] = {}
    anchor_hashes: set[str] = set()
    gold_sections: set[int] = set()
    for row, sentence in facts:
        fact_evidence[sentence] = [span_for(row, sentence)]
        anchor_hashes.add(row["content_hash"])
        gold_sections.add(int(row["section"]))
    gold_sections_sorted = sorted(gold_sections)
    gold_chunk_hashes = sorted(
        {
            row["content_hash"]
            for row in rows
            if int(row["section"]) in gold_sections_sorted
        }
    )
    entry: dict[str, Any] = {
        "query_index": query_index,
        "tier": tier,
        "source": "procedural-author-v1 (seed 20260807, corpus snapshot only)",
        "query_intent": query_intent,
        "section": section,
        "subsection": subsection,
        "query": query,
        "gold_sections": gold_sections_sorted,
        "gold_chunk_keys": [],
        "gold_anchor_hashes": sorted(anchor_hashes),
        "gold_chunk_hashes": gold_chunk_hashes,
        "must_recall_facts": [sentence for _, sentence in facts],
        "fact_evidence": fact_evidence,
        "requires_causal_retrieval": causal,
        "gold_anchor_exhaustive": True,
        "gold_sections_exhaustive": False,
        "gold_sections_source": "procedural_prior_context",
    }
    if wr_keys is not None:
        entry["wr_keys"] = wr_keys
    return entry


def main() -> None:
    snapshot = _load_json(SNAPSHOT_PATH)
    task = snapshot["tasks"][TASK_ID]
    rows = sorted(
        task["rows"],
        key=lambda row: (
            int(row["section"]),
            int(row["subsection"]),
            str(row["title"]),
            str(row["content_hash"]),
        ),
    )
    rng = random.Random(SEED)
    entries: list[dict[str, Any]] = []
    used = set()

    continuity_sections = rng.sample(range(4, 18), 8)
    intent_pool = [
        ["event", "character"],
        ["event", "scene"],
        ["character", "scene"],
        ["event", "character", "scene"],
    ]
    for index, section in enumerate(continuity_sections, start=1):
        subsection = rng.randint(1, 3)
        prior = prior_rows(rows, section, subsection)
        groups = rng.sample(list(TERM_GROUPS), k=rng.randint(2, 3))
        facts: list[tuple[dict[str, Any], str]] = []
        for group in groups:
            picked = pick_evidence(prior, TERM_GROUPS[group], rng, 1, used)
            if not picked:
                raise SystemExit(
                    f"no evidence for continuity {index} group {group} at "
                    f"S{section}.{subsection}"
                )
            row, sentence = picked[0]
            used.add(row["content_hash"])
            used.add(sentence)
            facts.append((row, sentence))
        topics = "、".join(GROUP_LABELS[group] for group in groups)
        title = title_for(rows, section, subsection)
        query = (
            f"写第{section}节第{subsection}小节（{title}）时，需要回忆此前"
            f"{topics}相关的情节与人物状态，检索之前章节中与此相关的内容。"
        )
        causal = rng.random() < 0.3
        entries.append(
            build_entry(
                query_index=f"H{index}",
                tier="continuity_fact",
                query=query,
                query_intent=list(rng.choice(intent_pool)),
                section=section,
                subsection=subsection,
                wr_keys=None,
                facts=facts,
                causal=causal,
                rows=rows,
            )
        )

    wr_sections = rng.sample(range(5, 18), len(WR_KEY_SPECS))
    for wr_index, spec in enumerate(WR_KEY_SPECS):
        index = len(entries) + 1
        found = False
        for section in range(wr_sections[wr_index], 19):
            subsection = rng.randint(1, 3)
            prior = prior_rows(rows, section, subsection)
            picked = pick_evidence(prior, spec["terms"], rng, 2, used)
            if len(picked) == 2:
                found = True
                break
        if not found:
            raise SystemExit(f"no evidence for wr key {spec['key']}")
        for row, sentence in picked:
            used.add(row["content_hash"])
            used.add(sentence)
        title = title_for(rows, section, subsection)
        query = (
            f"写第{section}节第{subsection}小节（{title}）时，需要确认此前"
            f"{spec['topic']}相关的事实，检索之前章节中与此相关的内容。"
        )
        entries.append(
            build_entry(
                query_index=f"H{index}",
                tier="wr_key_evidence",
                query=query,
                query_intent=list(spec["intent"]),
                section=section,
                subsection=subsection,
                wr_keys=[list(key) for key in spec["wr_keys"]],
                facts=picked,
                causal=spec["causal"],
                rows=rows,
            )
        )

    entries.sort(key=lambda entry: entry["query_index"])
    queries = [entry["query"] for entry in entries]
    if len(set(queries)) != len(queries):
        raise SystemExit("duplicate queries generated")
    causal_count = sum(1 for entry in entries if entry["requires_causal_retrieval"])
    if causal_count < 2:
        raise SystemExit("causal entries < 2")

    fixture = {
        "schema_version": "wr4-metadata-holdout-gold-v1",
        "author": AUTHOR,
        "author_seed": SEED,
        "blindness": (
            "procedural deterministic generator; reads only the new corpus "
            "snapshot; no LLM, no Chroma, no dev gold, no variant code"
        ),
        "created_at": "2026-08-07",
        "k": 5,
        "corpus": {
            "task_id": TASK_ID,
            "snapshot_file": SNAPSHOT_PATH.name,
            "snapshot_sha256": _sha256_bytes(SNAPSHOT_PATH.read_bytes()),
            "corpus_hash": task["corpus_hash"],
            "chunk_count": task["chunk_count"],
        },
        "character_names": list(CHARACTER_NAMES),
        "entries": entries,
    }
    rendered = json.dumps(fixture, ensure_ascii=False, indent=2)
    FIXTURE_PATH.write_text(rendered, encoding="utf-8")
    fixture_sha = _sha256_bytes(FIXTURE_PATH.read_bytes())
    tier_counts = {
        tier: sum(1 for entry in entries if entry["tier"] == tier)
        for tier in ("continuity_fact", "wr_key_evidence")
    }
    seal = {
        "schema_version": "wr4-metadata-holdout-seal-v1",
        "fixture": FIXTURE_PATH.name,
        "fixture_sha256": fixture_sha,
        "corpus_task_id": TASK_ID,
        "corpus_hash": task["corpus_hash"],
        "corpus_snapshot_sha256": _sha256_bytes(SNAPSHOT_PATH.read_bytes()),
        "entry_count": len(entries),
        "tier_counts": tier_counts,
        "llm_calls": 0,
        "production_authorized": False,
        "author": AUTHOR,
        "seed": SEED,
        "sealed_at": "2026-08-07",
    }
    SEAL_PATH.write_text(
        json.dumps(seal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_lines = [
        "# Procedural Blind Holdout Author — Batch 2",
        "",
        f"- author: {AUTHOR}",
        f"- seed: {SEED}",
        f"- corpus: {TASK_ID} ({task['chunk_count']} chunks, 18 sections)",
        "- blindness: generator reads only the new corpus snapshot; pure "
        "standard library; no LLM, no Chroma, no dev gold, no variant code.",
        "",
        "## 生成规则",
        "",
        "- 14 条 = continuity_fact 8 + wr_key_evidence 6；写作点由种子随机采样，"
        "证据全部来自 prior-context（section < 当前或同 section 更早 subsection）。",
        "- 事实 = 证据 chunk 中命中所属主题词表的一句话（8–90 字），逐字 span 绑定；"
        "查询为模板文本（只含写作点与主题提示，不含答案句）。",
        "- WR 键 6 个语义：open_days/weekday、clock、operation_state、"
        "access/light、knowledge、employment/status；knowledge 与 employment 标记因果检索。",
        "- 局限：金标为机械生成，事实是原文句子而非人工转述；查询为模板风格，"
        "不代表真实 Writer 输入的多样性。",
        "",
        "## 条目",
        "",
        "| query | tier | cur | gold sections | facts | causal |",
        "|---|---|---|---:|---:|---|",
    ]
    for entry in entries:
        report_lines.append(
            f"| {entry['query_index']} | {entry['tier']} | "
            f"{entry['section']}.{entry['subsection']} | "
            f"{','.join(map(str, entry['gold_sections']))} | "
            f"{len(entry['must_recall_facts'])} | "
            f"{entry['requires_causal_retrieval']} |"
        )
    report_lines.append("")
    report_lines.append(
        f"seal fixture_sha256: {fixture_sha}（fixture 文件原始字节）"
    )
    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "fixture": str(FIXTURE_PATH),
                "fixture_sha256": fixture_sha,
                "entries": len(entries),
                "tier_counts": tier_counts,
                "causal_count": causal_count,
                "corpus_hash": task["corpus_hash"],
                "snapshot_sha256": seal["corpus_snapshot_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
