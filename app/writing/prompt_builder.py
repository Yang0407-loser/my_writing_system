"""Pure, deterministic production Writer prompt rendering."""

from __future__ import annotations

import hashlib
import re

from ..utils.prompt_templates import WRITER_SYSTEM_PROMPT, WRITING_PROMPT, WRITING_SECTION1_PROMPT
from .contracts import PromptArtifact, SubsectionInput


def estimate_prompt_tokens(text: str) -> int:
    if not text:
        return 0
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - chinese
    return int(chinese * 1.5 + other * 0.3)


def messages_hash(messages: list[dict[str, str]]) -> str:
    payload = "\n".join(f"{item['role']}\0{item['content']}" for item in messages)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PromptBuilder:
    """Render the existing Writer templates without consulting runtime stores."""

    def build(
        self,
        prepared: SubsectionInput,
        *,
        token_by_source: dict[str, int] | None = None,
    ) -> PromptArtifact:
        values = dict(prepared.prepared_context_fields)
        # Backward-compatible default for legacy fixtures/checkpoints.  Empty
        # preserves the pre-kernel Writer prompt byte-for-byte at this slot.
        values.setdefault("anti_ai_expression_constraints", "")
        template = (
            WRITING_SECTION1_PROMPT
            if (prepared.section, prepared.subsection) == (1, 1)
            else WRITING_PROMPT
        )
        user_prompt = template.format(**values)
        messages = [
            {"role": "system", "content": WRITER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        content_payload = WRITER_SYSTEM_PROMPT + "\n" + user_prompt
        prompt_version = hashlib.sha256(
            (WRITER_SYSTEM_PROMPT + "\0" + template).encode("utf-8")
        ).hexdigest()
        return PromptArtifact(
            messages=messages,
            messages_hash=messages_hash(messages),
            content_hash=hashlib.sha256(content_payload.encode("utf-8")).hexdigest(),
            estimated_tokens=sum(estimate_prompt_tokens(item["content"]) for item in messages),
            token_by_source=dict(token_by_source or {}),
            source_manifest=list(prepared.source_manifest),
            prompt_version=prompt_version,
        )
