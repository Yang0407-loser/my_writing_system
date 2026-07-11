import time
import requests
from .base import EmbeddingProvider


class OllamaEmbeddingProvider(EmbeddingProvider):
    """使用 Ollama 本地服务的 embedding 提供商。

    前置条件：Ollama 已安装并运行，已拉取对应模型（如 ollama pull bge-m3）。
    """

    def __init__(
        self,
        model_name: str = "bge-m3:latest",
        base_url: str = "http://localhost:11434",
        max_retries: int = 2,
    ):
        self._model_name = model_name
        self._base_url = base_url.rstrip("/")
        self._api_url = f"{self._base_url}/api/embeddings"
        self._max_retries = max_retries

        # 验证连接并获取维度
        try:
            test = self.embed("test")
            self._dim = len(test)
        except Exception as e:
            raise RuntimeError(
                f"无法连接 Ollama 服务 ({self._base_url})。"
                f"请确保 Ollama 已启动，并且已拉取模型 '{model_name}'。"
                f"原始错误: {e}"
            )

    def embed(self, text: str) -> list[float]:
        last_err = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = requests.post(
                    self._api_url,
                    json={"model": self._model_name, "prompt": text},
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()["embedding"]
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                if attempt < self._max_retries:
                    time.sleep(2 ** attempt)
            except Exception:
                raise
        raise RuntimeError(f"Ollama 请求失败 (重试{self._max_retries}次后): {last_err}")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name
