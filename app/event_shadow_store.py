"""Isolated Chroma adapter for Phase 3 event-shadow experiments only."""

from __future__ import annotations

import hashlib
import json
import uuid


INDEX_PROFILE = "batch2gb_event_shadow"
CHUNK_LEVEL = "event"


def derive_shadow_task_id(source_task_id: str) -> str:
    if not source_task_id or "*" in source_task_id:
        raise ValueError("source task id must be non-empty and exact")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"writer:{source_task_id}:batch2gb-shadow"))


def shadow_filter(shadow_task_id: str) -> dict:
    if not shadow_task_id or "*" in shadow_task_id:
        raise ValueError("shadow task id must be non-empty and exact")
    return {"$and": [
        {"task_id": shadow_task_id},
        {"index_profile": INDEX_PROFILE},
        {"chunk_level": CHUNK_LEVEL},
    ]}


def collection_snapshot(collection, task_id: str) -> dict:
    result = collection.get(where={"task_id": task_id}, include=["metadatas", "documents"])
    rows = sorted(zip(result.get("ids", []), result.get("documents", []), result.get("metadatas", [])))
    digest = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"count": len(rows), "digest": digest, "ids": [row[0] for row in rows]}


class EventShadowStore:
    def __init__(self, vector_store, *, source_task_id: str):
        self.vector_store = vector_store
        self.collection = vector_store._collection
        self.source_task_id = source_task_id
        self.shadow_task_id = derive_shadow_task_id(source_task_id)

    def ingest(self, events: list[dict]) -> dict:
        expected_ids = [event["source_id"] for event in events]
        existing = self.collection.get(ids=expected_ids, include=["metadatas"])
        existing_by_id = dict(zip(existing.get("ids", []), existing.get("metadatas", [])))
        added, reused = [], []
        for event in events:
            event_id = event["source_id"]
            if event_id in existing_by_id:
                metadata = existing_by_id[event_id] or {}
                if metadata.get("task_id") != self.shadow_task_id or metadata.get("index_profile") != INDEX_PROFILE:
                    raise ValueError(f"stable event id collision: {event_id}")
                reused.append(event_id)
                continue
            metadata = {
                "task_id": self.shadow_task_id, "source_task_id": self.source_task_id,
                "index_profile": INDEX_PROFILE, "chunk_level": CHUNK_LEVEL,
                "parent_source_id": event["parent_source_id"], "event_source_id": event_id,
                "section": event["section"], "subsection": event["subsection"],
                "title": event["title"], "event_index": event["event_index"],
                "event_type": event["event_type"], "content_hash": event["content_hash"],
                "schema_version": event["schema_version"], "start": event["start"], "end": event["end"],
            }
            self.collection.add(ids=[event_id], documents=[event["text"]], metadatas=[metadata])
            added.append(event_id)
        return {"expected": len(events), "added": added, "reused": reused, "stable_ids": expected_ids}

    def query(self, query: str, *, event_k: int = 15) -> dict:
        import time
        started = time.perf_counter()
        result = self.collection.query(
            query_texts=[query], n_results=max(1, event_k), where=shadow_filter(self.shadow_task_id),
            include=["documents", "metadatas", "distances"],
        )
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        rows = []
        for rank, event_id in enumerate((result.get("ids") or [[]])[0], 1):
            metadata = (result.get("metadatas") or [[]])[0][rank - 1]
            distance = (result.get("distances") or [[]])[0][rank - 1]
            rows.append({"event_id": event_id, "text": (result.get("documents") or [[]])[0][rank - 1],
                "metadata": metadata, "distance": distance,
                "score": round(1 / (1 + max(float(distance), 0)), 6), "rank": rank})
        return {"filter": shadow_filter(self.shadow_task_id), "elapsed_ms": elapsed, "events": rows}

    def cleanup_exact(self, *, task_id: str, index_profile: str, event_ids: list[str], execute: bool = False) -> int:
        if task_id != self.shadow_task_id or task_id == self.source_task_id or "*" in task_id:
            raise ValueError("cleanup refused: task id is not the exact isolated shadow task")
        if index_profile != INDEX_PROFILE or not event_ids or any(not item or "*" in item for item in event_ids):
            raise ValueError("cleanup refused: exact profile and event ids are required")
        found = self.collection.get(ids=event_ids, where=shadow_filter(task_id), include=[]).get("ids", [])
        if execute and found:
            self.collection.delete(ids=found)
        return len(found)
