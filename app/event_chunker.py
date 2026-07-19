"""Deterministic parent/event chunk contracts for offline Phase 3 experiments."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, asdict


CHARACTERS = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")
TIME_OR_SCENE = re.compile(
    r"^(?:周[一二三四五六日天]|星期|凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|第二天|后来|电话挂断|回到|走到|推开|六点|七点)"
)
DIALOGUE = re.compile(r"[“”]")
INVITATION = re.compile(r"邀请|来帮忙|缺个人|进来|再来|周六来")
MONEY = re.compile(r"钱|万|元|借|还款|资金|银行卡|流水|入股")
CAUSE = re.compile(r"因为|所以|结果|于是|却|但是|没用|决定|删除|删了|删帖")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Paragraph:
    start: int
    end: int
    text: str


def paragraph_spans(text: str) -> list[Paragraph]:
    matches = list(re.finditer(r"\S(?:.*?\S)?(?=\r?\n\s*\r?\n|\Z)", text, re.S))
    return [Paragraph(match.start(), match.end(), text[match.start():match.end()]) for match in matches]


class EventChunker:
    """Split parents at explicit scene changes while keeping dialogue/action chains intact."""

    def __init__(self, *, min_event_chars: int = 120, max_event_chars: int = 360):
        self.min_event_chars = max(1, int(min_event_chars))
        self.max_event_chars = max(self.min_event_chars, int(max_event_chars))

    @staticmethod
    def _event_type(text: str) -> str:
        if MONEY.search(text):
            return "money_or_funding"
        if INVITATION.search(text):
            return "invitation_or_participation"
        if CAUSE.search(text):
            return "cause_or_consequence"
        if DIALOGUE.search(text):
            return "dialogue"
        return "narrative"

    def _boundaries(self, text: str, paragraphs: list[Paragraph]) -> tuple[list[int], dict[int, list[str]]]:
        starts, reasons = [0], {0: ["parent_start"]}
        current = 0
        for index, paragraph in enumerate(paragraphs[1:], 1):
            size = paragraph.start - current
            prior = paragraphs[index - 1].text
            explicit_scene = bool(TIME_OR_SCENE.search(paragraph.text.strip()))
            dialogue_chain = bool(DIALOGUE.search(prior) and DIALOGUE.search(paragraph.text))
            question_response = bool(("？" in prior or "?" in prior) and DIALOGUE.search(paragraph.text))
            invitation_response = bool(
                INVITATION.search(prior)
                and (DIALOGUE.search(paragraph.text) or re.search(r"好|为什么|答应|点头", paragraph.text))
            )
            money_chain = bool(MONEY.search(prior) and MONEY.search(paragraph.text))
            action_result = bool(CAUSE.search(prior) and CAUSE.search(paragraph.text))
            protected_chain = question_response or invitation_response or money_chain or action_result
            if explicit_scene and size >= self.min_event_chars and not protected_chain:
                starts.append(paragraph.start)
                reasons[paragraph.start] = ["explicit_time_or_scene_change"]
                current = paragraph.start
            elif size >= self.max_event_chars and not protected_chain:
                starts.append(paragraph.start)
                reasons[paragraph.start] = ["long_event_at_paragraph_boundary"]
                current = paragraph.start
        return starts, reasons

    def chunk_parent(self, parent: dict) -> list[dict]:
        text = str(parent["text"])
        paragraphs = paragraph_spans(text)
        if not paragraphs:
            starts, reasons = [0], {0: ["unstructured_parent"]}
        else:
            starts, reasons = self._boundaries(text, paragraphs)
        events = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(text)
            event_text = text[start:end]
            digest = _sha(event_text)
            event_id = f"event-{parent['source_id']}-{start}-{end}-{digest[:10]}"
            events.append({
                "source_id": event_id,
                "parent_source_id": str(parent["source_id"]),
                "task_id": str(parent["task_id"]),
                "section": int(parent.get("section", 0)),
                "subsection": int(parent.get("subsection", 0)),
                "title": str(parent.get("title", "")),
                "event_index": index,
                "event_type": self._event_type(event_text),
                "start": start,
                "end": end,
                "text": event_text,
                "actors": [name for name in CHARACTERS if name in event_text],
                "content_hash": digest,
                "schema_version": 1,
                "chunk_level": "event",
                "boundary_reasons": reasons[start],
                "overlap_with_previous_chars": 0,
            })
        return events


def make_parent(*, source_id: str, task_id: str, section: int, subsection: int, title: str, text: str) -> dict:
    return {
        "source_id": str(source_id), "task_id": str(task_id),
        "section": int(section), "subsection": int(subsection), "title": str(title),
        "text": str(text), "content_hash": _sha(str(text)),
        "schema_version": 1, "chunk_level": "parent",
    }
