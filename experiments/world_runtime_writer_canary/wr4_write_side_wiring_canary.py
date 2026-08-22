# -*- coding: utf-8 -*-
"""WR4 write-side wiring canary: real WR commit -> rag_metadata -> chunks.

Uses the frozen C2.1-R10 WR commit chain (3 subsections of the Saturday
bakery gold) and the real subsection prose from the same canary, and commits
chunks through the production ``StateCommitter.commit_subsection`` path with
``rag_metadata`` supplied by the write-side provider.  The collection is
isolated (new runtime dir); zero LLM; production off; no sealed runtime
touched.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.context_contracts import serialize_chroma_metadata
from app.embedding.factory import get_embedding_provider
from app.retrieval_pipeline import _decode_metadata_list
from app.vector_store import _ChromaEmbedFn
from app.writing.state_committer import StateCommitter
from app.writing.wr_rag_metadata_wiring import (
    build_rag_metadata_provider,
    flat_rag_metadata,
    load_wr_committed,
)


ROOT = Path(__file__).resolve().parents[2]
COMMITS_DIR = (
    ROOT
    / ".world_runtime_state_commit_canary_runtime"
    / "c21r10"
    / "private"
    / "commits"
)
OUTPUTS_DIR = (
    ROOT
    / ".world_runtime_state_commit_canary_runtime"
    / "c21r10"
    / "private"
    / "outputs"
)
RUNTIME_DIR = ROOT / ".world_runtime_wr4_write_side_canary_runtime"
COLLECTION_NAME = "writing_paragraphs_wr4_write_side_canary"
TASK_ID = "wr4-write-side-canary"
REPORT_JSON = ROOT / "reports" / "wr4-write-side-wiring-canary-2026-08-07.json"
REPORT_MD = ROOT / "reports" / "world-runtime-wr4-write-side-wiring-canary-2026-08-07.md"


class _IsolatedVectorStore:
    def __init__(self, collection) -> None:
        self._collection = collection
        self.added: list[tuple[str, dict]] = []

    def add_text(self, text: str, metadata: dict) -> str | None:
        normalized = str(text or "").strip()
        if not normalized:
            return None
        safe = serialize_chroma_metadata(dict(metadata or {}))
        safe.setdefault("content_hash", hashlib.sha256(normalized.encode("utf-8")).hexdigest())
        doc_id = str(uuid.uuid4())
        self._collection.add(ids=[doc_id], documents=[normalized], metadatas=[safe])
        self.added.append((normalized, dict(metadata or {})))
        return doc_id

    def enforce_task_limit(self, task_id: str) -> int:
        return 0


class _FakeContextManager:
    def add_subsection(self, draft: str, section: int) -> None:
        pass


class _FakeBlackboard:
    def set(self, task_id: str, key: str, value) -> None:
        pass


def main() -> None:
    if not COMMITS_DIR.exists() or not OUTPUTS_DIR.exists():
        raise SystemExit(f"missing c21r10 canary artifacts: {COMMITS_DIR} / {OUTPUTS_DIR}")

    client = chromadb.PersistentClient(
        path=str(RUNTIME_DIR), settings=ChromaSettings(anonymized_telemetry=False)
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=_ChromaEmbedFn(get_embedding_provider()),
    )
    provider = build_rag_metadata_provider(COMMITS_DIR, "saturday-bakery")
    committer = StateCommitter()
    store = _IsolatedVectorStore(collection)

    started = time.perf_counter()
    subsections_report = []
    for subsection in range(1, 4):
        prose_path = OUTPUTS_DIR / f"S{subsection}.txt"
        if not prose_path.exists():
            raise SystemExit(f"missing subsection prose: {prose_path}")
        prose = prose_path.read_text(encoding="utf-8").strip()
        rag_metadata = provider(1, subsection)
        assert rag_metadata is not None, f"no WR commit for S1.{subsection}"
        committed = load_wr_committed(COMMITS_DIR, "saturday-bakery", 1, subsection)
        expected = flat_rag_metadata(committed, section=1, subsection=subsection)
        artifact = committer.commit_subsection(
            idempotency_key=f"write-side:{TASK_ID}:1:{subsection}",
            source_hash=hashlib.sha256(prose.encode("utf-8")).hexdigest(),
            draft=prose,
            validation_complete=True,
            vector_store=store,
            context_manager=_FakeContextManager(),
            blackboard=_FakeBlackboard(),
            task_id=TASK_ID,
            section=1,
            subsection=subsection,
            title=f"S{subsection}",
            topic="周六面包店与凌晨三点半",
            rag_metadata=rag_metadata,
        )
        got = collection.get(
            where={"$and": [{"task_id": TASK_ID}, {"subsection": subsection}]},
            include=["documents", "metadatas"],
        )
        chunk_count = len(got.get("ids", []))
        verified = []
        for meta in got.get("metadatas", []) or []:
            checks = {
                "characters": sorted(_decode_metadata_list(meta.get("characters"))),
                "locations": sorted(_decode_metadata_list(meta.get("locations"))),
                "time": meta.get("time"),
                "weekday": meta.get("weekday"),
                "metadata_source": meta.get("metadata_source"),
            }
            expected_flat = {
                "characters": sorted(expected["characters"]),
                "locations": sorted(expected["locations"]),
                "time": expected["time"],
                "weekday": expected["weekday"],
                "metadata_source": expected["metadata_source"],
            }
            verified.append(checks == expected_flat)
        subsections_report.append(
            {
                "subsection": subsection,
                "commit_id": artifact.idempotency_key,
                "chunk_count": chunk_count,
                "rag_metadata": rag_metadata,
                "metadata_verified": bool(verified) and all(verified),
                "verified_chunks": sum(1 for ok in verified if ok),
            }
        )

    # Retrieval sanity: real-projected metadata should be present on hits.
    queries = [
        "凌晨三点半，林晚在面包店门口等周野开门",
        "季晴把文章链接发给林晚，说“你火了”",
        "老吴在面包店帮忙招呼客人",
    ]
    retrieval = []
    for query in queries:
        result = collection.query(
            query_texts=[query],
            n_results=5,
            where={"task_id": TASK_ID},
        )
        items = []
        ids = (result.get("ids") or [[]])[0] or []
        docs = (result.get("documents") or [[]])[0] or []
        metas = (result.get("metadatas") or [[]])[0] or []
        for index, doc_id in enumerate(ids):
            meta = metas[index] if index < len(metas) and metas[index] else {}
            items.append(
                {
                    "id": doc_id,
                    "section": meta.get("section"),
                    "subsection": meta.get("subsection"),
                    "characters": sorted(_decode_metadata_list(meta.get("characters"))),
                    "locations": sorted(_decode_metadata_list(meta.get("locations"))),
                    "time": meta.get("time"),
                    "weekday": meta.get("weekday"),
                    "metadata_source": meta.get("metadata_source"),
                    "preview": docs[index][:40] if index < len(docs) else "",
                }
            )
        retrieval.append({"query": query, "hits": items})
    elapsed = round(time.perf_counter() - started, 3)

    report = {
        "schema_version": "wr4-write-side-wiring-canary-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": {
            "commits_dir": str(COMMITS_DIR),
            "outputs_dir": str(OUTPUTS_DIR),
            "commit_chain": "c21r10",
            "task_id": "saturday-bakery",
        },
        "profile": {
            "mode": "canary",
            "llm_calls": 0,
            "production_switched": False,
            "isolated_runtime": str(RUNTIME_DIR),
            "collection": COLLECTION_NAME,
        },
        "subsections": subsections_report,
        "all_metadata_verified": all(item["metadata_verified"] for item in subsections_report),
        "retrieval": retrieval,
        "elapsed_seconds": elapsed,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": (
                    "write_side_wiring_verified"
                    if report["all_metadata_verified"]
                    else "write_side_wiring_failed"
                ),
                "all_metadata_verified": report["all_metadata_verified"],
                "subsections": [
                    {
                        "subsection": item["subsection"],
                        "chunk_count": item["chunk_count"],
                        "metadata_verified": item["metadata_verified"],
                    }
                    for item in subsections_report
                ],
                "retrieval_hits": [
                    {
                        "query": item["query"],
                        "top_characters": item["hits"][0]["characters"] if item["hits"] else [],
                        "top_metadata_source": item["hits"][0]["metadata_source"] if item["hits"] else None,
                    }
                    for item in retrieval
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# WR4 写侧接线 canary：真实 WR commit → rag_metadata → chunk 入库",
        "",
        "日期：2026-08-07",
        "",
        "- 提交链：C2.1-R10（周六面包店 gold，S1.1–S1.3，16 facts/commit）",
        "- 写侧路径：`build_rag_metadata_provider` → `StateCommitter.commit_subsection(rag_metadata=...)`",
        "- 隔离集合、零 LLM、生产 off、未触碰任何 sealed runtime。",
        "",
        "## 逐小节",
        "",
        "| subsection | commit | chunk_count | metadata_verified |",
        "|---:|---|---:|---:|",
    ]
    for item in report["subsections"]:
        lines.append(
            f"| {item['subsection']} | {item['commit_id']} | {item['chunk_count']} | "
            f"{item['metadata_verified']} ({item['verified_chunks']}/{item['chunk_count']}) |"
        )
    lines += [
        "",
        f"## 结论：{'write_side_wiring_verified' if report['all_metadata_verified'] else 'write_side_wiring_failed'}",
        "",
        "## 检索冒烟",
        "",
        "| query | top1 characters | top1 metadata_source |",
        "|---|---|---|",
    ]
    for item in report["retrieval"]:
        top = item["hits"][0] if item["hits"] else {}
        lines.append(
            f"| {item['query']} | {','.join(top.get('characters', [])) or '-'} | "
            f"{top.get('metadata_source') or '-'} |"
        )
    lines += [
        "",
        "机器可读数据：[wr4-write-side-wiring-canary-2026-08-07.json]"
        "(E:/writer/my_writing_system/reports/wr4-write-side-wiring-canary-2026-08-07.json)",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
