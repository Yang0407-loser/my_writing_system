from __future__ import annotations

import hashlib
import json
import random
import secrets
from pathlib import Path
from typing import Any

from .builder import DEFAULT_OUTPUT, load_json, write_json


def split_paragraphs(text: str) -> list[dict[str, str]]:
    raw = [part.strip() for part in text.replace("\r\n", "\n").split("\n") if part.strip()]
    if len(raw) == 1:
        sentences = [
            value.strip()
            for value in text.replace("！", "！\n").replace("？", "？\n").replace("。", "。\n").splitlines()
            if value.strip()
        ]
        chunk = max(1, (len(sentences) + 5) // 6)
        raw = ["".join(sentences[index:index + chunk]) for index in range(0, len(sentences), chunk)]
    return [
        {"paragraph_id": f"P{index:02d}", "text": paragraph}
        for index, paragraph in enumerate(raw, 1)
    ]


def build_public_material(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    summary = load_json(output_dir / "run-summary.json")
    if summary["succeeded"] != 12 or summary["failed"] != 0:
        raise ValueError("public blind package requires all 12 texts")
    config = load_json(output_dir / "private/config.locked.json")
    queue = load_json(output_dir / "private/generation-queue.locked.json")
    records = {
        path.stem: load_json(path)
        for path in (output_dir / "private/texts").glob("RC-GEN-*.json")
    }
    if len(records) != 12:
        raise ValueError("expected exactly 12 private text records")
    rng = random.Random(secrets.randbits(128))
    public_ids = [f"Q{index:02d}" for index in range(1, 13)]
    rng.shuffle(public_ids)
    key_entries: list[dict[str, Any]] = []
    by_block: dict[str, list[dict[str, Any]]] = {}
    for item in queue:
        record = records[item["generation_id"]]
        public_id = public_ids.pop()
        key_entries.append(
            {
                "public_text_id": public_id,
                "generation_id": item["generation_id"],
                "block_id": item["block_id"],
                "scene_id": item["scene_id"],
                "repeat": item["repeat"],
                "arm": item["arm"],
                "text_sha256": record["text_sha256"],
            }
        )
        by_block.setdefault(item["block_id"], []).append(
            {
                "public_text_id": public_id,
                "text_sha256": record["text_sha256"],
                "paragraphs": split_paragraphs(record["text"]),
            }
        )
    scene_lookup = {item["scene_id"]: item for item in config["scenes"]}
    public_blocks = []
    pair_key = []
    for block_number, block_id in enumerate(sorted(by_block), 1):
        candidates = by_block[block_id]
        rng.shuffle(candidates)
        entries = [item for item in key_entries if item["block_id"] == block_id]
        first = entries[0]
        scene = scene_lookup[first["scene_id"]]
        public_block_id = f"QB-{block_number:02d}"
        public_blocks.append(
            {
                "public_block_id": public_block_id,
                "scene_id": first["scene_id"],
                "scene_contract": {
                    "premise": scene["premise"],
                    "must_happen": scene["must_happen"],
                    "forbidden_events": scene["forbidden_events"],
                    "allowed_end_state": scene["allowed_end_state"],
                },
                "candidates": candidates,
            }
        )
        arm_to_public = {entry["arm"]: entry["public_text_id"] for entry in entries}
        pair_key.extend(
            [
                {"public_pair_id": f"{public_block_id}-L", "pair_type": "literary", "candidate_ids": [arm_to_public["G"], arm_to_public["L"]]},
                {"public_pair_id": f"{public_block_id}-W", "pair_type": "web_fiction", "candidate_ids": [arm_to_public["G"], arm_to_public["W"]]},
            ]
        )
    for pair in pair_key:
        rng.shuffle(pair["candidate_ids"])
    blind_key = {
        "schema_version": "style-root-cause-blind-key-v0",
        "entries": sorted(key_entries, key=lambda value: value["public_text_id"]),
        "pairs": pair_key,
    }
    public = {
        "schema_version": "style-root-cause-public-material-v0",
        "experiment_id": "style-root-cause-probe-v0",
        "reviewer_notice": "只根据匿名正文和公开场景合同评审；不得推测实验臂或读取私有材料。",
        "classification_options": ["traditional_literary", "commercial_web_fiction", "generic_or_unclear"],
        "score_direction": {
            "literary_intentionality": "higher_better",
            "commercial_momentum": "higher_better",
            "narrative_intentionality": "higher_better",
            "redundant_explanation": "higher_worse",
            "formulaic_expression": "higher_worse",
            "prompt_structure_leak": "higher_worse",
            "character_motivation_credibility": "higher_better",
            "overall_ai_taste": "higher_worse"
        },
        "blocks": public_blocks,
        "pairs": pair_key,
    }
    write_json(output_dir / "private/blind-key.json", blind_key)
    write_json(output_dir / "public/blind-review-material.json", public)
    material_bytes = (output_dir / "public/blind-review-material.json").read_bytes()
    write_json(
        output_dir / "public/material-manifest.json",
        {
            "schema_version": "style-root-cause-public-manifest-v0",
            "blocks": 4,
            "texts": 12,
            "pairs": 8,
            "material_sha256": hashlib.sha256(material_bytes).hexdigest(),
            "blind_key_private": True,
        },
    )
    return {"blocks": 4, "texts": 12, "pairs": 8}


if __name__ == "__main__":
    print(json.dumps(build_public_material(), ensure_ascii=False, indent=2))

