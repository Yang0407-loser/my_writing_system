from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.llm_client import get_llm_client
from experiments.style_root_cause_probe.runner import (
    finalize, mark_attempted, reserve, sha256_text,
)

from .builder import DEFAULT_OUTPUT, load_json, write_json


def validate_runtime(queue: list[dict[str, Any]]) -> None:
    if not settings.LLM_API_KEY:
        raise ValueError("LLM credential is unavailable")
    for item in queue:
        spec = item["provider_config"]
        if settings.LLM_BASE_URL != spec["base_url"] or settings.LLM_MODEL != spec["model"]:
            raise ValueError("runtime provider differs from probe contract")
        if spec["transport_max_retries"] != 0:
            raise ValueError("probe transport retry must be zero")


def basic_text_checks(text: str, finish_reason: str | None) -> dict[str, Any]:
    stripped = text.strip()
    labels = (
        "fixed_scene_contract", "commercial_narrative_policy", "language_realization_policy",
        "must_happen", "must_hold_back", "forbidden_events", "allowed_end_state",
    )
    return {
        "nonempty": bool(stripped),
        "character_count": len(stripped),
        "within_target_band_1000_1600": 1000 <= len(stripped) <= 1600,
        "finish_reason": finish_reason,
        "truncation_detected": finish_reason not in (None, "stop"),
        "heading_or_list_detected": bool(re.search(r"(?m)^\s*(?:#{1,6}\s|[-*]\s|\d+[.、]\s)", stripped)),
        "field_leakage_detected": any(label in stripped for label in labels),
    }


def execute_all(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    queue = load_json(output_dir / "private/generation-queue.locked.json")
    validate_runtime(queue)
    ledger = output_dir / "private/generation-ledger.sqlite"
    client = get_llm_client()
    receipts = []
    for item in sorted(queue, key=lambda value: value["ordinal"]):
        generation_id = item["generation_id"]
        reserve(ledger, item)
        mark_attempted(ledger, generation_id)
        metadata: dict[str, Any] = {}
        try:
            spec = item["provider_config"]
            content = client.chat_completion(
                messages=item["messages"], temperature=spec["temperature"],
                top_p=spec["top_p"], max_tokens=spec["max_tokens"], max_retries=0,
                json_mode=False, prompt_name="style_anti_ai_probe_v0",
                completion_metadata_sink=metadata.update,
            )
            response_hash = sha256_text(content)
            finalize(
                ledger, generation_id, "succeeded", response_sha256=response_hash,
                finish_reason=metadata.get("finish_reason"), input_tokens=metadata.get("input_tokens"),
                output_tokens=metadata.get("output_tokens"), latency_seconds=metadata.get("latency_seconds"),
            )
            write_json(
                output_dir / f"private/texts/{generation_id}.json",
                {
                    "schema_version": "style-anti-ai-text-v0", "generation_id": generation_id,
                    "block_id": item["block_id"], "scene_id": item["scene_id"],
                    "scene_type": item["scene_type"], "repeat": item["repeat"], "arm": item["arm"],
                    "text": content, "text_sha256": response_hash, "metadata": metadata,
                    "basic_checks": basic_text_checks(content, metadata.get("finish_reason")),
                },
            )
            receipt = {"generation_id": generation_id, "outcome": "succeeded", "provider_request_attempt_count": 1, "transport_retries": 0, "response_sha256": response_hash, "metadata": metadata}
        except Exception as error:
            error_hash = sha256_text(str(error))
            finalize(ledger, generation_id, "failed", error_type=type(error).__name__, error_message_sha256=error_hash)
            receipt = {"generation_id": generation_id, "outcome": "failed", "provider_request_attempt_count": 1, "transport_retries": 0, "error_type": type(error).__name__, "error_message_sha256": error_hash}
        receipts.append(receipt)
        write_json(output_dir / "private/generation-receipts.json", receipts)
        print(json.dumps({"generation_id": generation_id, "arm": item["arm"], "outcome": receipt["outcome"]}, ensure_ascii=False), flush=True)
    succeeded = sum(item["outcome"] == "succeeded" for item in receipts)
    summary = {
        "schema_version": "style-anti-ai-run-summary-v0", "requested": len(queue),
        "attempted": len(receipts), "succeeded": succeeded, "failed": len(receipts) - succeeded,
        "transport_retries": 0, "silent_reruns": 0, "fiction_texts": succeeded,
        "status": "complete" if len(receipts) == len(queue) else "incomplete",
    }
    write_json(output_dir / "run-summary.json", summary)
    return summary


if __name__ == "__main__":
    print(json.dumps(execute_all(), ensure_ascii=False, indent=2))

