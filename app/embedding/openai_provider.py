from .base import EmbeddingProvider


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """使用 OpenAI 兼容接口的 embedding 提供商。"""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
    ):
        self._model_name = model_name
        self._api_key = api_key
        self._base_url = base_url

        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url)

        # OpenAI text-embedding-3-small 维度为 1536
        self._dim = 1536

    def embed(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(
            model=self._model_name,
            input=text,
        )
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(
            model=self._model_name,
            input=texts,
        )
        # OpenAI 返回顺序与输入顺序一致
        return [d.embedding for d in resp.data]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name
