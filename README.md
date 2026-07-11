# 多智能体协作写作系统

> 一套用多个 LLM 智能体协作、生成 **10 万字以上长篇**并保持全局一致性的写作引擎。核心解决 LLM 长文创作的两大难题:**上下文遗忘**（角色/伏笔/世界观跨章节崩坏）和**任务时长**（单次生成动辄数小时）。

**技术亮点**

- **多智能体流水线** — 角色提取 → 风格分析 → 大纲评审 → 弧线规划 → 继承制写作 → 一致性检查 → 连续性编辑 → 分段审阅，8 个 Agent 各司其职，通过 Redis 黑板共享状态
- **长文一致性** — 统一事件模型（NarrativeEvent）+ 事件图谱（EventGraph）+ 交接笔记链，跨章节追踪伏笔与人物状态
- **RAG 检索增强** — 已写段落切块存入 Chroma 向量库，写新段前检索语义相关内容，抑制矛盾
- **异步 + 可恢复** — Celery 长任务队列，每章检查点序列化到 Redis，断点续写
- **流式输出** — Writer 逐 token 推送到 Redis Stream，前端实时增量渲染

**技术栈**：FastAPI · Celery · Redis · Chroma · BGE-M3 · DeepSeek V4 Pro · Vue 3 · Docker · 20+ 单元/集成测试 · 已部署阿里云 ECS

---

### 完整功能

支持短文本（~500 字）和长文本模式（100,000+ 字），通过统一事件模型（NarrativeEvent）、权重排序（rank_and_fill）、事件图谱（EventGraph）、交接笔记分离、情节节奏规划实现长文一致性和跨章节伏笔追踪。v0.9.0 新增抽卡模式、故事线约束、伏笔管理、规则中心、经历事件线、对话模式、物品背包、支线故事、故事地图等 20+ 功能。

---

## 目录

1. [系统架构](#系统架构)
2. [技术栈](#技术栈)
3. [快速开始](#快速开始)
4. [核心流程详解](#核心流程详解)
5. [智能体设计](#智能体设计)
6. [子系统详解](#子系统详解)
7. [API 参考](#api-参考)
8. [环境变量](#环境变量)
9. [项目结构](#项目结构)
10. [测试](#测试)
11. [变更日志](#变更日志)
12. [扩展方向](#扩展方向)

---

## 系统架构

```
                             ┌─────────────────────┐
                             │   用户 / Web UI      │
                             │  (writing_ui.html)   │
                             └──────┬──────┬───────┘
                                    │ HTTP  │ Stream 轮询
                                    ▼       ▼
                             ┌─────────────────────┐
                             │      FastAPI         │
                             │    (app/main.py)     │
                             └──────────┬──────────┘
                                        │ Celery Task
                                        ▼
┌───────────────────────────────────────────────────────────────┐
│                     coordinator.py                              │
│                    (任务编排引擎)                                │
│                                                                │
│  Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 2.6 ──→ Phase 3    │
│  角色提取     风格分析    大纲评审     弧线规划     继承制写作   │
│                                                                │
│  Phase 3.5 ──→ Phase 4 ──→ Phase 5 ──→ 导出                   │
│  一致性检查    连续性编辑   分段审阅      Markdown               │
└───────────────────────────────────────────────────────────────┘
         │            │            │              │
         ▼            ▼            ▼              ▼
    ┌────────┐  ┌─────────┐  ┌────────┐  ┌──────────────┐
    │ Redis  │  │ Chroma  │  │ SQLite │  │  LLM (DeepSeek│
    │ 黑板   │  │ 向量库  │  │ 角色库 │  │   V4 Pro)     │
    └────────┘  └─────────┘  └────────┘  └──────────────┘
                                       ┌────────────────┐
                                       │  WorldState    │
                                       │  世界状态管理   │
                                       └────────────────┘
```

### 设计原则

- **多智能体协作**：每个 Agent 职责单一，通过 Redis 黑板共享状态，coordinator 统一编排
- **继承制写作**：章节间通过交接笔记链传递伏笔/人物状态/待承接线索
- **世界锚点**：项目初始化时从世界观设定提取专有名词，push 到每节 prompt 作为一致性基准
- **长期记忆**：经历事件自动提取 + 主角重要经历注入 Writer prompt
- **RAG 检索增强**：已写段落切块存入 Chroma，每写新小节前检索语义相关段落
- **运行摘要压缩**：ContextManager 维护跨节上下文，超 6000 字触发 LLM 压缩
- **检查点恢复**：每节完成后序列化完整状态到 Redis，断点后可恢复
- **流式推送**：Writer 逐 token 推送事件到 Redis Stream，前端 HTTP 轮询增量获取
- **实时审阅与检测**：每小节 AI 痕迹检测 + 每 3 小节/8000 字分节审阅，fire-and-forget 不阻塞写作

---

## 技术栈

| 组件 | 技术 | 选型理由 |
|------|------|----------|
| API 网关 | FastAPI 0.110+ | 异步支持、自动 OpenAPI 文档、Pydantic 集成 |
| 异步任务 | Celery 5.4 + Redis | 长时写作任务异步执行，支持检查点恢复与重试 |
| 状态黑板 | Redis Hash + Stream | Hash 存取中间结果；Stream 实现流式事件推送与轮询消费 |
| 向量存储 | Chroma 0.5+ (本地持久化) | 轻量级向量库，支持自定义 EmbeddingFunction 和 metadata 过滤 |
| Embedding | BGE-M3 (默认) / OpenAI / Ollama | 工厂模式切换，抽象基类隔离业务逻辑 |
| LLM | DeepSeek V4 Pro (OpenAI 兼容) | 推理模式关闭，JSON 模式输出，2 次重试 |
| 角色库 | SQLite (双表: characters + character_traits) | 零依赖持久化，多值标签拆入关联表支持索引检索 |
| 任务历史 | SQLite (task_history 表) | 已完成任务元数据、草稿预览、大纲等 JSON 序列化存储 |
| 世界状态 | 规则层 + LLM 双模式 | 提取事实→规则预过滤→LLM 交叉验证→矛盾检测→按需检索 |
| 前端 | Vue 3 (CDN) + ES Modules | 多文件模块化 SPA，三栏布局（大纲编辑器 + 流式草稿 + 风格控制台），新UI入口 `/write-ui-v2` |
| 流式推送 | Redis Stream (XADD/XREAD) | Writer 逐 token 写入，前端 300ms 轮询增量消费 |
| 包管理 | uv + pyproject.toml | 快速依赖解析，hatchling 构建 |
| Python | 3.11+ | — |

---

## 快速开始

### 方式一：Docker（推荐）

```bash
# 1. 确保宿主机 Ollama 在运行（embeddings 需要）
ollama serve
ollama pull bge-m3:latest

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY

# 3. 一键启动
docker-compose up -d

# 4. 打开浏览器
# http://localhost:8000/write-ui-v2
```

首次构建需要下载基础镜像和 Python 依赖，约 5-10 分钟。之后秒起。

### 方式二：手动启动（Windows）

```bash
# 1. 环境准备
pip install uv
cd my_writing_system

# 2. 安装依赖
uv sync

# 3. 配置
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY

# 4. 启动 Redis
E:\Redis\redis-server.exe

# 5. 启动 Celery Worker (Windows 需要 -P solo)
uv run celery -A app.celery_app worker --loglevel=info -P solo

# 6. 启动 FastAPI
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 7. 打开浏览器
# http://localhost:8000/write-ui-v2
```

---

## 核心流程详解

### Phase 0: 角色提取

**输入**：`character_text`（自然语言人物描述）或 `characters`（结构化的角色卡列表）

**实现** (`CharacterManager.extract_characters()`)：
1. 将用户自由文本填充到 `CHARACTER_EXTRACTION_PROMPT` 模板
2. LLM 调用（temperature=0.3, max_tokens=4000）→ 返回 JSON 数组
3. `json_parser.parse_json()` 鲁棒解析（处理 markdown 代码块、前后多余文字、平衡括号提取）
4. 自动补齐缺失字段（空字符串 `""` / 空数组 `[]`），生成 UUID

**输出**：16 字段角色卡列表，存入 `bb.set(task_id, "characters", ...)`

**设计要点**：
- 缺失字段填默认值，不阻塞流程
- 同时支持结构化输入（角色库勾选）和自然语言提取两种方式
- 提取失败记录 timeline 但不中断 pipeline

---

### Phase 1: 风格分析

**实现** (`StyleAnalyzer.analyze()`)：
1. 参考文本（截取 6000 字符）填充 `STYLE_ANALYSIS_PROMPT` → LLM 返回 50 维 JSON
2. `_fill_defaults()` 用预设默认值补齐 LLM 未返回的字段
3. `build_brief()` 调 LLM 将 50 维参数转为 200-500 字自然语言风格简报（编辑口吻，给出可操作建议）

**50 维模型分组**：

| 组 | 维度 | 示例字段 |
|----|------|----------|
| A. 情感基调 | 13 项 | `primary_emotion`, `emotion_intensity`(0-100), `emotion_curve`, `narrative_empathy`, `inner_monologue_ratio` |
| B. 句式节奏 | 16 项 | `short_sentence_ratio`, `paragraph_rhythm`, `dialogue_ratio`, `pacing`, `tension_curve` |
| C. 修辞用词 | 21 项 | `metaphor_frequency`, `vocabulary_register`, `adjective_density`, `color_use`, `imagery_domain` |

**输出**：`{...50维..., style_brief: "自然语言简报"}` → `bb.set(task_id, "style", ...)`

**设计要点**：
- 用户可以直接提供 `style_profile` 跳过 AI 分析
- 7 个内置预设（中性/热血/冷峻/治愈/压抑/紧迫/荒诞）一键覆盖全 50 维
- `style_brief` 是下游 Agent 消费风格的主要方式——自然语言注入 prompt 比数字更有效

---

### 大纲树状编辑器（v0.7.0）

在 [writing_ui.html](writing_ui.html) 左侧面板中，大纲以**树状结构**呈现：

- **任意深度**：大节 → 小节 → 场景，叶子节点为写作原子单元（~2000 字）
- **手动编辑**：增删节点、编辑标题/描述/字数、上移/下移排序
- **AI 拆分**：选中任意节点，点击 🤖 输入拆分要求（可选），LLM 自动生成子节点
- **树→扁平转换**：写作时自动将树展开为 `[{section, subsections}]` 格式，后端管线零改动
- **全屏模式**：工具栏按钮切换，适合大规模编辑
- **双向兼容**：`flatOutlineToTree()` / `treeToFlatOutline()` 幂等转换，旧任务自动适配

---

### Phase 2: 协作式大纲评审

**实现** (`Planner.generate_outline()` + 多智能体审查)：

#### 2.1 大纲生成
1. 根据 `target_words_per_section` 计算小节数：`max(3, min(12, target_words // 2000))`
2. `PLANNING_PROMPT` 注入主题、字数目标、世界观设定、故事梗概、风格简报
3. LLM 返回 JSON 对象 → `_normalize_outline()` 标准化（补齐/裁剪小节数、统一 target_words）

#### 2.2 多智能体审查
```
Planner 大纲 V1
    │
    ├── StyleAnalyzer.review_outline()  → 风格一致性审查
    └── Writer._writer_review_outline() → 结构可执行性审查
    │
    └── 综合反馈 → Planner.revise_from_feedback() → 大纲 V2
```
- `OUTLINE_REVIEW_PROMPT` 要求输出 `{approved, criticism, suggestion}`
- 有批评意见时 Planer 修订生成 V2，无意见则 V2 = V1
- 交互模式下等待用户审批（5 分钟超时自动通过）

**输出**：`outline_v2`（V1/V2 均保留用于前端对比）→ `bb.set(task_id, "outline", ...)`

---

### Phase 2.6: 角色弧线规划

**实现** (`CharacterManager.plan_arcs()`)：
1. 角色卡 JSON + 大纲 V2 JSON 填充 `CHARACTER_ARC_PROMPT`
2. LLM 为每个角色生成：`starting_state → [milestones] → ending_state`
3. 里程碑精确到小节级别：`{section, subsection, event, location, time, emotional_shift}`
4. `current_state` 初始 = `starting_state`

**输出**：角色弧线列表 → `bb.set(task_id, "character_arcs", ...)`

---

### Phase 3: 继承制写作（核心）

**统一入口**：`Writer.run()` — coordinator 通过此方法委托全部写作逻辑

#### 3.1 RAG 检索增强

**实现原理**：
1. **切块入库**：每小节完成后 `chunk_text()` 按段落边界切分（500 字/块，100 字重叠），存入 Chroma
2. **语义检索**：写下一小节前，用 `{topic} {section_title} {sub_title} {key_points}` 拼接 query，调 `vector_store.search(query, k=5, task_id=task_id)`
3. **task_id 过滤**：Chroma metadata 过滤确保只检索当前任务的已写段落，避免跨任务污染

**切块策略** (`text_chunker.py`)：
```
优先按 \n\n 段落边界 → 段落超长按句号切 → 最后硬切固定长度
相邻块 100 字重叠，防止语义断裂
```

#### 3.2 运行摘要压缩

**实现** (`ContextManager`)：
```
add_subsection(text) → buffer 累积
                     → 超 6000 字触发 _compress()
                     → LLM 压缩为 500 字摘要
                     → running_summary 串联历史摘要
get_summary() → 返回 "前文摘要 + 最近 3 个小节原文"
```

**设计要点**：
- `ContextManager` 在 `Writer.run()` 循环外创建一次，整个写作周期共享
- 压缩失败不阻塞写作，保留原始 buffer
- `finalize()` 提供最终版本的全篇摘要

#### 3.3 交接笔记链

**机制**：
1. Writer 每小节输出末尾附加 `[交接笔记]` 标记（伏笔/人物状态/待承接/回溯修正）
2. `_parse_output()` 用正则分离正文和交接笔记
3. 每节结束后汇总各小节交接笔记 → 合并为一条 `HandoverNote` → 传给下一节

**prompt 注入格式**：
```
上一节留下的交接笔记：
  伏笔: 张三收到匿名信，来源不明
  人物状态: 张三——困惑且愤怒；李四——暗中观察
  待承接: 匿名信的发件人身份；张三下一步行动
```

#### 3.4 角色上下文注入

**实现** (`CharacterFormatter`)：
- `build_context()` — 生成角色摘要：`- 张三（内向、敏感）：复仇。当前状态：计划进行中`
- `build_arc_context()` — 生成当前小节弧线要求：匹配 section/subsection，找到对应里程碑事件/地点/情感转折

#### 3.5 字数控制

```
每小节生成后统计中文字数:
  - < 目标 70% → 自动续写 (_expand_text)：截取末尾 200 字作为上下文，继续生成
  - > 目标 130% → 自动精简 (_condense_text)：LLM 删冗余描述，保留核心情节
```

#### 3.6 流式推送

**事件流**：
```
section_start → token × N → section_end → handover → [awaiting_decision] → done
```

- **实现**：`Writer.run()` 接收 `stream_callback(payload, section, subsection, event_type)`
- **推送**：callback 内部调 `Blackboard.xadd_event()` 写入 Redis Stream（maxlen=10000）
- **消费**：前端 300ms 轮询 `GET /stream/{task_id}?last_id=...&count=50`，`XREAD` 增量获取

#### 3.7 交互模式检查点

```
每节完成 → 保存检查点到 Redis Hash (checkpoint:{task_id})
         → 等待用户确认 (POST /tasks/{task_id}/decide)
         → 用户 approve → 继续下一节
         → 用户 stop → 终止
         → 超时 10 分钟 → 自动继续
```

**检查点保存内容**：`task_id`, `phase`, `draft`, `section_texts`, `handover_chain`, `backref_suggestions`, `character_arcs`, `outline_v2`, 配置参数

#### 3.8 世界状态管理

**实现** (`WorldStateManager`)：

```
每小节完成后 → 提取世界事实 → 规则层预过滤（主观/客观/不确定）
             → LLM 交叉验证（区分客观事实 vs 主观感受）
             → 矛盾检测（同 category + 实体交集 → LLM 精判）
             → 不可变事实持久化到 Redis 黑板
```

**核心机制**：
- **规则层（零 LLM 成本）**：正则匹配主观词（感到/似乎/可能）→ 直接丢弃；客观词（获得/到达/摧毁/杀死）→ 自动通过
- **LLM 层（按需调用）**：不确定事实送 LLM 逐条核查，要求输出 `{verified: true/false, reason}`
- **矛盾检测**：同 category 下实体有交集 → LLM 判断是否逻辑不可共存（如"左臂已断"vs"双手持剑"）
- **按需检索**：每小节写作前，`query_relevant(keywords, top_k=8)` 检索与当前场景最相关的事实，注入 WRITING_PROMPT 的 `{world_state}` 占位符
- **运行警告**：检测到矛盾时不阻塞写作，而是将警告注入 prompt 提示 Writer 在后文中修正

**开关**：`ENABLE_WORLD_STATE=false` 环境变量可关闭整个模块；`WORLD_STATE_VERIFY_MODE=rule|llm|both` 控制验证策略。

---

### Phase 3.5: 角色一致性检查

**实现**：
1. 生成正文 + 角色库角色卡 JSON 填充 `CHARACTER_CONSISTENCY_PROMPT`
2. LLM 对比：性格矛盾（critical）、关系错误（critical）、口头禅遗漏（minor）、外貌不符（minor）
3. 结果存入 `bb.set(task_id, "consistency_issues", ...)`，供 ContinuityEditor 汇总

**开关**：`ENABLE_CONSISTENCY_CHECK=false` 环境变量可关闭

---

### Phase 4: 连续性编辑

**实现** (`ContinuityEditor.run()`)：
1. 汇总所有回溯修正建议（Writer 在写作过程中发现的前后矛盾）
2. LLM 分级判定：
   - **critical**：人物关系/事件因果/时间线矛盾 → 必须修正
   - **minor**：措辞风格微调/情感浓度调整 → 可改可不改
3. 自动执行 critical 修正：`Writer.revise_subsection()` 定向修改目标小节
4. 生成修正清单 `{critical_fixes: [...], minor_fixes: [...], summary}`

---

### Phase 5: 分段审阅

**实现** (`Reviewer`)：

#### 5.1 分节审阅
- 逐节调 `review_section()`：输出评分（1-10）、一致性评价、改进建议
- `SECTION_REVIEW_PROMPT` 注入主题、风格简报、字数、正文（截取 8000 字）

#### 5.2 全局终审
- `review_global()` 注入：各节摘要、交接笔记链、修正清单、人物一致性
- 输出：总体评分、优点/缺点、改进建议、交接笔记洞察、人物一致性评价、弧线完成度

---

### 导出

`_export_draft()` → `output/{topic}_{timestamp}.md`

Markdown 结构：
```
# {topic}
> 生成时间 / 总字数 / 全局评分
---
{正文}
---
## 审阅意见
## 交接笔记链
## 修正清单
```

---

## 智能体设计

### 基类架构

```python
class BaseAgent(ABC):
    def __init__(self):
        self.llm = get_llm_client()         # 全局单例 LLM 客户端
        self.last_raw_response: str = ""    # 调试用：保留 LLM 原始返回

    @abstractmethod
    def run(self, **kwargs) -> dict:
        """执行核心任务"""
```

**设计要点**：
- `llm_client` 全局单例，避免重复初始化 OpenAI 客户端
- `last_raw_response` 保留原始返回，JSON 解析失败时用于调试
- 子类可自由扩展方法，不强制所有逻辑在 `run()` 中

### 各智能体详解

| 智能体 | 职责 | 关键方法 | LLM 调用 |
|--------|------|----------|----------|
| **CharacterManager** | 角色提取、弧线规划、状态更新 | `extract_characters()`, `plan_arcs()`, `update_states()` | temperature=0.3~0.4, max_tokens=4000 |
| **StyleAnalyzer** | 50 维风格分析、预设管理、简报生成、大纲风格审查 | `analyze()`, `build_brief()`, `review_outline()` | temperature=0.3~0.5, max_tokens=800~3000 |
| **Planner** | 大纲生成、反馈修订 | `generate_outline()`, `revise_from_feedback()` | temperature=0.4~0.5, max_tokens=4000 |
| **Writer** | 继承制写作（统一入口）、定向修订、精修建议 | `run()`, `revise_subsection()`, `suggest_refinements()`, `refine_section()` | temperature=0.4~0.7, max_tokens 动态计算 |
| **Reviewer** | 分节审阅、全局终审、章节衔接检查 | `review_section()`, `review_global()`, `review_continuity()` | temperature=0.3~0.4, max_tokens=1000~1500 |
| **ContinuityEditor** | 回溯修正分级判定 | `run()` | temperature=0.3, max_tokens=2000 |
| **ContextManager** | 运行摘要维护、LLM 压缩 | `add_subsection()`, `get_summary()`, `_compress()` | temperature=0.3, max_tokens=600 |

### Writer 详解（核心智能体）

```
Writer.run(topic, style, outline, vector_store, blackboard, task_id,
           characters, character_arcs, stream_callback, interactive,
           on_section_done, world_setting, prev_draft, ...)

for 每节 (section):
    for 每小节 (subsection):
        1. RAG 检索 → vector_store.search(query, k=5, task_id)
        2. 角色上下文 → CharacterFormatter.build_context/arc_context
        3. 交接笔记上下文 → 上一节的 handover_chain
        4. 运行摘要 → ContextManager.get_summary()
        5. 构建 prompt → WRITING_PROMPT.format(...)
        6. LLM 调用 → 流式逐 token 推送 stream_callback / 非流式直接返回
        7. 分离正文与交接笔记 → _parse_output()
        8. 字数控制 → expand / condense
        9. 切块入库 → chunk_text() + vector_store.add_text()
        10. 更新运行摘要 → cm.add_subsection()
    节尾:
        - 汇总交接笔记链
        - CharacterManager.update_states() 更新角色状态
        - 交互模式: on_section_done() → 保存检查点 → 等待确认
返回: {draft, handover_notes, backref_suggestions, section_texts}
```

---

## 子系统详解

### Embedding 抽象层

**架构**：
```
EmbeddingProvider (ABC)
    ├── SentenceTransformerProvider  (BGE-M3 本地)
    ├── OpenAIEmbeddingProvider      (text-embedding-3-small)
    └── OllamaEmbeddingProvider      (Ollama /api/embeddings)
    
factory.get_embedding_provider() → 读 EMBEDDING_PROVIDER 环境变量 → 返回对应实例
```

**Chroma 适配** (`_ChromaEmbedFn`)：
```python
class _ChromaEmbedFn(EmbeddingFunction):
    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._provider.embed_batch(input)
```
ChromaDB 要求实现 `EmbeddingFunction` 协议（含 `name` 属性和 `__call__` 方法），不能传裸函数。

**切换方式**：修改 `.env` 中 `EMBEDDING_PROVIDER` 即可，无需改动业务代码。

### LLM 客户端

**实现** (`LLMClient`)：
```python
class LLMClient:
    def chat_completion(messages, temperature, max_tokens, max_retries=2, json_mode=False) -> str
    def chat_completion_stream(messages, temperature, max_tokens) -> Generator[str]
```

**关键设计**：
- **推理模式关闭**：`extra_body={"thinking": {"type": "disabled"}}` — DeepSeek V4 Pro 默认 chain-of-thought 会消耗大量 token 导致 JSON 被截断
- **空内容检测**：返回空内容时抛 RuntimeError，附带 `finish_reason` 和原始响应
- **重试机制**：失败自动重试 2 次，指数退避（1s → 2s）
- **全局单例**：`get_llm_client()` 返回共享实例

### JSON 解析器

**实现** (`json_parser.py`)：
```python
def parse_json(response: str) -> dict | list
```

**处理策略**（逐级 fallback）：
1. 尝试 `json.loads()` 直接解析
2. 移除 markdown 代码块（` ```json ... ``` `）
3. 状态机提取平衡括号内的 JSON 对象 `{...}`
4. 状态机提取平衡括号内的 JSON 数组 `[...]`

**状态机**：正确处理字符串内的转义引号和嵌套括号。

### 文本切块

**实现** (`text_chunker.py`)：
```
chunk_text(text, chunk_size=500, overlap=100) → list[str]

策略:
  1. 按 \n\n 段落边界切分
  2. 单段超 chunk_size → 按句号/换行进一步切
  3. 单句超 chunk_size → 固定长度硬切（最后手段）
  4. 相邻块尾部 overlap 字符重叠
```

### 中文字数统计

**实现** (`word_counter.py`)：
```python
_CJK_RE = re.compile(r'[一-鿿㐀-䶿豈-﫿]')
def count_chinese_chars(text: str) -> int:
    return len(_CJK_RE.findall(text))
```
仅统计 CJK 统一汉字，不含标点、空格、英文。

### Redis 黑板

**实现** (`Blackboard`)：

| 功能 | Redis 命令 | 方法 |
|------|-----------|------|
| 键值存取 | HSET / HGET / HGETALL | `set(task_id, key, value)`, `get(task_id, key)`, `get_all(task_id)` |
| 流式推送 | XADD / XREAD / XTRIM | `xadd_event()`, `xread_events()`, `stream_trim()` |
| 决策队列 | RPUSH / BLPOP / LPOP | `push_decision()`, `wait_for_decision()`, `pop_decision()` |
| 检查点 | HSET / HGETALL / EXPIRE | `save_checkpoint()`, `load_checkpoint()`, `delete_checkpoint()` |

**序列化规则**：
- 非字符串值 `json.dumps(value, ensure_ascii=False)` 后存入
- 读回时 `json.loads()` 恢复 dict/list 类型；解析失败保留原字符串

### 角色库

**实现** (`CharacterStore`)：

**双表设计**：
```sql
characters (id, name, gender, age, motivation, background, 
            appearance, catchphrase, secret, world_position,
            symbolism, key_lines JSON, relationships JSON, custom JSON)

character_traits (id, character_id, trait_type, trait_value)
  -- trait_type: 'personality' | 'strengths' | 'weaknesses'
  -- 索引: (trait_type, trait_value), (character_id)
```

**设计理由**：
- 单值文本字段存主表普通列，支持 `WHERE LIKE` 模糊检索
- 多值标签字段（性格/优点/缺点）拆入关联表，支持精确匹配、聚合统计、索引查询
- 复杂结构字段（台词含上下文、关系含对象+类型、自定义扩展）存 JSON 列

### 任务历史

**实现** (`TaskStore`, SQLite)：

| 字段 | 说明 |
|------|------|
| `task_id` | 主键 |
| `topic`, `word_count`, `section_count` | 摘要信息 |
| `style_json`, `outline_json`, `handover_json`, `characters_json`, `review_json` | 结构化中间结果 (JSON 列) |
| `draft_preview` | 草稿前 2000 字 |
| `output_file` | 导出文件路径 |
| `created_at`, `updated_at` | 时间戳 |

---

## API 参考

### 写作任务

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/write?mode=celery\|interactive` | 提交写作任务 |
| `GET` | `/status/{task_id}` | 查询任务状态与中间结果 |
| `GET` | `/result/{task_id}` | 阻塞等待任务完成，返回最终结果 |

### 流式事件

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/stream/{task_id}?last_id=0-0&count=100` | 轮询 Redis Stream 事件 |

**事件类型**：`phase_change` | `token` | `section_start` | `section_end` | `expand_start` | `handover` | `awaiting_decision` | `done` | `error`

### 检查点决策

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/tasks/{task_id}/decide?phase=...&action=...` | 交互模式决策，返回 `new_task_id` |

### 大纲与修订

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/tasks/{task_id}/update-outline` | 审批阶段替换大纲 |
| `POST` | `/tasks/{task_id}/approve-outline` | 大纲审批（兼容旧接口） |
| `POST` | `/tasks/{task_id}/revise` | 定向修订某节 |
| `POST` | `/tasks/{task_id}/edit-field` | 编辑任务中间字段 |

### 续写

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/tasks/{task_id}/continue` | 基于已完成任务续写新章节 |

### 精修

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/tasks/{task_id}/refine/start` | 进入精修模式 |
| `GET` | `/tasks/{task_id}/refine/state` | 获取精修进度与当前节文本 |
| `POST` | `/tasks/{task_id}/refine/section/{n}/suggest` | AI 分析粗稿，返回修改建议 |
| `POST` | `/tasks/{task_id}/refine/section/{n}/rewrite` | 根据指令重写一节 |
| `POST` | `/tasks/{task_id}/refine/section/{n}/confirm` | 确认一节精修结果 |
| `POST` | `/tasks/{task_id}/refine/finish` | 完成精修，触发终审和导出 |
| `POST` | `/tasks/{task_id}/review/continuity` | 评估相邻章节衔接质量 |

### 风格 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/style/analyze` | 从参考文本分析 50 维风格 |
| `POST` | `/api/style/preset` | 获取风格预设（7 个） |
| `POST` | `/api/style/brief` | 将 50 维转为自然语言简报 |

### AI 辅助生成

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/generate/world-setting` | 根据主题生成世界观设定 |
| `POST` | `/api/generate/story-synopsis` | 生成三幕式故事梗概 |
| `POST` | `/api/generate/split-node` | AI 拆分大纲树节点为子节点 |
| `POST` | `/api/generate/subsection-descriptions` | 为大纲小节生成梗概 |
| `POST` | `/api/generate/character-timeline` | 为角色生成出场时间线 |

### 角色库

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/characters?search=&trait=` | 角色列表 + 搜索 + trait 过滤 |
| `GET` | `/api/characters/stats` | 角色库统计 |
| `GET` | `/api/characters/{id}` | 角色详情 |
| `POST` | `/api/characters?on_conflict=skip\|overwrite\|merge` | 新建角色 |
| `PUT` | `/api/characters/{id}` | 更新角色 |
| `DELETE` | `/api/characters/{id}` | 删除角色 |
| `POST` | `/api/characters/extract` | 自然语言提取角色 |
| `POST` | `/api/characters/batch-save` | 批量保存提取结果 |

### 任务历史

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/tasks?limit=50` | 任务历史列表 |
| `GET` | `/tasks/{task_id}/history` | 任务完整历史 |
| `DELETE` | `/tasks/{task_id}/history` | 删除历史记录 |

### v0.9.0 新增 API

**规则中心** (`/api/rules`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/rules` | 规则列表 |
| `POST` | `/api/rules` | 创建规则 |
| `PUT` | `/api/rules/{id}` | 更新规则 |
| `DELETE` | `/api/rules/{id}` | 删除规则 |
| `POST` | `/api/rules/export` | 导出规则包 (JSON) |
| `POST` | `/api/rules/import` | 导入规则包 |
| `GET` | `/api/rules/context` | 获取规则注入上下文 |

**伏笔管理** (`/api/foreshadowings`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/foreshadowings?task_id=` | 伏笔列表 |
| `GET` | `/api/foreshadowings/chapter/{n}?task_id=` | 按章节查伏笔 |
| `POST` | `/api/foreshadowings` | 创建伏笔 |
| `PUT` | `/api/foreshadowings/{id}` | 更新伏笔 |
| `DELETE` | `/api/foreshadowings/{id}` | 删除伏笔 |

**抽卡模式** (`/api/cards`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/cards/draw` | 生成多张方案卡片 (3-5张) |
| `POST` | `/api/cards/redraw` | 重抽单张卡片 |
| `GET` | `/api/cards/steps` | 可用步骤列表 |
| `GET` | `/api/cards/inspirations?category=` | 灵感库 (45+ 套路) |

**对话模式** (`/api/dialogue`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/dialogue/chat` | 上下文感知创作讨论 |
| `POST` | `/api/dialogue/summarize` | 总结讨论为结构化数据 |
| `GET` | `/api/dialogue/quick-prompts` | 快捷提示词库 |

**经历事件线** (`/api/experience`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/experience?task_id=` | 事件列表 |
| `GET` | `/api/experience/context?task_id=&chapter=` | 注入上下文 |

**物品背包** (`/api/items`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/items/inventory/{character_id}` | 角色背包查询 |
| `POST` | `/api/items` | 创建物品 |
| `POST` | `/api/items/transactions` | 记录物品变动 |

**支线故事** (`/api/subplots`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/subplots?task_id=` | 支线列表 |
| `POST` | `/api/subplots` | 创建支线 |
| `GET` | `/api/subplots/heat-map?task_id=` | 章节热点图 |
| `POST` | `/api/subplots/auto-bind` | 一键自动绑定 |

**AI痕迹检测** (`/api/ai-detect`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/ai-detect/analyze` | 综合分析 (高频词+AI套路) |
| `POST` | `/api/ai-detect/high-freq` | 高频词检测 |
| `POST` | `/api/ai-detect/patterns` | AI套路匹配 |

**故事地图** (`/api/map`)
| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/map/full?task_id=` | 完整地图数据 |
| `POST` | `/api/map/nodes` | 创建节点 |
| `POST` | `/api/map/edges` | 创建连线 |
| `GET` | `/api/map/route?task_id=` | 主角路径 |
| `POST` | `/api/map/route` | 设置主角路径 |

### 页面路由

| 路由 | 说明 |
|------|------|
| `/` `/v2` `/debug` | 经典模式（`debug_ui.html`） |
| `/interactive` | 交互模式（`interactive.html`） |
| `/write-ui` | 主力写作界面 v1（`writing_ui.html`，Vue 3 单文件） |
| `/write-ui-v2` | 主力写作界面 v2（`app/static/index.html`，ES Modules 多文件架构） |

---

## 环境变量

```bash
# ═══ LLM 配置（DeepSeek V4 Pro）═══
LLM_API_KEY=sk-xxx              # DeepSeek API Key（必填）
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-pro

# ═══ Embedding 配置 ═══
EMBEDDING_PROVIDER=sentence_transformers  # sentence_transformers | openai | ollama
EMBEDDING_MODEL=BAAI/bge-m3

# OpenAI（仅 EMBEDDING_PROVIDER=openai 时需要）
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1

# ═══ 存储路径（可选）═══
CHROMA_DATA_PATH=./chroma_data
CHARACTER_DB_PATH=./characters.db
TASK_DB_PATH=./tasks.db

# ═══ 长文本配置 ═══
DEFAULT_TARGET_WORDS=10000       # 每节默认目标字数
CHUNK_SIZE=500                  # RAG 切块大小（字符数）
CHUNK_OVERLAP=100               # 切块重叠字符数
RAG_TOP_K=5                     # 每次检索段落数

# ═══ 角色一致性检查 ═══
ENABLE_CONSISTENCY_CHECK=true

# ═══ Redis ═══
REDIS_BROKER_URL=redis://localhost:6379/0
REDIS_BACKEND_URL=redis://localhost:6379/1
```

---

## 项目结构

```
my_writing_system/
├── pyproject.toml
├── README.md
├── DEBUG.md                         # 调试日志与设计问题记录
├── PROGRESS.md                      # 开发进度跟踪
├── .env.example
├── .gitignore                       # Git 忽略规则
├── docker-compose.yml               # Redis 容器（macOS/Linux）
├── start.bat / start.ps1            # Windows 一键启动
├── debug_ui.html                    # 经典模式 UI (vanilla JS)
├── interactive.html                 # 交互模式 UI (Vue 3)
├── writing_ui.html                  # 主力写作 UI (Vue 3, 三栏布局)
├── characters.db                    # 角色库 (自动生成)
├── tasks.db                         # 任务历史 (自动生成)
├── rules.db                         # [v0.9] 规则存储 (自动生成)
├── foreshadowings.db                # [v0.9] 伏笔存储 (自动生成)
├── experience.db                    # [v0.9] 经历事件线 (自动生成)
├── items.db                         # [v0.9] 物品背包 (自动生成)
├── subplots.db                      # [v0.9] 支线故事 (自动生成)
├── maps.db                          # [v0.9] 故事地图 (自动生成)
├── chroma_data/                     # Chroma 持久化 (自动生成)
├── output/                          # Markdown 导出目录
├── app/
│   ├── main.py                      # FastAPI 入口 + 所有 API 端点
│   ├── config.py                    # 环境变量配置 (Settings)
│   ├── models.py                    # Pydantic 模型 (请求/响应/角色/风格)
│   ├── celery_app.py                # Celery 实例 + 任务注册
│   ├── coordinator.py               # 任务编排引擎 (7 阶段 pipeline)
│   ├── blackboard.py                # Redis 黑板 (Hash + Stream + 队列)
│   ├── vector_store.py              # Chroma 封装 (EmbeddingFn 适配)
│   ├── character_store.py           # SQLite 角色库 (双表 CRUD)
│   ├── task_store.py                # SQLite 任务历史
│   ├── world_state.py               # 世界状态管理 (事实提取+矛盾检测)
│   ├── rule_store.py                 # [v0.9] 规则中心存储 (SQLite + 10预设)
│   ├── foreshadowing_store.py        # [v0.9] 伏笔管理存储 (SQLite)
│   ├── experience_timeline.py        # [v0.9] 经历事件线 (主角长期记忆)
│   ├── item_manager.py               # [v0.9] 物品背包系统
│   ├── subplot_manager.py            # [v0.9] 支线故事管理 (7要素+热点图)
│   ├── map_manager.py                # [v0.9] 故事地图管理 (节点+边+路径)
│   ├── ai_artifact_detector.py       # [v0.9] AI痕迹检测
│   ├── static/                       # [v0.9] 新前端 (ES Modules)
│   │   ├── index.html
│   │   ├── js/ (app, store, api, stream, persistence, utils)
│   │   ├── components/ (card-drawer, rules-panel, foreshadow-panel, ...)
│   │   └── styles/ (base, panels, cards)
│   ├── routers/
│   │   ├── cards.py                  # [v0.9] 抽卡 + 灵感库
│   │   ├── rules.py                  # [v0.9] 规则中心
│   │   ├── foreshadowings.py         # [v0.9] 伏笔管理
│   │   ├── dialogue.py               # [v0.9] 对话模式
│   │   ├── experience.py             # [v0.9] 经历事件线
│   │   ├── items.py                  # [v0.9] 物品背包
│   │   ├── subplots.py               # [v0.9] 支线故事
│   │   ├── ai_detect.py              # [v0.9] AI痕迹检测
│   │   └── map.py                    # [v0.9] 故事地图
│   ├── agents/
│   │   ├── base.py                  # Agent 基类
│   │   ├── character_manager.py     # 角色提取/弧线/状态更新
│   │   ├── character_formatter.py   # 角色上下文格式化
│   │   ├── style_analyzer.py        # 50 维风格 + 7 预设
│   │   ├── planner.py               # 大纲生成 + 修订 + 约束提取
│   │   ├── writer.py                # 继承制写作 (统一入口, v0.9 约束/伏笔/规则注入)
│   │   ├── reviewer.py              # 分节审阅 + 全局终审
│   │   ├── context_manager.py       # 运行摘要压缩
│   │   ├── continuity_editor.py     # 连续性编辑
│   │   ├── card_drawer.py           # [v0.9] 抽卡Agent (多方案生成)
│   │   └── dialogue_agent.py        # [v0.9] 对话Agent (创作讨论+总结)
│   ├── embedding/
│   │   ├── base.py                  # EmbeddingProvider ABC
│   │   ├── sentence_transformer_provider.py
│   │   ├── openai_provider.py
│   │   ├── ollama_provider.py
│   │   └── factory.py
│   └── utils/
│       ├── llm_client.py            # LLM 客户端 (重试/推理关闭)
│       ├── json_parser.py           # 鲁棒 JSON 解析 (状态机)
│       ├── prompt_templates.py      # 所有 prompt 模板
│       ├── text_chunker.py          # 段落感知文本切块
│       └── word_counter.py          # 中文字数统计
└── tests/
    └── test_basic.py
```

---

## 测试

```bash
uv run python tests/test_basic.py
```

测试覆盖：
1. Embedding 提供商验证（模型名称、维度、单条/批量）
2. LLM 配置验证（连通性测试）
3. 完整写作流程（提交 → 轮询 → 输出）
4. 角色数据模型（CharacterProfile、CharacterArc、WriteRequest）
5. 角色提取（自然语言 → 结构化角色卡）
6. 角色弧线规划（角色卡 + 大纲 → 弧线）

---

## 变更日志

### v0.9.0 — 创作体验全面升级 (2026-06)

- **前端架构重构**：单文件 SPA → ES Modules 多文件架构（`app/static/`），新增 `/write-ui-v2` 路由
- **Phase 1 — 基础约束系统**：故事线约束（不可变叙事骨架）+ 伏笔管理（埋设→回收生命周期）+ 规则中心（SQLite + 10条预设 + JSON导入导出）
- **Phase 2 — 抽卡模式**：AI一次性生成3-5个不同走向方案，支持采纳/重抽/修改/跳过，灵感库内置45+套路（世界观/主角/反转/爽点），卡片入场动画+键盘快捷键
- **Phase 3 — 深度记忆**：经历事件线（主角长期记忆，importance≥7永久保留）+ 细纲模式（场景级大纲）+ 对话模式（上下文感知AI头脑风暴 + 结构化总结 + 8条快捷提示词）
- **Phase 4 — 质量控制**：章节脉络（叙事任务分配）+ AI痕迹检测（高频词+套路模式匹配，零LLM成本）+ 物品背包（获取→转移→消耗全链路追踪）+ 大纲逻辑评估
- **Phase 5 — 支线系统**：七要素模型（欲望/阻碍/行动/结果/意外/转折/结局）+ 章节热点图（防止单章超载）+ 角色系统增强（力量等级/存续状态/人生经历/背包/关系追踪）
- **Phase 6 — 世界观构建**：增强交互式写作（section/paragraph粒度可切换，段落级选项复用抽卡机制）+ 故事地图（9级节点层次，拖拽布局，主角路径追踪）
- **交互模式统一**：游戏模式合并到交互模式，不再独立维护两套系统
- **新增 Agent**：CardDrawer（多方案生成）+ DialogueAgent（创作讨论+总结）
- **新增 API**：14 个新路由（rules, foreshadowings, cards, dialogue, experience, items, subplots, ai_detect, map）
- **Writer 增强**：run() 支持 constraints/rules_context 参数，prompt顶部注入约束/伏笔/规则上下文
- **Planner 增强**：新增 extract_constraints() 方法，大纲生成后自动提取约束

### v0.8.0 — 深度一致性 v3 核心架构

- **统一事件模型**（`NarrativeEvent`）：合并角色弧线、伏笔线索、世界事实为单一事件类型。伏笔回收自动生成世界事实
- **权重排序**（`rank_and_fill()`）：替代三层预算，所有上下文按 weight × urgency 排序后填充 prompt
- **事件图谱**（`EventGraph`）：事件关联网络，弧线↔伏笔↔事实 联动，精修修改自动传播 stale 标记
- **交接笔记分离**：Writer 只输出纯正文，`_extract_handover()` 独立提取交接信息（无元数据残留）
- **情节节奏阶段**（`narrative_rhythm`）：正弦波 intensity 曲线，每小节注入情感强度和焦点角色
- **Agent 角色转变**：从"守卫/警察"转为"笔记员"——记录事实、标注提醒、不做强制裁决
- **精修五档+维度开关**：Low/Mid/High/Max 四档 × 8 维度独立勾选，`suggest` 端点按维度分组返回
- **大纲文本导入**：粘贴自然语言 → AI 识别层级 → 替换选中节点子树
- **事件持久化**：EventGraph 存入 SQLite，前端状态栏显示伏笔回收率和弧线进度
- **前端持久化重构**：`persistence.js` 独立模块 + v2→v3 迁移 + beforeunload 强制保存 + 自检
- **ComfyUI 暗色主题**：`#121212` 底色 / `#1e1e1e` 面板 / 青色 `#00bcd4`
- **UI 清理**：删除密度滑块、角色时间线面板、旧版交接笔记模板

### v0.7.0 — 大纲树状编辑器 + 调试日志 + Bug 修复

- **大纲树状编辑器**：任意深度树形结构，AI 拆分节点，手动增删改移，全屏模式
- **调试日志**：协调器/Writer/LLM 三层日志，LLM 调用带耗时和 token 统计
- **导出修复**：Celery 结果返回 `output_file` 路径，前端完成后自动加载正文
- **历史记录修复**：树结构持久化到 SQLite，保存失败不再静默吞错
- **大纲审批跳过**：审批面板新增"跳过"按钮，修复审批后无限循环的竞态条件
- **代码修复**：测试文件旧风格字段更新、`main.py` 重复 import 移除、版本号统一到 0.6.0
- **工程**：新增 `.gitignore`，存储路径支持环境变量

### v0.6.0 — 世界状态管理

- 新增 `WorldStateManager`：规则层（零 LLM 成本）+ LLM 双模式事实验证
- 跨章节矛盾自动检测（同 category + 实体交集 → LLM 精判）
- 按需检索：小节写作前查询相关世界事实注入 prompt
- 运行警告：检测到矛盾时注入 prompt 提示 Writer 修正
- 配置：`ENABLE_WORLD_STATE` 开关 + `WORLD_STATE_VERIFY_MODE` 策略选择
- 持久化：事实库序列化到 Redis 黑板，断点恢复后完整还原

### v0.5.0 — Bug 修复轮次

- 核心写作逻辑统一：coordinator 委托 `Writer.run()`，删除 ~200 行重复代码
- 50 维风格模型字段全局迁移：所有 Agent 统一使用 `style_brief` / `emotion_intensity` / `paragraph_length_avg`
- ContextManager 运行摘要压缩修复（循环外创建一次实例）
- 新增"中性"风格预设
- `wait_for_decision()` 竞态条件修复
- `/decide` 端点返回 `new_task_id`
- 流式 fallback 按句子分段推送
- 存储路径支持环境变量配置
- `writing_ui.html` 新增调试日志面板（filter/clear/export/auto-expand）
- `writing_ui.html` 新增 API base URL 配置 + fetch 拦截器
- 写作完成后新增精修模式选择提示

### v0.4.0 — 流式重构

- 移除 WebSocket，统一为 Celery + Redis Stream + HTTP 轮询
- `Writer.run()` 新增 `stream_callback` 参数
- `Blackboard` 新增 Stream/决策队列/检查点操作

### v0.3.0 — 角色系统

- CharacterManager：LLM 提取 + 弧线规划 + 状态更新
- CharacterStore：SQLite 双表持久化
- 角色一致性检查（Phase 3.5）
- 角色库 CRUD API（8 个端点）

### v0.2.0 — 风格系统升级

- 50 维 StyleProfile 模型
- StyleAnalyzer：AI 分析 + 6 预设 + 简报生成
- 风格 API（3 个端点）

### v0.1.0 — MVP

- 多智能体协作 pipeline
- BGE-M3 Embedding + DeepSeek V4 Pro
- RAG 检索增强
- Celery 异步任务

---

## 扩展方向

- **风格系统**：✅ 50 维模型 + 7 预设。后续：风格迁移（A 文本风格 → B 文本改写）
- **角色库**：✅ SQLite 持久化 + CRUD API。后续：角色关系网络图、出场历史追踪、跨作品角色联动
- **交互式写作**：✅ Redis Stream + 检查点 + paragraph级粒度。后续：多人协作编辑、版本历史、分支剧情
- **抽卡模式**：✅ 多方案生成 + 灵感库。后续：社区灵感市场、历史卡片复用
- **规则系统**：✅ SQLite + 10预设 + 导入导出。后续：规则效果分析、社区规则市场
- **伏笔系统**：✅ 生命周期管理 + 自动注入。后续：伏笔回收分析报告
- **故事地图**：✅ 节点/边/路径模型。后续：cytoscape.js 可视化、AI自动生成地图
- **支线系统**：✅ 七要素 + 热点图。后续：支线→主线影响分析
- **写作逻辑**：✅ v0.5.0 统一入口。后续：段落级 undo/redo
- **更多 LLM**：OpenAI 兼容接口，可接入 Qwen / Claude / GPT
- **导出格式**：PDF / EPUB
- **FlagEmbedding**：`embedding/factory.py` 已预留扩展点

---

## 已知问题

### Docker 环境

- Docker 内用 Redis 7-alpine，完全兼容最新 redis-py，无需锁版本
- Embeddings 默认用 Ollama，需宿主机运行 `ollama serve` + 已拉取 `bge-m3:latest`
- 如需自包含部署（不用 Ollama），取消 Dockerfile 中 `sentence-transformers` 的注释即可，但镜像会增大 ~2GB

### Windows 环境注意事项

- **Celery Worker** 必须使用 `-P solo` 参数（不支持 fork）
- **Redis**：Windows 原生 Redis（[tporadowski/redis](https://github.com/tporadowski/redis)）最高 5.0.14，需降级 redis-py 到 4.4.4
- **BGE-M3 模型下载**：网络无法访问 HuggingFace 时，改用 Ollama（`EMBEDDING_PROVIDER=ollama`）
- **CMD 编码**：含中文的批处理可能截断，建议用 PowerShell

### ChromaDB 版本兼容

`vector_store.py` 中 ChromaDB 0.5.x vs 0.6.x 的集合列表类型判断使用了启发式方法，未来版本变更可能导致问题。当前使用 try/except 作为后备。

### 精修模式使用说明

精修模式（`/tasks/{task_id}/refine/start`）需要任务检查点在 Redis 中存在。历史任务可能因 Redis 重启或过期而无法进入精修，此时会尝试从 SQLite 和 `output/*.md` 文件自动重建检查点。

### DeepSeek V4 Pro 网络波动

API 断连时系统自动处理：流式失败 → fallback 非流式 → 失败自动重试 2 次（间隔 1s/2s）。重试全部失败后任务标记为 failed，交互模式支持检查点恢复续写。
