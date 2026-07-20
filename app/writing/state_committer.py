"""Ordered subsection side effects behind one auditable boundary."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable

from ..config import settings
from ..utils.text_chunker import chunk_text
from .contracts import CommitArtifact


class StateCommitter:
    CHECKPOINT_VERSION = "phase4r-r1"

    def __init__(self) -> None:
        self._committed: dict[str, CommitArtifact] = {}
        self.last_artifact: CommitArtifact | None = None

    @staticmethod
    def _hash(value) -> str:
        return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()

    def commit_handover_effects(
        self,
        *,
        idempotency_key: str,
        handover_note: dict | None,
        event_graph,
        world_state,
        world_state_enabled: bool,
        task_id: str,
        section: int,
        subsection: int,
        logger: logging.Logger,
    ) -> CommitArtifact:
        if idempotency_key in self._committed:
            return self._committed[idempotency_key].model_copy(update={"skipped_as_duplicate": True})
        committed_fields: list[str] = []
        warnings: list[str] = []
        note = handover_note or {}
        arc_progress = note.get("arc_progress", {})
        if event_graph and arc_progress and isinstance(arc_progress, dict):
            for character_id, status in arc_progress.items():
                if status in ("done", "deviated"):
                    count = event_graph.update_arc_status(str(character_id), status)
                    if count:
                        committed_fields.append(f"event_graph.arc_progress:{character_id}")
                        logger.info("[%s] 弧线更新: %s → %s (%s 里程碑)", task_id[:8], character_id, status, count)
        new_facts = note.get("new_facts", [])
        if new_facts and world_state and world_state_enabled:
            for fact_text in new_facts:
                if not isinstance(fact_text, str) or not fact_text.strip():
                    continue
                try:
                    fact_id = world_state.add_fact(
                        category="subplot_derived",
                        fact=fact_text.strip(),
                        source_section=section,
                        source_subsection=subsection,
                    )
                    if fact_id:
                        committed_fields.append(f"world_state.fact:{fact_id}")
                        logger.debug("[%s] 新事实已写入 WorldState: %s", task_id[:8], fact_text[:50])
                except Exception as exc:
                    warnings.append(f"world_state:{type(exc).__name__}")
                    logger.debug("[%s] 世界事实写入失败: %s", task_id[:8], fact_text[:50], exc_info=True)
        source_hash = self._hash(note)
        artifact = CommitArtifact(
            idempotency_key=idempotency_key,
            committed_fields=committed_fields,
            source_hash=source_hash,
            output_hash=source_hash,
            checkpoint_version=self.CHECKPOINT_VERSION,
            warnings=warnings,
            rollback_information={"automatic_rollback": False},
        )
        self._committed[idempotency_key] = artifact
        self.last_artifact = artifact
        return artifact

    def commit_local_handover(
        self,
        *,
        idempotency_key: str,
        handover_note: dict | None,
        section_handover_parts: list[dict],
        backref,
        backref_suggestions: list[dict],
    ) -> CommitArtifact:
        if idempotency_key in self._committed:
            return self._committed[idempotency_key].model_copy(update={"skipped_as_duplicate": True})
        committed_fields = []
        if handover_note:
            section_handover_parts.append(handover_note)
            committed_fields.append("handover.section_parts")
        if backref:
            backref_suggestions.extend(backref)
            committed_fields.append("handover.backref_suggestions")
        source_hash = self._hash({"handover": handover_note, "backref": backref})
        artifact = CommitArtifact(
            idempotency_key=idempotency_key,
            committed_fields=committed_fields,
            source_hash=source_hash,
            output_hash=source_hash,
            checkpoint_version=self.CHECKPOINT_VERSION,
            rollback_information={"automatic_rollback": False},
        )
        self._committed[idempotency_key] = artifact
        self.last_artifact = artifact
        return artifact

    def commit_section_handover(
        self,
        *,
        idempotency_key: str,
        section: int,
        section_handover_parts: list[dict],
        handover_notes: list[dict],
        stream_callback=None,
    ) -> tuple[dict, CommitArtifact]:
        if idempotency_key in self._committed:
            artifact = self._committed[idempotency_key].model_copy(update={"skipped_as_duplicate": True})
            return handover_notes[-1], artifact
        note = {
            "from_section": section,
            "to_section": section + 1,
            "foreshadowing": "; ".join(
                item.get("foreshadowing", "")
                for item in section_handover_parts if item.get("foreshadowing")
            ) or "无",
            "character_state": "; ".join(
                item.get("character_state", "")
                for item in section_handover_parts if item.get("character_state")
            ) or "无",
            "open_threads": "; ".join(
                item.get("open_threads", "")
                for item in section_handover_parts if item.get("open_threads")
            ) or "无",
        }
        handover_notes.append(note)
        committed_fields = ["handover.chain"]
        if stream_callback:
            stream_callback("", section, 0, "handover")
            committed_fields.append("stream.handover")
        source_hash = self._hash(section_handover_parts)
        artifact = CommitArtifact(
            idempotency_key=idempotency_key,
            committed_fields=committed_fields,
            source_hash=source_hash,
            output_hash=self._hash(note),
            checkpoint_version=self.CHECKPOINT_VERSION,
            rollback_information={"automatic_rollback": False},
        )
        self._committed[idempotency_key] = artifact
        self.last_artifact = artifact
        return note, artifact

    def commit_subsection(
        self,
        *,
        idempotency_key: str,
        source_hash: str,
        draft: str,
        validation_complete: bool,
        vector_store,
        context_manager,
        blackboard,
        task_id: str,
        section: int,
        subsection: int,
        title: str,
        topic: str,
        stream_callback=None,
        token_usage_provider: Callable[[], object] | None = None,
    ) -> CommitArtifact:
        if not validation_complete:
            raise ValueError("subsection cannot be committed before validation completes")
        if idempotency_key in self._committed:
            prior = self._committed[idempotency_key]
            duplicate = prior.model_copy(update={"skipped_as_duplicate": True})
            self.last_artifact = duplicate
            return duplicate

        committed_fields: list[str] = []
        warnings: list[str] = []
        artifact = CommitArtifact(
            idempotency_key=idempotency_key,
            committed_fields=[],
            source_hash=source_hash,
            output_hash=hashlib.sha256(draft.encode("utf-8")).hexdigest(),
            checkpoint_version=self.CHECKPOINT_VERSION,
            rollback_information={"automatic_rollback": False, "ordered_fields": committed_fields},
        )
        self.last_artifact = artifact
        try:
            for text in chunk_text(draft, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP):
                vector_store.add_text(text=text, metadata={
                    "task_id": task_id,
                    "section": section,
                    "subsection": subsection,
                    "title": title,
                    "topic": topic,
                })
            committed_fields.append("vector_store.chunks")
            vector_store.enforce_task_limit(task_id)
            committed_fields.append("vector_store.task_limit")
            context_manager.add_subsection(draft, section)
            committed_fields.append("context_manager.subsection")
            if token_usage_provider is not None:
                try:
                    blackboard.set(task_id, "token_usage", token_usage_provider())
                    committed_fields.append("blackboard.token_usage")
                except Exception as exc:
                    warnings.append(f"token_usage:{type(exc).__name__}")
            if stream_callback:
                stream_callback(draft, section, subsection, "section_end")
                committed_fields.append("stream.section_end")
        finally:
            artifact = artifact.model_copy(update={
                "committed_fields": list(committed_fields),
                "warnings": list(warnings),
                "rollback_information": {
                    "automatic_rollback": False,
                    "ordered_fields": list(committed_fields),
                },
            })
            self.last_artifact = artifact
        self._committed[idempotency_key] = artifact
        return artifact

    def save_checkpoint(self, blackboard, task_id: str, state: dict) -> CommitArtifact:
        source_hash = self._hash(state)
        key = f"checkpoint:{task_id}:{state.get('current_section', 0)}"
        if key in self._committed:
            return self._committed[key].model_copy(update={"skipped_as_duplicate": True})
        blackboard.save_checkpoint(task_id, state)
        artifact = CommitArtifact(
            idempotency_key=key,
            committed_fields=["blackboard.checkpoint"],
            source_hash=source_hash,
            output_hash=source_hash,
            checkpoint_version=self.CHECKPOINT_VERSION,
            rollback_information={"automatic_rollback": False},
        )
        self._committed[key] = artifact
        self.last_artifact = artifact
        return artifact
