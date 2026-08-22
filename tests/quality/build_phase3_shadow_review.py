"""Build the human review table for Phase 3 shadow-selected candidates.

This is a deterministic export.  It copies selected IDs and scores from the
frozen benchmark, retrieves the exact chunk text by ID, and deliberately leaves
all human judgment fields empty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings
from tests.quality.baseline import DEFAULT_RAG, load_json


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "reports" / "phase3-shadow-retrieval.json"
DEFAULT_OUTPUT = ROOT / "tests" / "quality" / "phase3_shadow_candidates_review.json"


def _load_documents(source_ids: list[str]) -> dict[str, dict]:
    client = chromadb.PersistentClient(
        path=settings.CHROMA_DATA_PATH,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_collection("writing_paragraphs")
    result = collection.get(ids=source_ids, include=["documents", "metadatas"])
    ids = result.get("ids", [])
    documents = result.get("documents", []) or []
    metadatas = result.get("metadatas", []) or []
    return {
        str(source_id): {
            "text": str(documents[index]),
            "metadata": dict(metadatas[index] or {}),
        }
        for index, source_id in enumerate(ids)
    }


def build_review(report: dict, annotation: dict) -> dict:
    annotation_by_index = {
        int(entry["query_index"]): entry for entry in annotation["entries"]
    }
    selected_ids = [
        str(candidate["id"])
        for run in report["queries"]
        for candidate in run["candidate_trace"]
        if candidate["selected"]
    ]
    documents = _load_documents(sorted(set(selected_ids)))
    if set(documents) != set(selected_ids):
        missing = sorted(set(selected_ids) - set(documents))
        raise ValueError(f"selected source IDs missing from Chroma: {missing}")

    task_id = str(annotation["task_id"])
    queries = []
    total = 0
    for run in report["queries"]:
        query_index = int(run["query_index"])
        source = annotation_by_index[query_index]
        seen: set[str] = set()
        candidates = []
        for trace in run["candidate_trace"]:
            if not trace["selected"]:
                continue
            source_id = str(trace["id"])
            if source_id in seen:
                continue
            seen.add(source_id)
            document = documents[source_id]
            metadata = document["metadata"]
            if str(metadata.get("task_id", "")) != task_id:
                raise ValueError(f"task_id mismatch for {source_id}")
            evidence = document["text"]
            digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
            if digest != trace["text_hash"]:
                raise ValueError(f"text hash mismatch for {source_id}")
            candidates.append({
                "review_item_id": f"q{query_index:02d}-c{len(candidates) + 1:02d}",
                "query_index": query_index,
                "query": source["query"],
                "query_intent": list(source["query_intent"]),
                "must_recall_facts": list(source["must_recall_facts"]),
                "source_id": source_id,
                "section": int(trace["section"]),
                "subsection": int(trace["subsection"]),
                "title": str(trace["title"]),
                "evidence_text": evidence,
                "evidence_text_hash": digest,
                "selection_reason": str(trace["reason"]),
                "matched_intents": list(trace["matched_intents"]),
                "score_components": dict(trace["score_components"]),
                "duplicate_section_penalty": float(trace["duplicate_section_penalty"]),
                "final_score": float(trace["final_score"]),
                "human_relevant": "",
                "supports_which_fact": [],
                "review_note": "",
            })
        total += len(candidates)
        queries.append({
            "query_index": query_index,
            "candidate_count": len(candidates),
            "candidates": candidates,
        })

    return {
        "schema_version": 1,
        "purpose": "Phase 3 shadow 候选人工相关性审阅；本文件不包含 Codex 自动判断",
        "source_report": "reports/phase3-shadow-retrieval.json",
        "source_annotation": "tests/rag_annotation_07d1391e.json",
        "task_id": task_id,
        "review_instructions": {
            "human_relevant": "人工填写：相关 / 不相关 / 无法判断",
            "supports_which_fact": "人工填写：复制本候选支持的 must_recall_facts；不支持则保持空数组",
            "review_note": "人工填写判断依据、歧义或缺失信息",
            "scope": "同一 source_id 在不同 query 下必须分别判断；不得跨 query 复用结论",
        },
        "summary": {
            "query_count": len(queries),
            "candidate_count": total,
            "human_reviewed_count": 0,
            "status": "awaiting_human_review",
        },
        "queries": queries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing table; may erase completed human labels",
    )
    args = parser.parse_args()
    if args.output.exists() and not args.force:
        raise FileExistsError(
            f"refusing to overwrite review table without --force: {args.output}"
        )
    review = build_review(load_json(args.report), load_json(args.annotations))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(review["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
