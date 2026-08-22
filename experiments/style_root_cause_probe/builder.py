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


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/style_root_cause_probe/fixtures/root_cause_probe_v0.json"
DEFAULT_OUTPUT = ROOT / "outputs/style-root-cause-probe-v0"
DEFAULT_REPORT = ROOT / "reports/style-root-cause-probe-v0-2026-08-01.md"

SYSTEM_PROMPT = """你是一名中文小说作者。根据用户提供的固定材料写一个完整小说小节。
只输出正文，不输出标题、分析、提纲、字段名、规则或写作说明。
使用第三人称近距离叙述，视点跟随材料中的视点人物。正文约1000—1500个中文字符。
严格遵守必须发生、禁止发生和结尾状态边界；不得照抄输入句子。"""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def digest_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != "style-root-cause-probe-v0":
        raise ValueError("unexpected config schema")
    if set(config.get("arms", {})) != {"G", "L", "W"}:
        raise ValueError("probe requires exactly G/L/W arms")
    scenes = config.get("scenes", [])
    if len(scenes) != 2 or len({item["scene_id"] for item in scenes}) != 2:
        raise ValueError("probe requires exactly two unique scenes")
    if config.get("repeats_per_scene") != 2:
        raise ValueError("probe requires two repeats per scene")
    provider = config["provider"]
    if provider.get("transport_max_retries") != 0:
        raise ValueError("transport retries must be zero")
    target = config["target_characters"]
    if target != {"minimum": 1000, "maximum": 1500}:
        raise ValueError("target character band drift")
    lengths = {arm: len(spec["instruction"]) for arm, spec in config["arms"].items()}
    if max(lengths.values()) - min(lengths.values()) > 50:
        raise ValueError(f"arm instruction length imbalance: {lengths}")
    for scene in scenes:
        required = {
            "scene_id", "scene_type", "title", "premise", "characters",
            "must_happen", "forbidden_events", "allowed_end_state",
        }
        if set(scene) != required:
            raise ValueError(f"scene field drift: {scene.get('scene_id')}")
        if len(scene["must_happen"]) != 4 or len(scene["forbidden_events"]) != 4:
            raise ValueError(f"scene contract must be balanced: {scene['scene_id']}")


def common_scene_contract(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": scene["scene_id"],
        "premise": scene["premise"],
        "characters": scene["characters"],
        "must_happen": scene["must_happen"],
        "forbidden_events": scene["forbidden_events"],
        "allowed_end_state": scene["allowed_end_state"],
    }


def build_requests(config_path: Path = CONFIG) -> list[dict[str, Any]]:
    config = load_json(config_path)
    validate_config(config)
    queue: list[dict[str, Any]] = []
    ordinal = 0
    for scene_index, scene in enumerate(config["scenes"], 1):
        for repeat in range(1, config["repeats_per_scene"] + 1):
            block_id = f"RC-BLOCK-{(scene_index - 1) * 2 + repeat:02d}"
            order = ["G", "L", "W"]
            random.Random(config["randomization_seed"] + scene_index * 10 + repeat).shuffle(order)
            for arm in order:
                ordinal += 1
                payload = {
                    "fixed_scene_contract": common_scene_contract(scene),
                    "narrative_task": config["arms"][arm]["instruction"],
                }
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, indent=2),
                    },
                ]
                request_core = {
                    "messages": messages,
                    "provider_config": config["provider"],
                }
                queue.append(
                    {
                        "schema_version": "style-root-cause-request-v0",
                        "generation_id": f"RC-GEN-{ordinal:02d}",
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
    if len(queue) != 12 or len({item["generation_id"] for item in queue}) != 12:
        raise ValueError("probe queue must contain exactly 12 unique requests")
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
                response_sha256 TEXT,
                finish_reason TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                latency_seconds REAL,
                error_type TEXT,
                error_message_sha256 TEXT,
                FOREIGN KEY(generation_id) REFERENCES generation_queue(generation_id)
            );
            """
        )
        db.executemany(
            "INSERT INTO generation_queue VALUES(?,?,?,?,?)",
            [
                (item["generation_id"], item["ordinal"], item["request_sha256"], "pending", 0)
                for item in queue
            ],
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
        "schema_version": "style-root-cause-manifest-v0",
        "experiment_id": config["experiment_id"],
        "config_sha256": digest_json(config),
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "scenes": [item["scene_id"] for item in config["scenes"]],
        "blocks": 4,
        "arms": {key: value["private_name"] for key, value in config["arms"].items()},
        "repeats_per_scene": 2,
        "generation_requests": 12,
        "transport_max_retries": 0,
        "silent_reruns": False,
        "provider_requests_attempted": 0,
        "fiction_texts": 0,
        "status": "built_not_run",
    }
    write_json(output_dir / "manifest.pre-generation.json", manifest)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Style Root Cause Probe V0\n\n"
        "状态：built_not_run。2 scenes × 3 arms × 2 repeats = 12 texts。"
        "只改变叙事任务说明，不修改生产 Writer。\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))

