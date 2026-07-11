import uuid
import logging
import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.api.types import EmbeddingFunction
from .embedding.factory import get_embedding_provider
from .config import settings

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

    def add_text(self, text: str, metadata: dict) -> None:
        doc_id = str(uuid.uuid4())
        self._collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )

    def search(self, query: str, k: int = 5, task_id: str | None = None) -> list[str]:
        """检索最相似的 k 个文本块，可按任务 ID 过滤。"""
        where_filter = None
        if task_id:
            where_filter = {"task_id": task_id}

        n = min(k, self._collection.count())
        if n == 0:
            return []

        results = self._collection.query(
            query_texts=[query],
            n_results=n,
            where=where_filter,
        )
        if results and results.get("documents") and results["documents"][0]:
            return results["documents"][0]
        return []

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
            # 删除最旧的块（UUID 按时间排序不可靠，按存储顺序删除）
            excess = len(ids) - self.MAX_CHUNKS_PER_TASK
            ids_to_delete = ids[:excess]
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
