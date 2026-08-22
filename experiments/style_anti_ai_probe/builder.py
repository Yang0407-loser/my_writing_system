from __future__ import annotations

import hashlib
import json
import random
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from experiments.style_root_cause_probe.builder import digest_json, load_json, write_json


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/style_anti_ai_probe/fixtures/anti_ai_probe_v0.json"
DEFAULT_OUTPUT = ROOT / "outputs/style-anti-ai-probe-v0"
DEFAULT_REPORT = ROOT / "reports/style-anti-ai-probe-v0-2026-08-02.md"

SYSTEM_PROMPT = """你是一名中文商业玄幻网文作者。根据固定材料写一个完整小说小节。
只输出正文，不输出标题、分析、提纲、字段名、规则或写作说明。
使用第三人称近距离叙述，视点跟随材料中的视点人物。正文约1000—1600个中文字符。
严格遵守必须发生、暂缓揭露、禁止发生和结尾状态边界；不得照抄输入句子。"""


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != "style-anti-ai-probe-v0":
        raise ValueError("unexpected config schema")
    if set(config.get("arms", {})) != {"W", "WA"}:
        raise ValueError("probe requires exactly W/WA arms")
    if not config["arms"]["WA"]["additional_policy"] or config["arms"]["W"]["additional_policy"]:
        raise ValueError("only WA may contain the anti-AI treatment")
    if len(config.get("scenes", [])) != 2 or config.get("repeats_per_scene") != 2:
        raise ValueError("probe requires two scenes and two repeats")
    if config["provider"].get("transport_max_retries") != 0:
        raise ValueError("transport retries must be zero")
    if config["target_characters"] != {"minimum": 1000, "maximum": 1600}:
        raise ValueError("target character band drift")
    for scene in config["scenes"]:
        if len(scene["must_happen"]) != 4 or len(scene["forbidden_events"]) != 4:
            raise ValueError(f"unbalanced scene contract: {scene['scene_id']}")
        if len(scene["must_hold_back"]) != 2:
            raise ValueError(f"hold-back contract drift: {scene['scene_id']}")


def scene_contract(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        key: scene[key]
        for key in (
            "scene_id", "premise", "characters", "must_happen", "must_hold_back",
            "forbidden_events", "allowed_end_state",
        )
    }


def build_requests(config_path: Path = CONFIG) -> list[dict[str, Any]]:
    config = load_json(config_path)
    validate_config(config)
    queue: list[dict[str, Any]] = []
    ordinal = 0
    for scene_index, scene in enumerate(config["scenes"], 1):
        for repeat in range(1, 3):
            block_id = f"AA-BLOCK-{(scene_index - 1) * 2 + repeat:02d}"
            order = ["W", "WA"]
            random.Random(config["randomization_seed"] + scene_index * 10 + repeat).shuffle(order)
            for arm in order:
                ordinal += 1
                payload = {
                    "fixed_scene_contract": scene_contract(scene),
                    "commercial_narrative_policy": config["shared_web_policy"],
                }
                if config["arms"][arm]["additional_policy"]:
                    payload["language_realization_policy"] = config["arms"][arm]["additional_policy"]
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
                ]
                request_core = {"messages": messages, "provider_config": config["provider"]}
                queue.append(
                    {
                        "schema_version": "style-anti-ai-request-v0",
                        "generation_id": f"AA-GEN-{ordinal:02d}",
                        "ordinal": ordinal,
                        "block_id": block_id,
                        "scene_id": scene["scene_id"],
                        "scene_type": scene["scene_type"],
                        "repeat": repeat,
                        "arm": arm,
                        "messages": messages,
                        "provider_config": config["provider"],
                        "request_sha256": digest_json(request_core),
                    }
                )
    if len(queue) != 8 or len({item["generation_id"] for item in queue}) != 8:
        raise ValueError("probe queue must contain exactly eight requests")
    return queue


def create_ledger(path: Path, queue: list[dict[str, Any]]) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE generation_queue(
                generation_id TEXT PRIMARY KEY,
                ordinal INTEGER UNIQUE NOT NULL,
                request_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','reserved','succeeded','failed')),
                attempt_count INTEGER NOT NULL CHECK(attempt_count IN (0,1))
            );
            CREATE TABLE generation_attempts(
                generation_id TEXT PRIMARY KEY,
                attempt_number INTEGER NOT NULL CHECK(attempt_number=1),
                request_attempted INTEGER NOT NULL CHECK(request_attempted IN (0,1)),
                outcome TEXT CHECK(outcome IN ('succeeded','failed')),
                response_sha256 TEXT, finish_reason TEXT, input_tokens INTEGER,
                output_tokens INTEGER, latency_seconds REAL, error_type TEXT,
                error_message_sha256 TEXT,
                FOREIGN KEY(generation_id) REFERENCES generation_queue(generation_id)
            );
            """
        )
        db.executemany(
            "INSERT INTO generation_queue VALUES(?,?,?,?,?)",
            [(item["generation_id"], item["ordinal"], item["request_sha256"], "pending", 0) for item in queue],
        )


def build(output_dir: Path = DEFAULT_OUTPUT, report_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    config = load_json(CONFIG)
    validate_config(config)
    queue = build_requests()
    ledger_target = output_dir / "private/generation-ledger.sqlite"
    if ledger_target.exists():
        raise FileExistsError("probe ledger already exists; refusing to reset attempts")
    with tempfile.TemporaryDirectory() as temporary:
        ledger = Path(temporary) / "generation-ledger.sqlite"
        create_ledger(ledger, queue)
        ledger_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger, ledger_target)
    write_json(output_dir / "private/generation-queue.locked.json", queue)
    write_json(output_dir / "private/config.locked.json", config)
    manifest = {
        "schema_version": "style-anti-ai-manifest-v0",
        "experiment_id": config["experiment_id"],
        "config_sha256": digest_json(config),
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "scenes": [item["scene_id"] for item in config["scenes"]],
        "blocks": 4,
        "arms": {key: value["private_name"] for key, value in config["arms"].items()},
        "generation_requests": 8,
        "transport_max_retries": 0,
        "silent_reruns": False,
        "status": "built_not_run",
    }
    write_json(output_dir / "manifest.pre-generation.json", manifest)
    report_path.write_text(
        "# Style Anti-AI Surface Mini-Probe V0\n\n状态：built_not_run。"
        "2 scenes × W/WA × 2 repeats = 8 texts。\n",
        encoding="utf-8", newline="\n",
    )
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))

