"""Shadow-only assembly of messages selected by a frozen ContextBroker trace.

This module is deliberately not imported by Writer.  It only renders benchmark
messages and never mutates the production prompt, ContextManager, or source text.
"""

from __future__ import annotations

import copy
import hashlib
import re

from .context_census import estimate_tokens


def messages_hash(messages: list[dict]) -> str:
    payload = "\n".join(f"{item['role']}\0{item['content']}" for item in messages)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(item.get("content", "")) for item in messages)


def _field_from_position(position: str) -> str | None:
    match = re.search(r"\{([^{}]+)\}", position or "")
    return match.group(1) if match else None


def assemble_shadow_messages(sample: dict, frozen_run: dict) -> dict:
    """Render B messages by deleting only complete P3 items from a frozen run."""
    if frozen_run.get("profile") not in {"budgeted_broker", "risk_guarded_broker"}:
        raise ValueError("only frozen budgeted/risk-guarded profiles are supported")
    blocks = sample.get("blocks") or []
    runtime = sample.get("runtime") or {}
    legacy_messages = copy.deepcopy(runtime.get("messages") or [])
    if len(legacy_messages) != 2:
        raise ValueError("sample has no two-message runtime prompt")
    traces = frozen_run.get("items") or []
    if any(item["priority"] in {"P0", "P1", "P2"} and not item["keep"] for item in traces):
        raise ValueError("frozen run drops a protected P0/P1/P2 item")
    decisions = {item["item_id"]: item for item in traces}
    values = copy.deepcopy(runtime["values"])

    recent_blocks = [block for block in blocks if block["category"] == "recent_original"]
    kept_recent = [block for block in recent_blocks if decisions[block["block_id"]]["keep"]]
    original_summary = values.get("summary_context", "")
    if recent_blocks:
        first_at = original_summary.find(recent_blocks[0]["text"])
        prefix = original_summary[:first_at] if first_at >= 0 else ""
        values["summary_context"] = prefix + "\n\n".join(block["text"] for block in kept_recent)

    global_block = next((block for block in blocks if block["block_id"] == "other:global-rules"), None)
    if global_block:
        lines = [line for line in global_block["text"].splitlines() if line.strip()]
        values["rules_context"] = "\n".join(
            line for index, line in enumerate(lines)
            if decisions[f"other:global-rules:line:{index}"]["keep"]
        )

    for block in blocks:
        if block["category"] in {"recent_original", "rag", "fixed_prompt", "current_writing", "character_relation", "handover"}:
            continue
        if block["block_id"] == "other:global-rules":
            continue
        decision = decisions.get(block["block_id"])
        field = _field_from_position(block.get("injection_position", ""))
        if decision and field and not decision["keep"]:
            values[field] = ""

    user_prompt = runtime["template"].format(**values)
    shadow_messages = [
        copy.deepcopy(legacy_messages[0]),
        {"role": "user", "content": user_prompt},
    ]
    if legacy_messages[0] != shadow_messages[0]:
        raise AssertionError("system prompt changed")

    combined_shadow = "\n".join(item["content"] for item in shadow_messages)
    raw_by_id = {block["block_id"]: block for block in blocks}
    for trace in traces:
        if not trace["keep"] or trace["priority"] not in {"P0", "P1", "P2"}:
            continue
        item_id = trace["item_id"]
        if item_id.startswith("other:global-rules:line:"):
            line_index = int(item_id.rsplit(":", 1)[1])
            expected = [line for line in global_block["text"].splitlines() if line.strip()][line_index]
        else:
            block = raw_by_id.get(item_id)
            if not block or block["category"] == "fixed_prompt":
                continue
            expected = block["text"]
        if expected and expected not in combined_shadow:
            raise AssertionError(f"protected item text changed or missing: {item_id}")

    return {
        "legacy_messages": legacy_messages,
        "shadow_messages": shadow_messages,
        "legacy_hash": messages_hash(legacy_messages),
        "shadow_hash": messages_hash(shadow_messages),
        "legacy_tokens": messages_tokens(legacy_messages),
        "shadow_tokens": messages_tokens(shadow_messages),
        "kept_source_ids": [item["source_id"] for item in traces if item["keep"]],
        "dropped_source_ids": [item["source_id"] for item in traces if not item["keep"]],
        "dropped_item_ids": [item["item_id"] for item in traces if not item["keep"]],
    }
