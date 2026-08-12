import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.api.types import EmbeddingFunction
from .embedding.factory import get_embedding_provider
from .config import settings
from .context_contracts import serialize_chroma_metadata

logger = logging.getLogger(__name__)


class _ChromaEmbedFn(EmbeddingFunction):
    def __init__(self, provider):
        self._provider = provider

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._provider.embed_batch(input)


class VectorStore:
    """基于 Chroma 的向量存储封装。

    用于存储已写文本块，支持 RAG 检索增强。
    长文本模式下，每小节被切分为多个小块（~500 字）存储。
    """

    # 每个 task_id 最多保留的向量块数（超出时清理旧块）
    MAX_CHUNKS_PER_TASK = 5000

    def __init__(self):
        self._provider = get_embedding_provider()
        self._client = chromadb.PersistentClient(
            path=settings.CHROMA_DATA_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection_name = "writing_paragraphs"
        self._embed_fn = _ChromaEmbedFn(self._provider)
        self._last_search_trace: dict = {}
        self._ensure_collection()

    def _ensure_collection(self):
        existing = self._client.list_collections()
        # ChromaDB 0.5.x returns list[str], 0.6.x returns list[Collection]
        names = []
        for item in existing:
            try:
                names.append(item.name)
            except AttributeError:
                names.append(str(item))
        if self._collection_name in names:
            self._collection = self._client.get_collection(
                name=self._collection_name,
                embedding_function=self._embed_fn,
            )
        else:
            self._collection = self._client.create_collection(
                name=self._collection_name,
                embedding_function=self._embed_fn,
            )

    def add_text(
        self, text: str, metadata: dict, *, document_id: str | None = None
    ) -> str | None:
        """Add one non-empty, task-scoped chunk and skip exact duplicates.

        Returns the stored/existing document ID.  Empty chunks return ``None``
        and are logged with the task and section so ingestion failures are
        observable instead of becoming invalid Chroma records.
        """
        normalized_text = text.strip() if isinstance(text, str) else ""
        task_id = str(metadata.get("task_id", "")) if isinstance(metadata, dict) else ""
        section = metadata.get("section", 0) if isinstance(metadata, dict) else 0
        if not normalized_text:
            logger.warning(
                "RAG ingestion skipped empty text: task=%s section=%s feature=story_chunk fallback=skip",
                task_id or "-", section,
            )
            return None

        content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        safe_metadata = serialize_chroma_metadata(dict(metadata or {}))
        safe_metadata.setdefault("content_hash", content_hash)
        safe_metadata.setdefault("source_version", 1)
        safe_metadata.setdefault("created_at", datetime.now(timezone.utc).isoformat())

        hash_filter: dict = {"content_hash": content_hash}
        if task_id:
            hash_filter = {
                "$and": [
                    {"task_id": task_id},
                    {"content_hash": content_hash},
                ]
            }
        if document_id:
            # Canonical projection IDs are semantic identities, not suggestions.
            # Always exact-upsert this ID; legacy content-hash dedupe below is
            # intentionally reserved for callers that did not provide one.
            self._collection.upsert(
                ids=[document_id],
                documents=[normalized_text],
                metadatas=[safe_metadata],
            )
            return document_id

        existing = self._collection.get(where=hash_filter, limit=1)
        existing_ids = existing.get("ids", []) if existing else []
        if existing_ids:
            logger.info(
                "RAG ingestion skipped duplicate: task=%s section=%s document_id=%s",
                task_id or "-", section, existing_ids[0],
            )
            return existing_ids[0]

        doc_id = document_id or str(uuid.uuid4())
        self._collection.add(
            ids=[doc_id],
            documents=[normalized_text],
            metadatas=[safe_metadata],
        )
        return doc_id

    def search(self, query: str, k: int = 5, task_id: str | None = None) -> list[str]:
        """检索最相似的 k 个文本块，可按任务 ID 过滤。"""
        return [item["text"] for item in self.search_with_meta(query, k=k, task_id=task_id)]

    def search_with_meta(
        self,
        query: str,
        k: int = 5,
        task_id: str | None = None,
        candidate_k: int | None = None,
    ) -> list[dict]:
        """检索最相似的 k 个文本块，返回文档文本及元数据。

        ``candidate_k`` enables observation of a larger coarse-recall set while
        preserving the legacy top-k result returned to Writer.  Its default is
        exactly ``k``, so production retrieval behavior is unchanged.

        Returns:
            Items include document ID, distance/score, complete metadata,
            applied filter, rank and query latency in addition to legacy keys.
            向后兼容: 不存在的 metadata 字段填默认值
        """
        where_filter = None
        if task_id:
            where_filter = {"task_id": task_id}

        # Reranking needs a wider coarse set to have anything to reorder. When
        # the flag is off this stays exactly the legacy candidate size.
        rerank_on = bool(getattr(settings, "RAG_RERANKER_ENABLED", False))
        requested_candidates = max(k, candidate_k or k)
        if rerank_on:
            requested_candidates = max(
                requested_candidates, int(settings.RAG_RERANKER_CANDIDATE_K)
            )
        n = min(requested_candidates, self._collection.count())
        if n == 0:
            self._last_search_trace = {
                "query": query,
                "filter": where_filter,
                "elapsed_ms": 0.0,
                "candidate_count": 0,
                "returned_count": 0,
                "candidates": [],
            }
            return []

        started = time.perf_counter()
        results = self._collection.query(
            query_texts=[query],
            n_results=n,
            where=where_filter,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        out = []
        if results and results.get("documents") and results["documents"][0]:
            docs = results["documents"][0]
            metas = results.get("metadatas", [[]])[0] if results.get("metadatas") else []
            ids = results.get("ids", [[]])[0] if results.get("ids") else []
            distances = results.get("distances", [[]])[0] if results.get("distances") else []
            for i, doc in enumerate(docs):
                meta = metas[i] if i < len(metas) and metas[i] else {}
                distance = distances[i] if i < len(distances) else None
                score = None
                if isinstance(distance, (int, float)):
                    score = round(1.0 / (1.0 + max(float(distance), 0.0)), 6)
                out.append({
                    "id": ids[i] if i < len(ids) else "",
                    "text": doc,
                    "section": meta.get("section", 0),
                    "subsection": meta.get("subsection", 0),
                    "title": meta.get("title", ""),
                    "distance": distance,
                    "score": score,
                    "metadata": dict(meta),
                    "filter": where_filter,
                    "elapsed_ms": elapsed_ms,
                    "rank": i + 1,
                })
        self._last_search_trace = {
            "query": query,
            "filter": where_filter,
            "elapsed_ms": elapsed_ms,
            "candidate_count": len(out),
            "returned_count": min(k, len(out)),
            "candidates": [
                {
                    "id": item["id"],
                    "rank": item["rank"],
                    "distance": item["distance"],
                    "score": item["score"],
                    "metadata": item["metadata"],
                }
                for item in out
            ],
        }
        if rerank_on and out:
            # Fail-open by construction: rerank_items never raises and returns
            # the legacy slice on any provider error, timeout or degradation.
            try:
                from .reranker import rerank_items

                selected, rerank_trace = rerank_items(query, out, k)
            except Exception as error:  # import-level failure only
                logger.warning("RAG reranker unavailable: %s", error, exc_info=True)
                self._last_search_trace["rerank"] = {
                    "applied": False,
                    "reason": f"import_failed:{type(error).__name__}",
                }
                return out[:k]
            self._last_search_trace["rerank"] = rerank_trace
            self._last_search_trace["returned_count"] = len(selected)
            return selected
        return out[:k]

    @property
    def last_search_trace(self) -> dict:
        """A copy of the most recent coarse/final retrieval trace."""
        return dict(self._last_search_trace)

    def cleanup_task(self, task_id: str) -> int:
        """删除指定 task_id 的所有向量块。返回删除数量。

        在任务完成或被删除时调用，防止 ChromaDB 无限增长。
        """
        try:
            where_filter = {"task_id": task_id}
            count = self._collection.count()
            if count == 0:
                return 0
            # 获取该 task 的所有 doc ids
            results = self._collection.get(where=where_filter)
            ids_to_delete = results.get("ids", [])
            if ids_to_delete:
                self._collection.delete(ids=ids_to_delete)
                logger.info(f"ChromaDB: 已清理 task={task_id[:8]} 的 {len(ids_to_delete)} 个向量块")
            return len(ids_to_delete)
        except Exception as e:
            logger.warning(f"ChromaDB 清理 task={task_id[:8]} 失败: {e}", exc_info=True)
            return 0

    @staticmethod
    def _canonical_where(*, tenant_id: str, project_id: str, task_id: str) -> dict:
        if not tenant_id or not project_id or not task_id:
            raise ValueError("tenant_id, project_id and task_id are required")
        return {
            "$and": [
                {"tenant_id": tenant_id},
                {"project_id": project_id},
                {"task_id": task_id},
            ]
        }

    def list_canonical_chunks(
        self, *, tenant_id: str, project_id: str, task_id: str
    ) -> tuple[dict, ...]:
        """Enumerate only chunks carrying the complete Canon scope metadata."""
        result = self._collection.get(
            where=self._canonical_where(
                tenant_id=tenant_id, project_id=project_id, task_id=task_id
            ),
            include=["documents", "metadatas"],
        )
        ids = result.get("ids", []) or []
        documents = result.get("documents", []) or []
        metadatas = result.get("metadatas", []) or []
        return tuple(
            {
                "record_id": record_id,
                "text": documents[index] if index < len(documents) else "",
                "metadata": dict(metadatas[index] or {})
                if index < len(metadatas)
                else {},
            }
            for index, record_id in enumerate(ids)
        )

    def delete_canonical_chunks(
        self, *, tenant_id: str, project_id: str, task_id: str
    ) -> int:
        rows = self.list_canonical_chunks(
            tenant_id=tenant_id, project_id=project_id, task_id=task_id
        )
        ids = [row["record_id"] for row in rows]
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    def enforce_task_limit(self, task_id: str) -> int:
        """确保单个 task 的向量块不超过 MAX_CHUNKS_PER_TASK。
        超出时删除最旧的块。返回删除数量。
        """
        try:
            where_filter = {"task_id": task_id}
            results = self._collection.get(where=where_filter)
            ids = results.get("ids", [])
            if len(ids) <= self.MAX_CHUNKS_PER_TASK:
                return 0
            metadatas = results.get("metadatas", []) or []
            excess = len(ids) - self.MAX_CHUNKS_PER_TASK
            indexed = list(enumerate(ids))
            indexed.sort(
                key=lambda pair: (
                    str(metadatas[pair[0]].get("created_at", ""))
                    if pair[0] < len(metadatas) and metadatas[pair[0]] else "",
                    pair[0],
                )
            )
            ids_to_delete = [doc_id for _, doc_id in indexed[:excess]]
            self._collection.delete(ids=ids_to_delete)
            logger.info(f"ChromaDB: task={task_id[:8]} 超出限制，已清理 {len(ids_to_delete)} 个旧块")
            return len(ids_to_delete)
        except Exception as e:
            logger.warning(f"ChromaDB 限制检查 task={task_id[:8]} 失败: {e}", exc_info=True)
            return 0

    @property
    def provider_name(self) -> str:
        return self._provider.model_name

    @property
    def embedding_dimension(self) -> int:
        return self._provider.dimension
