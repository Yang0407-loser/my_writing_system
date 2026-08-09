from __future__ import annotations

import json
import random
import secrets
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_bytes

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
        raw = ["".join(sentences[index : index + chunk]) for index in range(0, len(sentences), chunk)]
    return [
        {"paragraph_id": f"P{index:02d}", "text": paragraph}
        for index, paragraph in enumerate(raw, 1)
    ]


def build_public_material(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    summary = load_json(output_dir / "run-summary.json")
    if summary["succeeded"] != 12 or summary["failed"] != 0:
        raise ValueError("public blind package requires all 12 texts")
    queue = load_json(output_dir / "private/generation-queue.locked.json")
    records = {
        path.stem: load_json(path)
        for path in (output_dir / "private/texts").glob("SK-GEN-*.json")
    }
    if len(records) != 12:
        raise ValueError("expected exactly 12 private text records")
    rng = random.Random(secrets.randbits(128))
    public_ids = [f"Q{index:02d}" for index in range(1, 13)]
    rng.shuffle(public_ids)
    key_entries = []
    public_blocks = []
    by_block: dict[str, list[dict[str, Any]]] = {}
    for item in queue:
        record = records[item["generation_id"]]
        public_id = public_ids.pop()
        entry = {
            "public_text_id": public_id,
            "generation_id": item["generation_id"],
            "canary_block_id": item["canary_block_id"],
            "scene_id": item["scene_id"],
            "repeat": item["repeat"],
            "arm": item["arm"],
            "text_sha256": record["text_sha256"],
        }
        key_entries.append(entry)
        by_block.setdefault(item["canary_block_id"], []).append(
            {
                "public_text_id": public_id,
                "text_sha256": record["text_sha256"],
                "paragraphs": split_paragraphs(record["text"]),
            }
        )
    for public_block_number, private_block_id in enumerate(sorted(by_block), 1):
        candidates = by_block[private_block_id]
        rng.shuffle(candidates)
        first = next(
            item for item in queue if item["canary_block_id"] == private_block_id
        )
        source_record = records[first["generation_id"]]
        public_blocks.append(
            {
                "public_block_id": f"QB-{public_block_number:02d}",
                "scene_id": source_record["scene_id"],
                "candidates": candidates,
            }
        )
    blind_key = {
        "schema_version": "writer-sparse-kernel-blind-key-v0",
        "entries": sorted(key_entries, key=lambda value: value["public_text_id"]),
    }
    public = {
        "schema_version": "writer-sparse-kernel-public-material-v0",
        "experiment_id": "writer-sparse-kernel-canary-v0",
        "reviewer_notice": (
            "只根据匿名正文评审，不推测路线身份。每个 block 的三篇文本来自同一"
            "场景和 repeat。"
        ),
        "metrics": [
            "naturalness",
            "less_template",
            "character_credibility",
            "emotional_residue",
            "overall_quality",
            "most_mechanical",
        ],
        "blocks": public_blocks,
    }
    write_json(output_dir / "private/blind-key.json", blind_key)
    write_json(output_dir / "public/blind-review-material.json", public)
    write_json(
        output_dir / "public/material-manifest.json",
        {
            "schema_version": "writer-sparse-kernel-public-manifest-v0",
            "blocks": 4,
            "texts": 12,
            "material_sha256": digest_bytes(
                (output_dir / "public/blind-review-material.json").read_bytes()
            ),
            "blind_key_private": True,
        },
    )
    return {"blocks": 4, "texts": 12}


if __name__ == "__main__":
    print(json.dumps(build_public_material(), ensure_ascii=False, indent=2))
