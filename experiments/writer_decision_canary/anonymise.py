from __future__ import annotations

import hashlib
import random
from typing import Any

from .review import build_review_template


def _paragraphs(text: str) -> list[dict[str, str]]:
    rows = [row.strip() for row in text.split("\n\n") if row.strip()]
    return [{"paragraph_id": f"P{i:02d}", "text": row} for i, row in enumerate(rows, 1)]


def anonymise(results: list[dict[str, Any]], seed: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    rng = random.Random(seed)
    shuffled = list(results)
    rng.shuffle(shuffled)
    mapping: dict[str, Any] = {}
    public_texts = []
    for index, row in enumerate(shuffled, 1):
        text_id = f"T{index:02d}"
        mapping[text_id] = {
            "arm": row["arm"], "repeat": row["repeat"],
            "result_path": row.get("result_path"),
            "ticket_hash": row.get("consumed_ticket_hash"),
        }
        public_texts.append({"text_id": text_id, "paragraphs": _paragraphs(row["text"])})
    pairs = []
    for repeat in (1, 2):
        ids = [key for key, value in mapping.items() if value["repeat"] == repeat]
        rng.shuffle(ids)
        pairs.append({"pair_id": f"PAIR-{repeat}", "text_1": ids[0], "text_2": ids[1]})
    public = {
        "schema_version": "1.0",
        "scene_contract": {
            "setting": "夜间旧书店修复工坊停电，备用电力只能保护一件物品。",
            "required_outcome": "优先保护顾客相册；目录只做临时处理；长期方案留待天亮。",
            "characters": ["许栀", "沈闻"],
        },
        "texts": public_texts,
        "pairs": pairs,
        "witness_definitions": [
            "process_log", "direct_explanation", "abstract_emotion",
            "event_overengineering", "logistics_dialogue",
        ],
    }
    digest = hashlib.sha256(str(seed).encode()).hexdigest()
    private = {"schema_version": "1.0", "shuffle_seed_sha256": digest, "mapping": mapping}
    return public, private, build_review_template(public)

