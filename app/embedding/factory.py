from .base import EmbeddingProvider
from ..config import settings


def get_embedding_provider() -> EmbeddingProvider:
    """根据环境变量 EMBEDDING_PROVIDER 创建对应的 embedding 提供商。

    支持的值:
        - 'sentence_transformers' (默认): 使用 BGE-M3 本地模型
        - 'openai': 使用 OpenAI text-embedding-3-small

    扩展方式:
        在此函数中添加新的 elif 分支即可，例如未来可支持:
        - 'flag_embedding': 使用 FlagEmbedding 加载 BGE-M3
        - 'ollama': 使用 Ollama 本地 embedding 服务
    """
    provider_name = settings.EMBEDDING_PROVIDER.lower()

    if provider_name == "sentence_transformers":
        from .sentence_transformer_provider import SentenceTransformerProvider
        return SentenceTransformerProvider(model_name=settings.EMBEDDING_MODEL)

    elif provider_name == "openai":
        from .openai_provider import OpenAIEmbeddingProvider
        return OpenAIEmbeddingProvider(
            model_name=settings.EMBEDDING_MODEL or "text-embedding-3-small",
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
        )

    elif provider_name == "ollama":
        from .ollama_provider import OllamaEmbeddingProvider
        return OllamaEmbeddingProvider(
            model_name=settings.EMBEDDING_MODEL or "bge-m3:latest",
            base_url=settings.OLLAMA_BASE_URL,
        )

    # 预留扩展点
    # elif provider_name == "flag_embedding":
    #     from .flag_embedding_provider import FlagEmbeddingProvider
    #     return FlagEmbeddingProvider(model_name=settings.EMBEDDING_MODEL)

    else:
        raise ValueError(
            f"不支持的 embedding 提供商: '{provider_name}'。"
            f"可选值: 'sentence_transformers', 'openai', 'ollama'"
        )
