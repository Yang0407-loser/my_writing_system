from __future__ import annotations

import hashlib
import json
import random
import secrets
from typing import Any

from experiments.style_root_cause_probe.public_builder import split_paragraphs

from .builder import DEFAULT_OUTPUT, load_json, write_json


def build_public_material(output_dir=DEFAULT_OUTPUT) -> dict[str, Any]:
    summary = load_json(output_dir / "run-summary.json")
    if summary["succeeded"] != 8 or summary["failed"] != 0:
        raise ValueError("public package requires all eight texts")
    config = load_json(output_dir / "private/config.locked.json")
    queue = load_json(output_dir / "private/generation-queue.locked.json")
    records = {p.stem: load_json(p) for p in (output_dir / "private/texts").glob("AA-GEN-*.json")}
    if len(records) != 8:
        raise ValueError("expected exactly eight private texts")
    rng = random.Random(secrets.randbits(128))
    ids = [f"AQ{index:02d}" for index in range(1, 9)]
    rng.shuffle(ids)
    key_entries, blocks = [], []
    by_block: dict[str, list[dict[str, Any]]] = {}
    for item in queue:
        record = records[item["generation_id"]]
        public_id = ids.pop()
        key_entries.append({
            "public_text_id": public_id, "generation_id": item["generation_id"],
            "block_id": item["block_id"], "scene_id": item["scene_id"],
            "repeat": item["repeat"], "arm": item["arm"], "text_sha256": record["text_sha256"],
        })
        by_block.setdefault(item["block_id"], []).append({
            "public_text_id": public_id, "text_sha256": record["text_sha256"],
            "paragraphs": split_paragraphs(record["text"]),
        })
    scene_lookup = {item["scene_id"]: item for item in config["scenes"]}
    for number, block_id in enumerate(sorted(by_block), 1):
        candidates = by_block[block_id]
        rng.shuffle(candidates)
        first = next(item for item in key_entries if item["block_id"] == block_id)
        scene = scene_lookup[first["scene_id"]]
        blocks.append({
            "public_block_id": f"AB-{number:02d}", "scene_id": first["scene_id"],
            "scene_contract": {key: scene[key] for key in ("premise", "must_happen", "must_hold_back", "forbidden_events", "allowed_end_state")},
            "candidates": candidates,
        })
    blind_key = {"schema_version": "style-anti-ai-blind-key-v0", "entries": sorted(key_entries, key=lambda x: x["public_text_id"])}
    public = {
        "schema_version": "style-anti-ai-public-material-v0", "experiment_id": "style-anti-ai-probe-v0",
        "reviewer_notice": "每个 block 是同一场景同一 repeat 的两个匿名候选；只根据正文和公开合同评审。",
        "score_direction": {
            "commercial_momentum": "higher_better", "character_motivation_credibility": "higher_better",
            "specificity": "higher_better", "naturalness": "higher_better",
            "redundant_explanation": "higher_worse", "formulaic_expression": "higher_worse",
            "summary_closure": "higher_worse", "prompt_structure_leak": "higher_worse", "overall_ai_taste": "higher_worse"
        },
        "blocks": blocks,
    }
    write_json(output_dir / "private/blind-key.json", blind_key)
    write_json(output_dir / "public/blind-review-material.json", public)
    digest = hashlib.sha256((output_dir / "public/blind-review-material.json").read_bytes()).hexdigest()
    write_json(output_dir / "public/material-manifest.json", {"schema_version": "style-anti-ai-public-manifest-v0", "blocks": 4, "texts": 8, "material_sha256": digest, "blind_key_private": True})
    return {"blocks": 4, "texts": 8}


if __name__ == "__main__":
    print(json.dumps(build_public_material(), ensure_ascii=False, indent=2))

