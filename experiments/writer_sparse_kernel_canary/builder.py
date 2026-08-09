from __future__ import annotations

import json
import random
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_bytes, digest_json


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/writer_sparse_kernel_canary/fixtures/canary_v0.json"
SOURCE_REQUESTS = ROOT / "outputs/writer-boundary-v1-2-r3/requests/locked-requests.synthetic.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-sparse-kernel-canary-v0"
DEFAULT_REPORT = ROOT / "reports/writer-sparse-kernel-canary-v0-2026-07-31.md"

SYSTEM_PROMPT = """你是一名中文小说作者。请根据材料写一段完整的小说正文。
只输出正文，不输出标题、分析、提纲、规则、字段名、检查清单或写作说明。
正文约800—1200个汉字，使用第三人称近距离叙述。
让约束通过动作、观察、停顿和必要对话自然发生；避免解释流程，避免让人物朗读规程，
避免把情绪直接概括成抽象结论。不要照抄输入中的句子。"""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def validate_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_json(CONFIG)
    if digest_bytes(SOURCE_REQUESTS.read_bytes()) != config["source_request_corpus_sha256"]:
        raise ValueError("source request corpus drift")
    requests = load_json(SOURCE_REQUESTS)
    for block in config["blocks"]:
        for arm, text_id in block["source_text_ids"].items():
            source = requests[text_id]
            envelope = source["envelope"]
            if (
                envelope["scene_id"] != block["scene_id"]
                or envelope["arm"] != arm
                or digest_json(envelope) != source["sha256"]
            ):
                raise ValueError(f"invalid pinned source request: {text_id}")
    return config, requests


def common_brief(source_input: dict[str, Any]) -> dict[str, Any]:
    return {
        key: source_input[key]
        for key in (
            "scene_id",
            "scene",
            "characters",
            "world_facts",
            "primary_obligation",
            "decision_shape",
            "long_term_problem",
            "mandatory_events",
            "forbidden_events",
            "style_signature",
        )
    } | {"target_chars": 1000}


def arm_guidance(
    arm: str,
    block: dict[str, Any],
    source_input: dict[str, Any],
) -> dict[str, Any]:
    if arm == "A":
        choices = [
            value["selected_summary"]
            for value in source_input["shared_decision_contract"]["allowed_values"]
        ]
        return {
            "mode": "baseline_self_select",
            "task": "从两个合法内容选项中自行选择一个，并在正文中保持一致。",
            "allowed_choices": choices,
            "realization_freedom": "人物互动、动作组织、段落推进和结尾余味均由你自行决定。",
        }
    if arm == "B":
        return {
            "mode": "expanded_locked_ticket",
            "locked_choice": block["locked_choice"],
            "ordered_realization_plan": block["expanded_ticket"],
            "instruction": "按上述顺序实现，不得改变内容决定；不要输出步骤编号。",
        }
    return {
        "mode": "sparse_decision_kernel",
        "instruction": (
            "以下内容只规定结果边界和叙事压力，不是段落顺序，也不是可复制句子。"
            "请自行决定场景从哪里开始、动作怎样衔接、对话何时出现以及如何收束。"
        ),
        "kernel": block["kernel"],
        "realization_freedom": (
            "允许自由安排动作、观察、沉默、对话和段落节奏；只要不违反五项边界，"
            "不必逐项展示，也不要解释这些边界。"
        ),
    }


def build_requests() -> list[dict[str, Any]]:
    config, requests = validate_inputs()
    queue: list[dict[str, Any]] = []
    generation_number = 0
    for block_index, block in enumerate(config["blocks"], 1):
        order = list("ABC")
        random.Random(9100 + block_index).shuffle(order)
        for arm in order:
            generation_number += 1
            source_id = block["source_text_ids"][arm]
            source_envelope = requests[source_id]["envelope"]
            source_input = source_envelope["messages"][0]["content"]["input"]
            user_payload = {
                "experiment": "writer-sparse-kernel-canary-v0",
                "common_scene_brief": common_brief(source_input),
                "arm_guidance": arm_guidance(arm, block, source_input),
            }
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, indent=2),
                },
            ]
            queue.append(
                {
                    "schema_version": "writer-sparse-kernel-request-v0",
                    "generation_id": f"SK-GEN-{generation_number:02d}",
                    "ordinal": generation_number,
                    "canary_block_id": block["canary_block_id"],
                    "scene_id": block["scene_id"],
                    "repeat": block["repeat"],
                    "arm": arm,
                    "source_text_id": source_id,
                    "messages": messages,
                    "provider_config": config["provider"],
                    "request_sha256": digest_json(
                        {
                            "messages": messages,
                            "provider_config": config["provider"],
                        }
                    ),
                }
            )
    if len(queue) != 12 or len({item["generation_id"] for item in queue}) != 12:
        raise ValueError("canary queue must contain exactly 12 unique requests")
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
                status TEXT NOT NULL CHECK(
                    status IN ('pending','reserved','succeeded','failed')
                ),
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
                error_type TEXT,
                error_message_sha256 TEXT,
                FOREIGN KEY(generation_id) REFERENCES generation_queue(generation_id)
            );
            """
        )
        db.executemany(
            "INSERT INTO generation_queue VALUES(?,?,?,?,?)",
            [
                (
                    item["generation_id"],
                    item["ordinal"],
                    item["request_sha256"],
                    "pending",
                    0,
                )
                for item in queue
            ],
        )


def build(
    output_dir: Path = DEFAULT_OUTPUT,
    report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    queue = build_requests()
    ledger_target = output_dir / "private/generation-ledger.sqlite"
    if ledger_target.exists():
        raise FileExistsError("canary ledger already exists; refusing to reset attempts")
    with tempfile.TemporaryDirectory() as temporary:
        ledger = Path(temporary) / "generation-ledger.sqlite"
        create_ledger(ledger, queue)
        ledger_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger, ledger_target)
    write_json(output_dir / "private/generation-queue.locked.json", queue)
    manifest = {
        "schema_version": "writer-sparse-kernel-canary-v0-manifest",
        "experiment_id": "writer-sparse-kernel-canary-v0",
        "scenes": ["SC9", "SC12"],
        "blocks": 4,
        "arms": {
            "A": "baseline_self_select",
            "B": "expanded_locked_ticket",
            "C": "sparse_decision_kernel",
        },
        "repeats_per_scene": 2,
        "generation_requests": 12,
        "transport_max_retries": 0,
        "silent_reruns": False,
        "provider_requests_attempted": 0,
        "fiction_texts": 0,
        "status": "built_not_run"
    }
    write_json(output_dir / "manifest.pre-generation.json", manifest)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Writer Sparse Decision Kernel Mini-Canary V0\n\n"
        "本轮只比较正文质量：2 scenes × 3 arms × 2 repeats = 12 texts。"
        "A 为基线，B 为展开的锁定实现票据，C 为五项非顺序 Sparse Decision "
        "Kernel。B/C 共享相同潜在决定；provider 参数一致，单次调用且不重试。\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
