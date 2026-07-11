from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Embedding 提供商抽象基类。所有提供商必须实现 embed 和 embed_batch 方法。"""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """将单个文本转换为向量。"""
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量将文本转换为向量。"""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回 embedding 维度。"""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回当前使用的模型名称。"""
        ...
