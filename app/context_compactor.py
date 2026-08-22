"""Deterministic, traceable context compaction for Phase 3 shadow experiments."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Iterable


def _tokens(text: str) -> set[str]:
    lowered = str(text or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _normalized_sentence_set(text: str) -> set[str]:
    values = set()
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", str(text or "")):
        value = re.sub(r"\s+", "", match.group(0)).strip("。！？!?")
        if value:
            values.add(value)
    return values


@dataclass(frozen=True)
class SentenceSpan:
    index: int
    start: int
    end: int
    text: str


def split_sentence_spans(text: str) -> list[SentenceSpan]:
    spans = []
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", str(text or "")):
        raw = match.group(0)
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        start = match.start() + left
        end = match.start() + right
        if end > start:
            spans.append(SentenceSpan(len(spans), start, end, text[start:end]))
    return spans


class ContextCompactor:
    """Fold near duplicates and extract query-linked spans without hard truncation."""

    def __init__(
        self,
        *,
        duplicate_threshold: float = 0.82,
        max_anchor_sentences: int = 2,
        neighbor_radius: int = 1,
        soft_token_budget: int = 400,
    ):
        self.duplicate_threshold = max(0.0, min(float(duplicate_threshold), 1.0))
        self.max_anchor_sentences = max(1, int(max_anchor_sentences))
        self.neighbor_radius = max(0, int(neighbor_radius))
        self.soft_token_budget = max(1, int(soft_token_budget))

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        union = left | right
        return len(left & right) / len(union) if union else 0.0

    def _duplicate_groups(self, sources: list[dict]) -> list[dict]:
        groups: list[dict] = []
        assigned = set()
        sentence_sets = {
            str(source["source_id"]): _normalized_sentence_set(source["text"])
            for source in sources
        }
        ordered = sorted(
            sources,
            key=lambda item: (-float(item.get("final_score", 0.0)), str(item["source_id"])),
        )
        for source in ordered:
            source_id = str(source["source_id"])
            if source_id in assigned:
                continue
            members = [source_id]
            similarities = {source_id: 1.0}
            assigned.add(source_id)
            for other in ordered:
                other_id = str(other["source_id"])
                if other_id in assigned:
                    continue
                similarity = self._jaccard(sentence_sets[source_id], sentence_sets[other_id])
                if similarity >= self.duplicate_threshold:
                    assigned.add(other_id)
                    members.append(other_id)
                    similarities[other_id] = round(similarity, 6)
            groups.append({
                "canonical_source_id": source_id,
                "represented_source_ids": members,
                "similarities_to_canonical": similarities,
            })
        return groups

    def _extract_fragments(
        self, source: dict, *, query_tokens: set[str], characters: set[str],
    ) -> list[dict]:
        text = str(source["text"])
        spans = split_sentence_spans(text)
        if not spans:
            return [{
                "source_id": str(source["source_id"]),
                "section": int(source.get("section", 0)),
                "subsection": int(source.get("subsection", 0)),
                "title": str(source.get("title", "")),
                "start": 0,
                "end": len(text),
                "text": text,
                "anchor_sentence_indices": [],
            }]
        title_tokens = _tokens(str(source.get("title", "")))
        scored = []
        for span in spans:
            sentence_tokens = _tokens(span.text)
            overlap = len(query_tokens & sentence_tokens) / len(query_tokens) if query_tokens else 0.0
            title_overlap = len(title_tokens & sentence_tokens) / len(title_tokens) if title_tokens else 0.0
            character_hits = sum(name in span.text for name in characters)
            score = overlap + 0.20 * title_overlap + 0.08 * character_hits
            scored.append((round(score, 8), span.index))
        anchors = [
            index for _, index in sorted(scored, key=lambda item: (-item[0], item[1]))
            [: self.max_anchor_sentences]
        ]
        selected_indices = set()
        for anchor in anchors:
            selected_indices.update(
                range(
                    max(0, anchor - self.neighbor_radius),
                    min(len(spans), anchor + self.neighbor_radius + 1),
                )
            )
        runs = []
        for index in sorted(selected_indices):
            if not runs or index != runs[-1][-1] + 1:
                runs.append([index])
            else:
                runs[-1].append(index)
        fragments = []
        for run in runs:
            start = spans[run[0]].start
            end = spans[run[-1]].end
            fragments.append({
                "source_id": str(source["source_id"]),
                "section": int(source.get("section", 0)),
                "subsection": int(source.get("subsection", 0)),
                "title": str(source.get("title", "")),
                "start": start,
                "end": end,
                "text": text[start:end],
                "anchor_sentence_indices": [index for index in anchors if index in run],
            })
        return fragments

    def compact(
        self,
        *,
        query: str,
        sources: Iterable[dict],
        character_names: Iterable[str] = (),
    ) -> dict:
        started = time.perf_counter()
        sources = [dict(source) for source in sources]
        source_ids = [str(source["source_id"]) for source in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("sources must already be unique by source_id")
        groups = self._duplicate_groups(sources)
        by_id = {str(source["source_id"]): source for source in sources}
        canonical_ids = {group["canonical_source_id"] for group in groups}
        raw_characters = sum(len(str(source["text"])) for source in sources)
        deduplicated_characters = sum(len(str(by_id[source_id]["text"])) for source_id in canonical_ids)
        query_tokens = _tokens(query)
        characters = {name for name in character_names if name and name in query}
        fragments = [
            fragment
            for source in sources
            for fragment in self._extract_fragments(
                source, query_tokens=query_tokens, characters=characters
            )
        ]
        compacted_characters = sum(len(fragment["text"]) for fragment in fragments)
        compacted_tokens = math.ceil(compacted_characters / 4)
        represented_source_ids = sorted({fragment["source_id"] for fragment in fragments})
        overflow_reason = ""
        if compacted_tokens > self.soft_token_budget:
            overflow_reason = (
                "soft budget exceeded to preserve at least one traceable fragment per selected source"
            )
        return {
            "profile": {
                "duplicate_threshold": self.duplicate_threshold,
                "max_anchor_sentences": self.max_anchor_sentences,
                "neighbor_radius": self.neighbor_radius,
                "soft_token_budget": self.soft_token_budget,
                "hard_truncation": False,
            },
            "selected_source_ids": source_ids,
            "represented_source_ids": represented_source_ids,
            "source_retention": (
                round(len(represented_source_ids) / len(source_ids), 6) if source_ids else 1.0
            ),
            "duplicate_groups": groups,
            "near_duplicate_group_count": sum(
                len(group["represented_source_ids"]) > 1 for group in groups
            ),
            "raw_characters": raw_characters,
            "raw_tokens": math.ceil(raw_characters / 4),
            "deduplicated_characters": deduplicated_characters,
            "deduplicated_tokens": math.ceil(deduplicated_characters / 4),
            "folded_characters": raw_characters - deduplicated_characters,
            "compacted_characters": compacted_characters,
            "compacted_tokens": compacted_tokens,
            "budget_overflow_reason": overflow_reason,
            "fragments": fragments,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
