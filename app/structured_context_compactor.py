"""Structure-aware, traceable context compaction for Phase 3 shadow benchmarks."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Iterable

from app.context_compactor import _tokens


@dataclass(frozen=True)
class TextBlock:
    index: int
    start: int
    end: int
    text: str


def split_paragraph_spans(text: str) -> list[TextBlock]:
    """Return non-empty natural paragraphs with exact source offsets."""
    spans: list[TextBlock] = []
    for match in re.finditer(r"[^\r\n]+(?:\r?\n(?!\s*\r?\n)[^\r\n]+)*", str(text or "")):
        raw = match.group(0)
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        start, end = match.start() + left, match.start() + right
        if end > start:
            spans.append(TextBlock(len(spans), start, end, text[start:end]))
    return spans


def _merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


class StructuredContextCompactor:
    """Extract paragraph, dialogue or character windows without hard truncation."""

    STRATEGIES = {"paragraph_window", "dialogue_narrative_block", "character_span_window"}
    RISK_PATTERN = re.compile(
        r"[0-9０-９一二三四五六七八九十百千万亿]+(?:元|万|块|点|年|月|日)?"
        r"|不是|不再|没有|没用|却|但是|因为|所以|如果|借|还款|资金|风险"
        r"|邀请|回答|回应|决定|删除|删帖|愧疚|海盐|咸味"
    )

    def __init__(
        self,
        *,
        strategy: str,
        window_radius: int = 250,
        short_chunk_chars: int = 240,
        soft_token_budget: int = 380,
        max_anchors: int = 3,
    ):
        if strategy not in self.STRATEGIES:
            raise ValueError(f"unknown strategy: {strategy}")
        self.strategy = strategy
        self.window_radius = max(1, int(window_radius))
        self.short_chunk_chars = max(1, int(short_chunk_chars))
        self.soft_token_budget = max(1, int(soft_token_budget))
        self.max_anchors = max(1, int(max_anchors))

    @staticmethod
    def _score_blocks(text: str, blocks: list[TextBlock], query: str, title: str) -> list[tuple[float, int]]:
        query_tokens = _tokens(query)
        title_tokens = _tokens(title)
        scored = []
        for block in blocks:
            block_tokens = _tokens(block.text)
            query_overlap = len(query_tokens & block_tokens) / max(1, len(query_tokens))
            title_overlap = len(title_tokens & block_tokens) / max(1, len(title_tokens))
            risk_hits = len(StructuredContextCompactor.RISK_PATTERN.findall(block.text))
            quote_bonus = 0.025 if "“" in block.text or "\"" in block.text else 0.0
            scored.append((query_overlap + 0.15 * title_overlap + min(risk_hits, 4) * 0.0125 + quote_bonus, block.index))
        return sorted(scored, key=lambda item: (-item[0], item[1]))

    def _anchor_indices(self, blocks: list[TextBlock], query: str, title: str) -> list[int]:
        scored = self._score_blocks("", blocks, query, title)
        positive = [index for score, index in scored if score > 0]
        return positive[: self.max_anchors] or ([0] if blocks else [])

    @staticmethod
    def _fragment(source: dict, text: str, start: int, end: int, *, reason: str) -> dict:
        return {
            "source_id": str(source["source_id"]),
            "section": int(source.get("section", 0)),
            "subsection": int(source.get("subsection", 0)),
            "title": str(source.get("title", "")),
            "start": start,
            "end": end,
            "text": text[start:end],
            "selection_reason": reason,
        }

    def _paragraph_ranges(self, blocks: list[TextBlock], anchors: list[int]) -> list[tuple[int, int]]:
        selected: set[int] = set()
        for anchor in anchors:
            block = blocks[anchor]
            risky = bool(self.RISK_PATTERN.search(block.text)) or "“" in block.text
            radius = 1 if risky else 0
            selected.update(range(max(0, anchor - radius), min(len(blocks), anchor + radius + 1)))
        return _merge_ranges((blocks[i].start, blocks[i].end) for i in selected)

    def _dialogue_ranges(self, blocks: list[TextBlock], anchors: list[int]) -> list[tuple[int, int]]:
        selected: set[int] = set()
        for anchor in anchors:
            selected.add(anchor)
            block = blocks[anchor]
            dialogue = "“" in block.text or "”" in block.text or '"' in block.text
            radius = 2 if dialogue or self.RISK_PATTERN.search(block.text) else 1
            selected.update(range(max(0, anchor - radius), min(len(blocks), anchor + radius + 1)))
        return _merge_ranges((blocks[i].start, blocks[i].end) for i in selected)

    def _character_ranges(self, text: str, blocks: list[TextBlock], anchors: list[int]) -> list[tuple[int, int]]:
        ranges = []
        for anchor in anchors:
            block = blocks[anchor]
            center = (block.start + block.end) // 2
            wanted_start = max(0, center - self.window_radius)
            wanted_end = min(len(text), center + self.window_radius)
            start_block = next((item for item in blocks if item.end >= wanted_start), blocks[0])
            end_block = next((item for item in reversed(blocks) if item.start <= wanted_end), blocks[-1])
            ranges.append((start_block.start, end_block.end))
        return _merge_ranges(ranges)

    def _extract_source(self, source: dict, query: str) -> tuple[list[dict], str]:
        text = str(source["text"])
        if len(text) <= self.short_chunk_chars:
            return [self._fragment(source, text, 0, len(text), reason="short_chunk_full_text")], "short_chunk"
        blocks = split_paragraph_spans(text)
        if not blocks:
            return [self._fragment(source, text, 0, len(text), reason="no_structure_full_text")], "no_structure"
        anchors = self._anchor_indices(blocks, query, str(source.get("title", "")))
        if self.strategy == "paragraph_window":
            ranges = self._paragraph_ranges(blocks, anchors)
        elif self.strategy == "dialogue_narrative_block":
            ranges = self._dialogue_ranges(blocks, anchors)
        else:
            ranges = self._character_ranges(text, blocks, anchors)
        if not ranges:
            return [self._fragment(source, text, 0, len(text), reason="no_anchor_full_text")], "no_anchor"
        return [
            self._fragment(source, text, start, end, reason=self.strategy)
            for start, end in ranges
        ], ""

    def compact(self, *, query: str, sources: Iterable[dict], character_names: Iterable[str] = ()) -> dict:
        del character_names  # Contract parity; names are already present in the real writing query.
        started = time.perf_counter()
        sources = [dict(source) for source in sources]
        source_ids = [str(source["source_id"]) for source in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("sources must already be unique by source_id")
        fragments, fallbacks = [], []
        for source in sources:
            selected, fallback = self._extract_source(source, query)
            fragments.extend(selected)
            if fallback:
                fallbacks.append({"source_id": str(source["source_id"]), "reason": fallback})
        raw_characters = sum(len(str(source["text"])) for source in sources)
        compacted_characters = sum(len(fragment["text"]) for fragment in fragments)
        represented = sorted({fragment["source_id"] for fragment in fragments})
        compacted_tokens = math.ceil(compacted_characters / 4)
        overflow_reason = ""
        if compacted_tokens > self.soft_token_budget:
            overflow_reason = "soft budget exceeded to preserve structured source evidence"
        return {
            "profile": {
                "strategy": self.strategy,
                "window_radius": self.window_radius if self.strategy == "character_span_window" else None,
                "short_chunk_chars": self.short_chunk_chars,
                "soft_token_budget": self.soft_token_budget,
                "max_anchors": self.max_anchors,
                "hard_truncation": False,
            },
            "selected_source_ids": source_ids,
            "represented_source_ids": represented,
            "source_retention": round(len(represented) / len(source_ids), 6) if source_ids else 1.0,
            "raw_characters": raw_characters,
            "raw_tokens": math.ceil(raw_characters / 4),
            "compacted_characters": compacted_characters,
            "compacted_tokens": compacted_tokens,
            "token_reduction": round(1 - compacted_characters / raw_characters, 6) if raw_characters else 0.0,
            "fallbacks": fallbacks,
            "fallback_full_text_count": len(fallbacks),
            "budget_overflow_reason": overflow_reason,
            "fragments": fragments,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
