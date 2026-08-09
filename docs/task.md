# 项目：多智能体协作写作系统（MVP）- 默认使用 BGE-M3 Embedding + DeepSeek V4 Pro

请生成一个完整的 Python 项目，使用 `uv` 管理依赖。项目实现一个最小可行版的多智能体协作写作系统，核心功能：用户提供写作主题和一段风格参考文本 → 系统提取风格 → 生成大纲 → 撰写短文（约 500 字）→ 审阅并输出最终结果。后续可扩展情节管理、长文本一致性等。

**特别要求**：
1. 系统必须支持检索增强生成（RAG），默认使用 **BGE-M3** 作为 embedding 模型（通过 `sentence-transformers` 或 `FlagEmbedding` 加载），并且 embedding 提供商支持通过环境变量切换（至少支持本地 `sentence_transformers` 和 OpenAI）。
2. **默认 LLM 模型改为 DeepSeek V4 Pro**（使用 OpenAI 兼容接口），不依赖 ChatGPT 订阅。

## 技术栈要求
- **API 网关**：FastAPI
- **异步任务队列**：Celery + Redis（作为 broker 和结果后端）
- **状态黑板**：Redis Hash
- **向量存储**：Chroma（本地），需要支持自定义 embedding 函数
- **Embedding 抽象层**：
  - 默认实现：使用 `sentence_transformers` 加载 `BAAI/bge-m3` 模型
  - 可选的实现：OpenAI Embeddings（`text-embedding-3-small` 等）
  - 架构应便于日后增加 `FlagEmbedding` 或 `Ollama` 提供商
- **LLM 调用**：使用 OpenAI 兼容库，默认模型 `deepseek-v4-pro`，base_url 可配置（例如 `https://api.deepseek.com/v1`），API key 从环境变量读取。
- **包管理**：`uv`（生成 `pyproject.toml` 和 `uv.lock`）
- **Python 版本**：3.11+

## 项目结构（生成完整代码）
my_writing_system/
├── pyproject.toml
├── README.md
├── .env.example
├── docker-compose.yml (用于启动 Redis)
├── app/
│ ├── init.py
│ ├── main.py # FastAPI 应用入口
│ ├── config.py # 环境变量与配置
│ ├── models.py # Pydantic 模型（请求/响应）
│ ├── celery_app.py # Celery 应用实例
│ ├── coordinator.py # 协调中心（任务编排）
│ ├── agents/
│ │ ├── init.py
│ │ ├── base.py # Agent 基类
│ │ ├── style_analyzer.py # 风格分析器
│ │ ├── planner.py # 规划师
│ │ ├── writer.py # 撰稿人
│ │ ├── reviewer.py # 审阅者
│ ├── embedding/ # Embedding 抽象层
│ │ ├── init.py
│ │ ├── base.py # EmbeddingProvider ABC
│ │ ├── sentence_transformer_provider.py # 使用 BGE-M3
│ │ ├── openai_provider.py # OpenAI 实现
│ │ └── factory.py # 根据环境变量获取 provider
│ ├── blackboard.py # Redis 黑板操作
│ ├── vector_store.py # Chroma 封装（使用 factory 获取的 provider）
│ └── utils/
│ ├── init.py
│ ├── llm_client.py # LLM 调用封装（DeepSeek V4 Pro）
│ └── prompt_templates.py # 提示词模板
└── tests/
└── test_basic.py

text

## 功能流程（必须实现）
1. **用户请求**：`POST /write` 接收 `{ "topic": "...", "reference_text": "..." }`，返回 `task_id`。
2. **异步任务**：Celery 任务 `writing_task` 按照顺序调用：
   - **风格分析**：分析 `reference_text`，提取风格特征（JSON 格式，包含：热血程度 0-100、伤痛程度 0-100、平均句长、形容词密度等），并将风格特征存入黑板（key: `task_id:style`）。
   - **规划**：根据主题和风格特征生成大纲（3 个小节，每节要点），存入黑板（`task_id:outline`）。
   - **撰写**：根据大纲和风格特征，逐节生成正文（总字数约 500 字）。**撰写时从向量库检索历史段落**（检索增强）：每写完一个段落，将其存入向量库；写下一段前，检索与当前主题相关的已写段落，将检索结果加入 LLM 上下文，防止前后矛盾。向量存储使用 Chroma，embedding 通过 factory 获取的 provider 实现（默认使用 BGE-M3）。
   - **审阅**：对生成的整篇初稿进行评分（1-10 分）和简短建议，存入黑板（`task_id:review`）。
   - 最终将初稿和审阅结果返回给用户（通过 Celery 结果后端或单独查询接口）。
3. **状态查询**：`GET /status/{task_id}` 返回当前任务状态和中间结果。

## LLM 配置具体要求
- 在 `utils/llm_client.py` 中封装 OpenAI 兼容客户端：
  - 从环境变量读取：
    - `LLM_API_KEY`（必需，用户自己的 DeepSeek API key）
    - `LLM_BASE_URL`（默认 `https://api.deepseek.com/v1`）
    - `LLM_MODEL`（默认 `deepseek-v4-pro`）
  - 提供 `chat_completion(messages, temperature=0.7, max_tokens=2000)` 方法。
- 所有智能体调用 LLM 时统一使用该客户端。

## Embedding 切换具体要求
- 默认使用 `sentence_transformers` 提供商，模型为 `BAAI/bge-m3`。
- 如果 `EMBEDDING_PROVIDER` 设置为 `openai`，则使用 OpenAI 的 embedding 模型。
- 代码必须保证切换提供商时，只需修改环境变量，不需要改动业务逻辑。
- 在 `sentence_transformer_provider.py` 中，加载模型的方式：
  ```python
  from sentence_transformers import SentenceTransformer
  model = SentenceTransformer("BAAI/bge-m3")
如果用户的环境无法自动下载模型，应当给出清晰的错误提示，建议手动下载或使用 trust_remote_code=True 等选项。

可选支持 FlagEmbedding（暂不要求，但预留注释）。

环境变量配置（.env.example 内容）
env
# ############ LLM 配置（DeepSeek V4 Pro） ############
LLM_API_KEY=your_deepseek_api_key_here
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-pro

# ############ Embedding 模型配置 ############
# 可选: 'sentence_transformers' (默认，使用 BGE-M3), 'openai'
EMBEDDING_PROVIDER=sentence_transformers
# 当使用 sentence_transformers 时，可以指定模型名称（默认 BAAI/bge-m3）
EMBEDDING_MODEL=BAAI/bge-m3
# 当使用 openai 时，需要配置 OPENAI_API_KEY 和 OPENAI_BASE_URL
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
依赖项（pyproject.toml 必须包含）
fastapi

celery

redis

chromadb

openai（用于 LLM 客户端和可选的 embedding）

sentence-transformers（用于 BGE-M3 加载）

python-dotenv

uvicorn

pydantic

requests（测试用）

其他实现要求
所有提示词模板放在 prompt_templates.py 中，撰写 prompt 必须包含检索到的历史段落上下文占位符。

黑板操作使用 Redis Hash，实现 set(task_id, key, value) 和 get(task_id, key)。

实现错误处理和重试（LLM 调用失败重试 2 次）。

提供 docker-compose.yml 用于启动 Redis。

测试脚本 tests/test_basic.py 应当打印使用的 embedding 模型信息（例如 BGE-M3）和 LLM 模型。

运行方式（在 README.md 中写明）
复制 .env.example 为 .env，填写 DeepSeek API key。

使用 uv sync 安装依赖（会自动下载 BGE-M3 模型，首次运行可能需要几分钟）。

启动 Redis：docker-compose up -d

启动 Celery worker：celery -A app.celery_app worker --loglevel=info

启动 FastAPI：uvicorn app.main:app --reload

运行测试：python tests/test_basic.py

输出要求
生成所有文件的完整代码。确保：

默认 embedding 使用 BGE-M3，并且能正常工作在 CPU 或 GPU 环境（sentence-transformers 会自动检测）。

代码中包含适当的注释，解释 RAG 在长文本一致性中的作用，以及预留情节管理的扩展点。

向量检索部分真实调用 Chroma 的相似度搜索，不要 mock。