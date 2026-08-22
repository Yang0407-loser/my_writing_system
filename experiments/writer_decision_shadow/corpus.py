from __future__ import annotations

import hashlib
from typing import Any

from .models import ParagraphRecord, ShadowCorpus, ShadowSample


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_shadow_corpus(
    public_payload: dict[str, Any],
    *,
    source_public_raw: str,
) -> ShadowCorpus:
    samples: list[ShadowSample] = []
    for item in public_payload["samples"]:
        text = item["text"]
        raw_paragraphs = text.split("\n\n")
        paragraphs = [
            ParagraphRecord(
                paragraph_id=f"P{index:03d}",
                text=paragraph,
                sha256=_sha(paragraph),
            )
            for index, paragraph in enumerate(raw_paragraphs, 1)
        ]
        sample = ShadowSample(
            blind_id=item["blind_id"],
            scene_code=item["scene_code"],
            text_sha256=_sha(text),
            paragraphs=paragraphs,
        )
        restored = sample.paragraph_separator.join(
            paragraph.text for paragraph in sample.paragraphs
        )
        if restored != text:
            raise ValueError(f"paragraph round-trip failed: {item['blind_id']}")
        samples.append(sample)
    return ShadowCorpus(
        source_public_sha256=_sha(source_public_raw),
        sample_count=len(samples),
        samples=samples,
    )

