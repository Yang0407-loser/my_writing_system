# 多智能体协作写作系统 — 调试与设计问题报告

> 最后更新: 2026-05-19

## 环境
- OS: Windows 11
- Python: 3.11+
- 包管理: uv
- LLM: DeepSeek V4 Pro (OpenAI 兼容接口, base_url=https://api.deepseek.com/v1)
- Embedding: BGE-M3 via Ollama
- 向量库: Chroma
- 任务队列: Celery + Redis

---

# 一、历史问题（已解决）

---

## 问题 1: start.bat 中文乱码 → 命令截断

**现象**: 双击 start.bat 后，CMD 窗口输出乱码，命令被截断执行 (`start notepad` 变成 `otepad`，`celery` 变成 `lery`)。

**原因**: 批处理文件包含 UTF-8 中文字符，CMD 在 `chcp 65001` 生效前就开始解析文件内容，多字节字符被拆成单个字节，导致命令边界错乱。

**解决**: 将 start.bat 改为纯 ASCII 英文，中文注释全部移除。同时提供 PowerShell 版 start.ps1 作为替代（PowerShell 原生支持 UTF-8）。

**文件**: `start.bat`, `start.ps1`

---

## 问题 2: Docker 未安装 → Redis 无法启动

**现象**: `[ERROR] Failed to start Docker. Is Docker Desktop running?`

**原因**: `E:/Docker` 目录为空，系统中未安装 Docker Desktop。

**解决**: 不使用 Docker。从 GitHub 下载 Windows 原生 Redis (tporadowski/redis v5.0.14.1) 到 `E:/Redis/`，修改 start.bat 直接调用 `redis-server.exe`。

**文件**: `start.bat`

---

## 问题 3: uv sync 失败 — hatchling 找不到包目录

**现象**: `ValueError: Unable to determine which files to ship inside the wheel...`

**原因**: `pyproject.toml` 中项目名为 `my-writing-system`，hatch 默认寻找名为 `my_writing_system` 的目录，但实际代码在 `app/` 目录下。

**解决**: 在 `pyproject.toml` 中添加 `[tool.hatch.build.targets.wheel]` 配置，显式指定 `packages = ["app", "tests"]`。

**文件**: `pyproject.toml`

---

## 问题 4: Celery worker 启动后任务一直 pending

**现象**: Web UI 提交任务成功 (status: pending)，但永远不进入 running 状态。

**原因**: `celery_app.py` 只创建了 Celery 实例，但没有导入 `coordinator.py`。`@celery_app.task` 装饰器未被触发注册。

**解决**: 在 `celery_app.py` 末尾添加 `from . import coordinator`。

**文件**: `app/celery_app.py`

---

## 问题 5: KeyError — JSON 模板花括号被 Python str.format() 误解析

**现象**: `KeyError: '\n "intensity"'`，任务 failed。

**原因**: `STYLE_ANALYSIS_PROMPT` 模板中包含原始 JSON 示例，Python 的 `str.format()` 把 `{` `}` 当作占位符标记。

**解决**: 将所有 prompt 模板中的字面花括号统一转义为 `{{` 和 `}}`，仅保留真正的 format 占位符为单花括号。

**文件**: `app/utils/prompt_templates.py`

---

## 问题 6: HuggingFace 不可达 → BGE-M3 模型加载失败

**现象**: `无法加载 SentenceTransformer 模型 'BAAI/bge-m3'。We couldn't connect to 'https://huggingface.co'...`

**原因**: 默认 provider `sentence_transformers` 需要从 HuggingFace 下载 BGE-M3 模型（约 2.2GB），但网络环境无法访问 huggingface.co。

**解决**: 新增 Ollama embedding provider，通过 Ollama 的 `/api/embeddings` 接口获取向量。

**文件**: `app/embedding/ollama_provider.py`, `app/embedding/factory.py`, `.env`

---

## 问题 7: Chroma embedding function 类型错误

**现象**: `'function' object has no attribute 'name'`

**原因**: ChromaDB 的 `embedding_function` 参数要求实现 `EmbeddingFunction` 协议的对象（需具备 `name` 属性和 `__call__` 方法），不能直接传入裸 Python 函数。

**解决**: 创建 `_ChromaEmbedFn` 类继承 `chromadb.api.types.EmbeddingFunction`，包装 `EmbeddingProvider`。

**文件**: `app/vector_store.py`

---

## 问题 8: DeepSeek V4 Pro 推理模式导致 JSON 解析失败 ★ 核心问题

**现象**: `风格分析 JSON 解析失败。原始响应: 我们被要求分析文本。文本是： "风卷起满地的落叶..." 先数句子和词数...`

**原因**: DeepSeek V4 Pro 是推理模型，默认开启 chain-of-thought 思考模式。模型会先生成冗长的"思考过程"，然后才输出 JSON。但 `max_tokens` 远不够同时容纳思考过程和最终答案，JSON 还没开始写就被截断了。

**解决**:
1. API 调用中添加 `extra_body={"thinking": {"type": "disabled"}}` 关闭推理模式
2. 移除 `reasoning_content` 作为内容 fallback 的逻辑
3. 新增 `LLM 返回空内容` 的错误处理

**文件**: `app/utils/llm_client.py`

---

# 二、当前设计问题与 Bug（待修复）

---

## P0 — 严重：影响核心功能正确性

---

### Bug P0-1: Writer.run() 是死代码 — 核心写作逻辑存在双重实现

**涉及文件**:
- [app/agents/writer.py](app/agents/writer.py#L25-L284) — `Writer.run()` (284 行)
- [app/coordinator.py](app/coordinator.py#L329-L491) — `_phase_writing()` 
- [app/coordinator.py](app/coordinator.py#L676-L812) — `_write_subsection()`

**描述**:

`Writer.run()` 是一套完整的继承制写作实现（包含角色上下文注入、交接笔记解析、RAG 检索、续写/精简、切块入库、角色状态更新），共 284 行。但 `coordinator.py` 的 `_phase_writing()` 完全不调用 `writer.run()`，而是自己逐节逐小节循环，调用 `_write_subsection()`。

两条路径实现了大量重复逻辑：
| 功能 | Writer.run() (writer.py) | _write_subsection() (coordinator.py) |
|------|--------------------------|--------------------------------------|
| RAG 检索 | 第 84-92 行 | 第 697-703 行 |
| 角色上下文 | 第 95-96 行 | 第 706-710 行 |
| 交接笔记上下文 | 第 99-106 行 | 第 713-720 行 |
| Prompt format | 第 113-131 行 | 第 728-745 行 |
| 流式/非流式 | 第 140-159 行 | 第 754-765 行 |
| 交接笔记解析 | 第 162-164 行 | 第 768 行 |
| 续写逻辑 | 第 167-195 行 | 第 772-793 行 |
| 精简逻辑 | 第 198-208 行 | 第 796-803 行 |
| 切块入库 | 第 225-234 行 | coordinator line 440 |
| 角色状态更新 | 第 262-269 行 | coordinator line 457-462 |

**影响**:
- `Writer.run()` 中的 prompt format 缺少 `{world_setting}` 和 `{sub_description}` 参数，如果被调用会直接 `KeyError`（见 P1-2）
- 在一个路径修 bug 不会在另一路径生效
- coordinator 路径使用 `style.get("emotion_intensity")` 同时赋值给 intensity 和 melancholy（见 P0-2）

**修复方向**:
删除 `_write_subsection()` 和 coordinator 中的手动循环，改为调用 `Writer.run()`。或者在 `Writer.run()` 中修复所有参数传递问题后，将 coordinator 改为委托给 `Writer.run()`。

---

### Bug P0-2: 50 维风格模型字段全局未迁移

**涉及文件**:
- [app/models.py](app/models.py#L76-L133) — `StyleProfile` 定义 (50 维)
- [app/agents/style_analyzer.py](app/agents/style_analyzer.py#L151-L157) — `_fill_defaults()`
- [app/agents/planner.py](app/agents/planner.py#L28-L33) — `generate_outline()` 使用旧字段
- [app/agents/reviewer.py](app/agents/reviewer.py#L14-L16) — 使用旧字段
- [app/agents/reviewer.py](app/agents/reviewer.py#L47-L49) — `review_global()` 使用旧字段
- [app/agents/writer.py](app/agents/writer.py#L120-L124) — `run()` 使用旧字段
- [app/agents/writer.py](app/agents/writer.py#L357-L358) — `suggest_refinements()` 使用旧字段
- [app/agents/writer.py](app/agents/writer.py#L411-L412) — `refine_section()` 使用旧字段
- [app/agents/style_analyzer.py](app/agents/style_analyzer.py#L199-L200) — `review_outline()` 混合新旧
- [app/coordinator.py](app/coordinator.py#L735-L738) — `_write_subsection()` 使用错误映射
- [app/utils/prompt_templates.py](app/utils/prompt_templates.py#L82-L88) — `PLANNING_PROMPT` 使用旧占位符
- [app/utils/prompt_templates.py](app/utils/prompt_templates.py#L160-L184) — `WRITING_PROMPT` 部分使用旧字段

**描述**:

系统从 6 个旧风格字段升级到 50 维 `StyleProfile` 模型，但大量消费者代码仍在读取旧字段名。旧模型 vs 新模型字段对照：

| 旧字段 (被使用) | 新 50 维对应字段 | 状态 |
|-----------------|------------------|------|
| `intensity` | `emotion_intensity` | 大部分代码未迁移 |
| `melancholy` | 不存在（需从 `emotion_blend` 提取或用 `primary_emotion` 判断） | 无对应字段 |
| `avg_sentence_length` | 不存在（新模型按比例分 short/medium/long） | 无对应字段 |
| `adjective_density` | `adjective_density` | 字段名一致，但其他代码不匹配 |
| `tone` | `style_brief` (自然语言简报) | 语义不同 |
| `features` | 不存在 | 无对应字段 |

**具体问题**:

1. **[coordinator.py:735-738](app/coordinator.py#L735-L738)**: 
   ```python
   intensity=style.get("emotion_intensity", 50),
   melancholy=style.get("emotion_intensity", 50),  # ← 伤痛=热血，语义完全错误
   avg_sentence_length=style.get("paragraph_length_avg", 20),  # ← 段落长度≠句长
   ```
   伤痛程度和热血程度取了同一个值，段落平均字数被当作句长。

2. **[planner.py:28-33](app/planner.py#L28-L33)**: `generate_outline()` 传入 `intensity`, `melancholy`, `avg_sentence_length`, `adjective_density`, `tone`, `features` — 这些从 `style` dict 取，LLM 返回的 50 维 profile 中没有这些字段，全部为默认值 50/20/0.1/""。

3. **[reviewer.py:15-16](app/reviewer.py#L15-L16)** 和 **[reviewer.py:48-49](app/reviewer.py#L48-L49)**: `intensity`, `melancholy`, `avg_sentence_length` — 同上，始终使用默认值。

4. **[prompt_templates.py:82-88](app/utils/prompt_templates.py#L82-L88)** `PLANNING_PROMPT`:
   ```
   热血程度：{intensity}/100
   伤痛程度：{melancholy}/100
   平均句长：{avg_sentence_length}
   ```
   当 Planner 传入默认值时，计划大纲看到的永远是 "热血程度：50/100，伤痛程度：50/100，平均句长：20"。

**影响**: 50 维风格分析耗时耗 token，但**分析结果对写作和审阅几乎无影响**——所有消费者读到的都是默认值。风格参考功能降级为仅 `style_brief` 自然语言起作用。

**修复方向**:
1. 更新 `PLANNING_PROMPT` 和 `WRITING_PROMPT`，使用 `{style_brief}` 替代零散数字字段
2. 各 Agent 统一通过 `style.get("style_brief", "")` 读取简报文本，不再逐字段取值
3. 或者定义一套简化的风格摘要字段（从 50 维导出），供旧接口消费者使用
4. Reviewer 中保留精细化风格一致性对比的能力（50 维 vs 生成文本）

---

### Bug P0-3: ContextManager 每次小节都重新创建 — 运行摘要压缩完全失效

**涉及文件**:
- [app/coordinator.py](app/coordinator.py#L723) — 每次 `_write_subsection()` 内 new ContextManager
- [app/agents/writer.py](app/agents/writer.py#L48) — `Writer.run()` 循环外创建一次（但该代码未被调用）
- [app/agents/context_manager.py](app/agents/context_manager.py#L11-L17) — 压缩阈值 6000 字

**描述**:

`_write_subsection()` 在 coordinator.py 中被每小节调用一次，内部第 723 行：
```python
cm = ContextManager(writer.llm)
```
每次创建一个全新的 `ContextManager`。这意味着：
- `self._buffer` 永远是空列表（上一小节的 buffer 已随对象销毁）
- `self._buffer_char_count` 永远从 0 开始
- `self.running_summary` 永远是空字符串

单个小节通常约 2000 字，远低于 6000 字压缩阈值。`_compress()` 方法永远不会被触发。

**对比正确行为** (Writer.run() 第 48 行):
```python
cm = ContextManager(self.llm)  # ← 循环外创建，所有小节共享
```

**影响**: 长文本模式的核心功能——"前文运行摘要"每次传给 LLM 的都是 `"（故事开头）"` 或仅最近一小节的内容。跨章节一致性保障机制完全失效，LLM 在写第 10 小节时看不到第 1-8 节的摘要。

**修复方向**: 将 `ContextManager` 实例化移到 `_phase_writing()` 层级（循环外），作为参数传入 `_write_subsection()`。或更根本地，直接让 coordinator 使用 `Writer.run()`。

---

## P1 — 中等：影响特定场景正确性或可维护性

---

### Bug P1-1: 缺少"中性"风格预设，fallback 行为不直观

**涉及文件**: [app/agents/style_analyzer.py](app/agents/style_analyzer.py#L152-L164)

**描述**: `_fill_defaults()` 调用 `StyleAnalyzer.get_preset("中性")` 作为默认值来源，但 `STYLE_PRESETS` 字典只有 6 个预设（热血/冷峻/治愈/压抑/紧迫/荒诞），没有"中性"。`get_preset()` fallback 返回 `STYLE_PRESETS["冷峻"]`。

**影响**: LLM 未填充的字段会被"冷峻"风格的值填充，而非真正的"中性"默认值。一个中性默认的预设应该存在（如 `StyleProfile` Pydantic 模型的 default 值），但目前代码中默认值来源不统一。

**修复方向**: 在 `STYLE_PRESETS` 中添加"中性"预设，或在 `_fill_defaults()` 中使用 `StyleProfile()` 的字段默认值。

---

### Bug P1-2: Writer.run() prompt format 缺少参数 → KeyError

**涉及文件**:
- [app/agents/writer.py](app/agents/writer.py#L113-L131) — format() 调用
- [app/utils/prompt_templates.py](app/utils/prompt_templates.py#L160-L209) — WRITING_PROMPT
- [app/utils/prompt_templates.py](app/utils/prompt_templates.py#L214-L259) — WRITING_SECTION1_PROMPT

**描述**: `WRITING_PROMPT` 和 `WRITING_SECTION1_PROMPT` 模板包含 `{world_setting}` 和 `{sub_description}` 占位符，但 `Writer.run()` 的 `template.format()` 调用没有传入这两个参数。

对比 coordinator 的 `_write_subsection()` 正确传入了：
```python
sub_description=sub_desc if sub_desc else "（按大意自由发挥）",
world_setting=world_setting if world_setting.strip() else "",
```

**影响**: 如果 `Writer.run()` 被调用（例如在测试或替代 coordinator 时），会直接抛 `KeyError`。当前该 bug 未暴露，因为 `Writer.run()` 是死代码（P0-1）。

**修复方向**: 在 `Writer.run()` 中补齐缺失的 format 参数，或移除模板中未使用的占位符。

---

### Bug P1-3: 交接笔记解析逻辑重复实现

**涉及文件**:
- [app/agents/writer.py](app/agents/writer.py#L286-L332) — `Writer._parse_output()`, `Writer._parse_backrefs()`
- [app/coordinator.py](app/coordinator.py#L815-L853) — `_extract_handover_from_text()`, `_parse_backrefs()`

**描述**: 两处实现了几乎完全相同的逻辑——用正则从 LLM 输出中提取 `[交接笔记]` 标记，解析伏笔/人物状态/待承接/回溯修正四个字段。正则模式和字段映射完全一致。

**影响**: 如果交接笔记格式变化、正则 bug 修复、或新增字段，必须在两处同步修改。

**修复方向**: 删除 coordinator 中的重复实现，统一使用 `Writer._parse_output()`（与 P0-1 一起修复）。

---

### Bug P1-4: Blackboard.wait_for_decision() 竞态条件

**涉及文件**: [app/blackboard.py](app/blackboard.py#L105-L120)

**描述**:
```python
def wait_for_decision(self, task_id, phase, timeout=300):
    key = self.decision_queue_key(task_id, phase)
    self._redis.delete(key)        # ← 先删队列
    result = self._redis.blpop(key, timeout=timeout)  # ← 再阻塞等
```
在 `delete` 和 `blpop` 之间有一个时间窗口：如果前端恰好此时 `POST /tasks/{task_id}/decide`，决策被 `rpush` 到队列后立刻被 `delete` 清空，然后 `blpop` 永远等不到。

**影响**: 当前 coordinator 使用 `pop_decision()` 非阻塞轮询而非 `wait_for_decision()`，所以此 bug 暂不触发。但如果将来改为阻塞等待模式（减少轮询开销），会间歇性丢失用户决策。

**修复方向**: `blpop` 之前不应 delete。改用 key 不存在时自动创建的语义，或在 `XREAD` 的 block 参数中实现阻塞等待（不依赖 queue delete）。

---

### Bug P1-5: Resume/Decide 模式不返回新 task_id

**涉及文件**: [app/main.py](app/main.py#L194-L202)

**描述**: 当用户在交互模式下通过 `POST /tasks/{task_id}/decide` 发送决策后，coordinator 内部调用 `writing_task.delay(...)` 创建一个**全新的** Celery 任务（新 task_id）。但 API 返回：
```json
{"status": "ok", "phase": "outline", "action": "approve"}
```
没有返回新的 `task_id`。

**影响**: 前端继续用旧的 `task_id` 轮询 `/stream/{task_id}` 和 `/status/{task_id}`，永远看不到新任务的进度。交互模式的逐节确认流程实际不可用。

**修复方向**: API 响应中返回 `new_task_id` 字段，前端收到后切换到新 task_id。

---

## P2 — 轻微：边缘场景或代码质量问题

---

### Bug P2-1: 流式调用失败时 fallback 行为不一致

**涉及文件**:
- [app/coordinator.py](app/coordinator.py#L760-L763)
- [app/agents/writer.py](app/agents/writer.py#L150-L155)

**描述**: 流式 LLM 调用异常时 fallback 到非流式，但将完整结果作为单个 `token` 事件推送给前端：
```python
stream_callback(raw_output, section_num, sub_num, "token")
```
前端期望 `token` 事件是增量文本（逐 token），收到数千字的一个"token"可能导致渲染异常或进度条失效。

**修复方向**: fallback 时发送 `section_start` → 分段发送（按句号切割）→ `section_end`，或发送一个特殊的 `fallback_text` 事件类型。

---

### Bug P2-2: SQLite/Chroma 存储路径硬编码

**涉及文件**:
- [app/vector_store.py](app/vector_store.py#L26) — `path="./chroma_data"`
- [app/character_store.py](app/character_store.py#L18) — `db_path="./characters.db"`
- [app/task_store.py](app/task_store.py#L10) — `db_path="./tasks.db"`

**描述**: 数据库和向量存储路径硬编码为相对路径。Celery worker 和 FastAPI 可能在不同工作目录启动（尤其是使用 `--workdir` 或 systemd 管理时），导致路径不一致。

**修复方向**: 通过环境变量配置这些路径（如 `CHROMA_DATA_PATH`, `CHARACTER_DB_PATH`, `TASK_DB_PATH`），默认值保持现有行为。

---

### Bug P2-3: CharacterStore JSON 字段解析静默失败

**涉及文件**: [app/character_store.py](app/character_store.py#L64-L70)

**描述**: `_row_to_dict()` 中 JSON 字段（`key_lines`, `relationships`, `custom`）解析失败时 `pass` 静默忽略，字段保持原始字符串。这可能导致下游代码收到字符串而非预期的 list/dict，引发 `AttributeError`。

**修复方向**: 解析失败时记录 warning 日志，并赋默认值 `[]` 或 `{}`。

---

### Bug P2-4: ChromaDB 0.5.x vs 0.6.x API 兼容代码脆弱

**涉及文件**: [app/vector_store.py](app/vector_store.py#L34-L39)

**描述**:
```python
if existing and not isinstance(existing[0], str):
    names = [c.name for c in existing]
else:
    names = existing
```
这假设 ChromaDB 0.5.x 返回 `list[str]`，0.6.x 返回 `list[Collection]`。未来版本可能返回其他类型，`isinstance(existing[0], str)` 是脆弱的启发式判断。

**修复方向**: 使用 `try/except AttributeError` 或检查 Collection 协议，替代类型判断。

---

# 三、修复优先级与路线

## 推荐修复顺序

### 第一步（紧急）：合并写作逻辑 + 修复风格字段

1. **修复 P0-1**: 删除 `coordinator.py` 中的 `_write_subsection()` 和 `_extract_handover_from_text()`/`_parse_backrefs()`，让 `_phase_writing()` 调用 `Writer.run()`。同时修复 P0-1 涉及的 P1-2（补齐 format 参数）和 P1-3（删除重复代码）。

   **涉及文件**: `coordinator.py`（删 ~200 行）, `writer.py`（修复 format 参数）

2. **修复 P0-2**: 将所有 prompt 模板和 Agent 代码中的旧风格字段替换为 `style_brief` 自然语言简报。`PLANNING_PROMPT` 和 `WRITING_PROMPT` 中删除零散数字，改为注入 `{style_brief}`。

   **涉及文件**: `prompt_templates.py`, `planner.py`, `writer.py`, `reviewer.py`, `style_analyzer.py`, `coordinator.py`

3. **修复 P0-3**: ContextManager 实例化移到 `_phase_writing()` 层级。如果采纳了步骤 1（使用 `Writer.run()`），则自动修复。

### 第二步（高优先级）：交互模式修复

4. **修复 P1-5**: `/tasks/{task_id}/decide` 返回 `new_task_id`。
5. **修复 P1-4**: `wait_for_decision()` 移除前置 delete。

### 第三步（改进）：代码质量

6. **修复 P1-1**: 添加"中性"预设。
7. **修复 P2-1**: 流式 fallback 分段发送。
8. **修复 P2-2**: 路径环境变量化。
9. **修复 P2-3**: JSON 解析失败加 warning。
10. **修复 P2-4**: ChromaDB 版本兼容改用 try/except。

## 涉及文件总览

| 文件 | P0 | P1 | P2 | 改动量估计 |
|------|-----|-----|-----|-----------|
| `coordinator.py` | ✓ | ✓ | - | 删 ~200 行，改 ~50 行 |
| `writer.py` | ✓ | ✓ | - | 改 ~30 行 |
| `prompt_templates.py` | ✓ | - | - | 改 ~40 行 |
| `planner.py` | ✓ | - | - | 改 ~10 行 |
| `reviewer.py` | ✓ | - | - | 改 ~10 行 |
| `style_analyzer.py` | ✓ | ✓ | - | 改 ~20 行 |
| `blackboard.py` | - | ✓ | - | 改 ~5 行 |
| `main.py` | - | ✓ | - | 改 ~10 行 |
| `config.py` | - | - | ✓ | 改 ~5 行 |
| `vector_store.py` | - | - | ✓ | 改 ~10 行 |
| `character_store.py` | - | - | ✓ | 改 ~5 行 |

---

# 四、调试经验总结

1. **Windows CMD 编码坑**: 批处理文件中文字符在 UTF-8 无 BOM 时会被错误解析，纯 ASCII 最安全。

2. **推理模型陷阱**: DeepSeek V4/R1 等推理模型默认有思考过程，会消耗大量 token，需要显式禁用或大幅提高 max_tokens。

3. **Chroma 接口要求**: `embedding_function` 必须实现 `EmbeddingFunction` 协议（有 `name` 属性），不能传裸函数。

4. **Python str.format() 与 JSON 花括号冲突**: 模板中含 JSON 示例时，`{` `}` 必须转义为 `{{` `}}`。

5. **Celery 任务发现**: `-A app.celery_app` 只加载 celery_app 模块，需在其中显式 import 任务模块。

6. **死代码危害**: `Writer.run()` 284 行代码从未被执行，期间新增的功能（world_setting、sub_description）未同步到 coordinator 路径，导致实际运行的代码缺少这些参数。两条路径分叉后 bug 无法互相暴露——coordinator 路径不知道 Writer.run() 会 KeyError，Writer.run() 不知道 coordinator 有 ContextManager bug。

7. **数据模型升级需全量迁移**: 从 6 维到 50 维是结构性变更，仅改数据模型定义和生产者、不改消费者，导致消费者全部使用默认值。应在新模型中保留旧字段作为计算属性（如 `@property intensity` 返回 `emotion_intensity`），实现平滑迁移。

8. **实例生命周期决定功能正确性**: ContextManager 设计依赖跨小节的状态累积，但被放在一个每次重建的函数内。有状态对象的作用域必须大于其使用场景的跨度。
