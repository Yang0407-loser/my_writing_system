"""Benchmark Chroma metadata filtering against per-task collections.

The default smoke profile is safe for local regression.  ``--profile plan``
runs the complete 1/10/100 task × 100/1000/5000 chunk matrix from the refactor
plan and can require substantial time and disk space.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import tempfile
import time
from pathlib import Path
from statistics import mean

import chromadb
from chromadb.api.client import SharedSystemClient
from chromadb.config import Settings


def embedding(task_index: int, chunk_index: int, task_count: int, chunk_count: int) -> list[float]:
    task_value = task_index / max(task_count, 1)
    chunk_value = chunk_index / max(chunk_count, 1)
    return [
        task_value,
        chunk_value,
        math.sin(task_index + 1),
        math.cos(chunk_index + 1),
        task_value * chunk_value,
        chunk_value * chunk_value,
        1.0 - task_value,
        1.0,
    ]


def batched(values: list, size: int = 500):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def add_task_chunks(collection, task_index: int, task_count: int, chunk_count: int) -> None:
    rows = list(range(chunk_count))
    for chunk_batch in batched(rows):
        collection.add(
            ids=[f"t{task_index}:c{chunk}" for chunk in chunk_batch],
            embeddings=[
                embedding(task_index, chunk, task_count, chunk_count)
                for chunk in chunk_batch
            ],
            metadatas=[
                {
                    "task_id": f"task-{task_index}",
                    "section": chunk // 10,
                    "content_hash": f"{task_index}-{chunk}",
                }
                for chunk in chunk_batch
            ],
        )


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(int(len(ordered) * ratio), len(ordered) - 1)]


def run_case(task_count: int, chunk_count: int, repeats: int, base_dir: Path) -> dict:
    case_dir = base_dir / f"tasks-{task_count}_chunks-{chunk_count}"
    case_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(case_dir),
        settings=Settings(anonymized_telemetry=False),
    )

    filtered = client.get_or_create_collection("bench_filtered")
    per_task = [client.get_or_create_collection(f"bench_task_{index}") for index in range(task_count)]

    insert_started = time.perf_counter()
    for task_index in range(task_count):
        add_task_chunks(filtered, task_index, task_count, chunk_count)
        add_task_chunks(per_task[task_index], task_index, task_count, chunk_count)
    insert_seconds = time.perf_counter() - insert_started

    sample_tasks = sorted(
        set(round(index * (task_count - 1) / max(min(task_count, 10) - 1, 1))
            for index in range(min(task_count, 10)))
    )
    target_chunk = chunk_count // 2
    filtered_latencies = []
    per_task_latencies = []
    parity_hits = 0
    query_count = 0
    query_errors = []

    for _ in range(repeats):
        for task_index in sample_tasks:
            query_embedding = embedding(task_index, target_chunk, task_count, chunk_count)
            started = time.perf_counter()
            filtered_result = filtered.query(
                query_embeddings=[query_embedding],
                n_results=5,
                where={"task_id": f"task-{task_index}"},
                include=["distances", "metadatas"],
            )
            filtered_latencies.append((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            try:
                isolated_result = per_task[task_index].query(
                    query_embeddings=[query_embedding],
                    n_results=5,
                    include=["distances", "metadatas"],
                )
            except Exception as exc:
                query_errors.append(
                    {"task_index": task_index, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            per_task_latencies.append((time.perf_counter() - started) * 1000)

            filtered_ids = filtered_result.get("ids", [[]])[0]
            isolated_ids = isolated_result.get("ids", [[]])[0]
            parity_hits += int(filtered_ids == isolated_ids)
            query_count += 1

    open_started = time.perf_counter()
    collection_count = len(client.list_collections())
    open_all_collections_ms = (time.perf_counter() - open_started) * 1000
    result = {
        "task_count": task_count,
        "chunks_per_task": chunk_count,
        "stored_vectors": task_count * chunk_count * 2,
        "insert_seconds": round(insert_seconds, 3),
        "metadata_filter": {
            "mean_ms": round(mean(filtered_latencies), 3),
            "p50_ms": round(percentile(filtered_latencies, 0.5), 3),
            "p95_ms": round(percentile(filtered_latencies, 0.95), 3),
        },
        "per_task_collection": {
            "mean_ms": round(mean(per_task_latencies), 3) if per_task_latencies else None,
            "p50_ms": round(percentile(per_task_latencies, 0.5), 3),
            "p95_ms": round(percentile(per_task_latencies, 0.95), 3),
        },
        "top5_exact_parity": round(parity_hits / query_count, 4) if query_count else None,
        "collection_count": collection_count,
        "open_all_collections_ms": round(open_all_collections_ms, 3),
        "disk_bytes": directory_size(case_dir),
        "query_count": query_count,
        "query_errors": query_errors,
    }
    client._system.stop()
    SharedSystemClient.clear_system_cache()
    del per_task, filtered, client
    gc.collect()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("smoke", "plan"), default="smoke")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/chroma-isolation-benchmark-smoke.json"),
    )
    args = parser.parse_args()
    cases = (
        [(1, 100), (10, 100), (100, 100), (1, 1000), (10, 1000)]
        if args.profile == "smoke"
        else [(tasks, chunks) for tasks in (1, 10, 100) for chunks in (100, 1000, 5000)]
    )

    with tempfile.TemporaryDirectory(
        prefix="writing-rag-benchmark-", ignore_cleanup_errors=True
    ) as temp_dir:
        base_dir = Path(temp_dir)
        results = [run_case(tasks, chunks, args.repeats, base_dir) for tasks, chunks in cases]

    report = {
        "profile": args.profile,
        "chroma_version": chromadb.__version__,
        "repeats": args.repeats,
        "embedding_dimension": 8,
        "synthetic": True,
        "production_strategy_changed": False,
        "limitations": [
            "Smoke profile is not the complete plan matrix.",
            "Collection-open timing is a warm-process metadata operation, not a cold OS startup.",
            "Synthetic exact-neighbor parity verifies filter semantics, not production embedding quality.",
        ],
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
