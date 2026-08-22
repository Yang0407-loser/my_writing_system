from __future__ import annotations

import hashlib
import random
from typing import Any

from .review import template


def _paras(text: str) -> list[dict[str, str]]:
    return [{"paragraph_id": f"P{i:02d}", "text": value.strip()} for i, value in enumerate((x for x in text.split("\n\n") if x.strip()), 1)]


def anonymise(rows: list[dict[str, Any]], seed: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    rng = random.Random(seed)
    shuffled = list(rows); rng.shuffle(shuffled)
    mapping, texts = {}, []
    for i, row in enumerate(shuffled, 1):
        tid = f"T{i:02d}"
        mapping[tid] = {"arm": row["arm"], "repeat": row["repeat"], "result_path": row.get("result_path"), "ticket_hash": row.get("consumed_ticket_hash"), "summary_hash": row.get("consumed_summary_hash")}
        texts.append({"text_id": tid, "paragraphs": _paras(row["text"])})
    pairs = []
    for repeat in (1, 2):
        ids = [k for k, v in mapping.items() if v["repeat"] == repeat]; rng.shuffle(ids)
        pairs.append({"pair_id": f"PAIR-{repeat}", "text_1": ids[0], "text_2": ids[1]})
    public = {
        "schema_version": "1.1",
        "scene_contract": {
            "setting": "闭店后的旧书店阅览室，高窗在暴雨中失效，雨水威胁低层架上的两件物品。",
            "required_outcome": "唯一防水箱优先保护顾客手写日记；书店校样只采用一种临时处置；长期问题留到天亮。",
            "characters": ["许栀", "沈闻"],
        },
        "texts": texts, "pairs": pairs,
    }
    private = {"schema_version": "1.1", "shuffle_seed_sha256": hashlib.sha256(str(seed).encode()).hexdigest(), "mapping": mapping}
    return public, private, template(public)

