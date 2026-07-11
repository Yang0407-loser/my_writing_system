from .base import EmbeddingProvider


class SentenceTransformerProvider(EmbeddingProvider):
    """使用 sentence-transformers 加载 BGE-M3 模型的 embedding 提供商。"""

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self._model_name = model_name
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
        except Exception as e:
            raise RuntimeError(
                f"无法加载 SentenceTransformer 模型 '{model_name}'。"
                f"请检查网络连接，或手动下载模型到本地后指定路径。"
                f"原始错误: {e}"
            )

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 10,
        )
        return embeddings.tolist()

    @property
    def dimension(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    @property
    def model_name(self) -> str:
        return self._model_name
