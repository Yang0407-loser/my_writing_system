# 长篇写作一致性系统重构执行计划

> 状态：批次 A 与 Phase 3 入口收尾已完成；19 条 hard 规则已人工审阅，Phase 3 可启动但尚未开始  
> 日期：2026-07-18  
> 执行者：Codex  
> 核心目标：降低长篇上下文不一致、角色漂移和风格漂移；减少 Writer 无效上下文；建立可重复验证的质量闭环。

## 1. 执行原则

1. 本计划是质量重构，不新增面向用户的子系统。
2. 所有改动必须先建立基线，再修改，再与基线对比。
3. 每个阶段必须独立提交、独立测试、可以回滚；不得一次性重写 Writer、RAG、EventGraph 和存储层。
4. 不以“代码跑通”作为验收标准，必须报告外部行为指标。
5. 不得把 LLM-as-Judge 当作唯一真值；核心指标必须保留人工标注集。
6. 不得继续使用 `except Exception: pass`；可降级路径必须记录 task、section、功能名和 fallback。
7. 未经基准测试，不假定 ChromaDB 的 metadata filter 是前过滤或后过滤，也不直接切换 per-task collection。
8. 每完成一个阶段，更新本文的“执行记录”，记录数据、结论、遗留问题和下一步。

## 2. 问题定义

系统要解决的不是“让多个 Agent 看起来都在工作”，而是三个可测问题：

### 2.1 上下文连续性

Writer 写当前小节时，应获得必要的历史事实、近期状态、跨章原文细节和相关事件，且不被大量无关信息干扰。

### 2.2 角色一致性

角色的稳定属性不能无故改变；动态状态必须连续；角色弧允许变化，但变化必须发生在规划或实际事件支持的范围内。

### 2.3 风格稳定性

各章节应持续符合目标 StyleProfile；允许随叙事节拍变化，但不能发生无法解释的句式、对话比例、感官密度和叙述方式漂移。

## 3. 当前主要缺陷

### 3.1 Writer 职责过载

Writer 同时接收规则、伏笔、角色、关系、势力、地图、物品、世界事实、事件图、摘要、交接笔记、RAG 和风格信息，并自行判断相关性、优先级和冲突。系统虽然拆出了多个 Agent，但上下文决策仍集中在 Writer。

### 3.2 状态没有唯一权威来源

角色状态、关系、事件、世界事实和长期记忆在多个模块中重复表达，缺少所有权、版本和冲突优先级。

### 3.3 RAG 只有粗召回，没有完整检索链路

当前缺少明确的 query 计划、候选重排、阈值、去重、章节覆盖、token 预算和引用利用率测试。现有人工样本 Precision@5 为 68%，且后期章节明显退化。

### 3.4 因果检索名义存在、实际无产出

EventGraph 主要保存 Planner 预设的稀疏 `arc_milestone`，没有稳定接收正文中实际发生的事件；现有因果边无方向、无类型，不能支撑严格意义上的因果检索。

### 3.5 记忆机制重复

交接笔记、运行摘要、WorldState、ExperienceTimeline、EventGraph、RAG 和未被引用的 MemoryFuser 存在职责交叉。MemoryFuser 当前无调用方，应视为待确认废案。

### 3.6 质量评估未进入开发闭环

已有 `eval_quality.py` 和一份 RAG 人工标注，但样本小、运行手动、没有成为每次改动的回归门槛。角色与风格也缺少稳定人工基准集。

## 4. 目标架构

```text
权威状态源
  ├─ CharacterProfile / CharacterState
  ├─ RelationshipState
  ├─ WorldFact
  ├─ NarrativeEvent
  ├─ Foreshadowing
  └─ StoryChunk
          ↓
Context Broker
  ├─ 写前必需上下文
  ├─ 冲突处理与去重
  ├─ token 预算
  ├─ 来源追踪
  └─ 检索计划
          ↓
Writer
  ├─ 接收最小必要上下文
  └─ 信息不足时调用受控工具
          ↓
正文
          ↓
Validators
  ├─ 上下文事实验证
  ├─ 角色一致性验证
  └─ 风格偏差验证
          ↓
状态更新 + 质量指标 + 可追溯日志
```

## 5. 核心数据契约

第一阶段只定义契约，不立即迁移全部数据库。

### 5.1 ContextItem

Context Broker 的统一输入输出单位：

| 字段 | 类型 | 责任 |
|---|---|---|
| `id` | string | 全局可追溯 ID |
| `kind` | enum | rule / character_profile / character_state / relationship / event / world_fact / foreshadowing / handover / story_chunk / style |
| `content` | string | 传给 Writer 的内容；硬约束原则上保留原文 |
| `source_id` | string | 原始记录 ID |
| `source_version` | string/int | 来源版本，用于恢复和审计 |
| `project_id` | string | 项目隔离 |
| `section` | int/null | 来源章节 |
| `subsection` | int/null | 来源小节 |
| `characters` | list[string] | 相关角色 |
| `event_ids` | list[string] | 相关事件 |
| `priority` | int | 1–10，业务重要性 |
| `hardness` | enum | hard / soft / evidence |
| `confidence` | float | 提取或检索置信度 |
| `token_estimate` | int | 预算控制 |
| `reason` | string | 为什么被选入当前上下文 |

### 5.2 StoryChunk metadata

保留现有字段，并逐步增加：

- `project_id`、`task_id`；
- `section`、`subsection`、`title`；
- `characters`、`locations`；
- `event_ids`、`foreshadowing_ids`；
- `event_type`、`timeline_position`；
- `content_hash`、`source_version`、`created_at`。

Chroma metadata 对复杂类型有限制时，将列表序列化为稳定字符串；具体格式必须由兼容性测试决定。

### 5.3 NarrativeEvent

在现有字段基础上增加或明确：

- `source`: planned / extracted / user；
- `actual_status`: planned / occurred / contradicted / cancelled；
- `participants`；
- `location_id`；
- `source_chunk_ids`；
- 边结构独立为 `EventEdge`：`from_event_id`、`to_event_id`、`relation_type`、`confidence`、`evidence_source_ids`；
- `relation_type`: causes / enables / blocks / resolves / foreshadows / motivates / follows。

## 6. 分阶段执行计划

## Phase 0：冻结功能并建立基线

### 目标

在任何检索和 Prompt 改动前，获得可以重复运行的基线。

### 任务

1. 记录当前 Git 状态和现有测试结果，不覆盖用户未提交改动。
2. 运行现有 unit、integration 和 quality 测试，记录失败项。
3. 固定一份 10–20 章的“黄金故事”作为端到端样本；如果现有完整任务可恢复，优先复用，不重新付费生成。
4. 扩充 RAG 人工标注格式，使每条 query 包含：
   - query intent；
   - gold sections/chunks；
   - 候选相关性；
   - 必须召回事实；
   - 是否需要因果检索。
5. 建立角色一致性标注集：至少 3 个核心角色，每人 10 条允许/禁止/边界行为。
6. 建立风格基线：固定 5–10 个文本片段及期望区间。
7. 记录当前每小节：输入 token、输出 token、上下文各块 token、生成耗时、重写次数。

### 验收

- 相同数据可重复运行评估；
- 输出一份 baseline JSON/Markdown 报告；
- 至少包含 Precision@5、Recall@5、角色硬约束违反率、风格偏差和每小节上下文 token；
- 不修改生产检索行为。

### 回滚

本阶段只增加测试与报告，不改变运行逻辑。

## Phase 1：代码与状态源审计

### 目标

明确每种状态谁负责，删除或隔离废案，防止后续继续重复设计。

### 任务

1. 生成模块清单，逐个记录：调用方、输入、输出、持久化位置、异常 fallback、是否进入 Writer Prompt。
2. 对下列机制做重叠矩阵：交接笔记、摘要、WorldState、ExperienceTimeline、EventGraph、RAG、角色状态、关系状态。
3. 指定唯一权威来源：
   - 稳定人设 → CharacterProfile；
   - 动态人物状态 → CharacterState；
   - 人物关系 → CharacterRelation；
   - 客观世界事实 → WorldState；
   - 已发生情节 → NarrativeEvent；
   - 原文证据 → StoryChunk；
   - 最近承接 → Handover。
4. 确认 MemoryFuser 无调用方后删除文件及相关文档描述；如发现隐式调用，先补测试再决定。
5. 将 ExperienceTimeline 标记为兼容层，不再拥有独立事件存储；避免立即删除外部接口。
6. 修正文档中“50维风格”等与实际实现不一致的描述。

### 验收

- 一份状态所有权表；
- 无生产调用方的代码被删除或明确 deprecated；
- 全量测试通过；
- Writer 每个上下文块都能映射到唯一来源。

## Phase 2：RAG 可观测性与检索基准

### 目标

先看清检索过程，再改变算法。

### 任务

1. `search_with_meta` 返回或记录：document ID、distance/score、metadata、过滤条件、耗时。
2. 记录粗召回候选，不只记录最终 top-5。
3. 对 Chroma 当前版本做基准：
   - 1、10、100 个 task；
   - 每 task 100、1,000、5,000 chunks；
   - 比较 metadata filter 与独立 collection 的查询延迟、召回结果和启动时间；
   - 不根据推测决定 collection 策略。
4. 修正空文本写入、重复 chunk、未调用的 task limit/cleanup 生命周期。
5. 将 retrieval metrics 与 Writer 输出关联：记录候选是否被正文引用或改写。

### 验收

- 能回答一次检索为什么返回这五条；
- 获得当前版本 Chroma filter 的实测数据；
- 能区分“没召回”“重排丢失”“Writer未使用”；
- 生产逻辑默认保持旧策略，可用 feature flag 开启实验。

## Phase 3：完整语义检索链路

### 目标

从“单次向量 top-5”升级为可测试的两阶段检索。

### 任务

1. 新增 QueryPlanner，将本节需求拆为有限意图：人物、事件、伏笔、场景；禁止无上限拼接全部 key_points。
2. 每个意图进行粗召回，合并候选到 top-10 或 top-20。
3. 增加 metadata 过滤与加权：项目、时间范围、角色、事件、章节。
4. 增加 reranker 接口，第一版采用可解释规则：
   - 向量分；
   - title/关键词匹配；
   - 角色交集；
   - 章节距离；
   - 同章重复惩罚；
   - 未来章节强制排除。
5. 若规则版仍不足，再实验 BM25 或 cross-encoder/LLM rerank；不得第一步就增加昂贵模型。
6. 动态选择 0–5 条，不为凑数加入低相关候选；设置总 token 预算。
7. 为每条最终候选生成 `reason` 和 `source_id`。

### 验收

- 在固定人工集上，Precision@5 不低于基线且 Recall@5 有提升；
- 推荐目标：Precision@5 ≥ 80%，Recall@5 ≥ 80%；若样本不足，不宣称统计显著；
- 后期章节指标不低于前期章节 15 个百分点以上；
- RAG 上下文 token 不高于旧版；
- 做 A/B 消融：无 RAG、旧 RAG、新 RAG。

### 回滚

保留旧检索器，通过配置按 task 固定版本，任务进行中不得切换。

## Phase 4：Context Broker 与 Writer 上下文减负

### 目标

减少重复和无关上下文，同时保持硬约束覆盖率。

### 任务

1. 新建 Context Broker，接收所有 `ContextItem`，负责：
   - 相关性筛选；
   - 状态冲突处理；
   - 去重；
   - 优先级排序；
   - token 预算；
   - 输出来源追踪。
2. 上下文分三层：
   - hard：硬约束原文，不允许自由摘要；
   - soft：结构化状态；
   - evidence：RAG/事件原文证据。
3. 删除 Prompt 中明显重复项：
   - `sub_description` 与 `key_points` 合并；
   - `style_examples` 与风格行为指令按需二选一；
   - `summary_context` 与 `handover_context` 去重；
   - 完整 world setting 只在需要时提供相关片段；
   - 势力、地图、物品等仅在本节相关时进入。
4. 先做“影子模式”：Broker 生成新上下文但 Writer 仍使用旧上下文，比较覆盖率后再切流量。
5. 所有 ContextItem 记录选择原因和被裁剪原因。

### 验收

- 硬约束覆盖率 100%；
- 必需角色状态覆盖率 100%；
- Writer 输入 token 相对基线降低至少 30%；
- 角色违反率和风格偏差不得恶化；
- 能从 task 日志还原 Writer 实际看到的完整上下文。

## Phase 5：Writer 受控工具调用

### 目标

当写前上下文不足时，允许 Writer 按需获取证据，而不是预先塞入所有信息。

### 工具接口

1. `get_character_state(character_ids, section)`
2. `get_relationship_state(character_a, character_b, section)`
3. `get_recent_handover(section, subsection)`
4. `search_story_memory(query, filters, max_results)`
5. `get_event_chain(event_id, depth)`
6. `get_foreshadowing_requirements(section)`
7. `get_style_constraints(section, subsection)`

### 约束

- 工具只读；
- 每小节设置调用数和 token 上限；
- 同参数调用缓存；
- 每次结果带 `source_id`、版本和置信度；
- 工具失败时返回结构化错误，不伪造空成功；
- 任务开始时绑定工具协议和 Prompt 版本；
- 暂不引入 MCP Server，先使用现有 LLM API 的 Function Calling；未来多应用共享工具时再评估 MCP。

### 实施步骤

1. 先给 LLMClient 增加通用 tool-call 循环及单元测试。
2. 首批只上线两个工具：`search_story_memory`、`get_character_state`。
3. 通过 feature flag 对固定任务做 A/B；确认收益后再增加其他工具。
4. 防止工具循环、无效重复查询和结果无限扩张。

### 验收

- 工具参数、返回值和错误分支均有契约测试；
- Writer 能在缺失信息时获取正确来源；
- 工具调用版相对 Broker-only 版提升至少一个核心质量指标；
- 平均额外延迟和 token 成本有明确报告；没有收益则关闭该工具。

## Phase 6：真实事件图与因果检索

### 目标

让因果检索基于正文中实际发生的事件，而不是仅依赖 Planner 里程碑。

### 任务

1. 每小节完成后提取 0–N 个实际事件，保存原文证据 chunk ID。
2. 将规划事件与实际事件匹配，标记 occurred/deviated/cancelled。
3. 创建有方向、有类型的 EventEdge；禁止把普通同章共现直接称为因果。
4. 建边必须附证据和置信度；低置信度边只作候选，不直接注入 Writer。
5. 因果召回与语义召回并行：因果召回负责语义不相似但逻辑相关的事件。
6. 两路结果进入 Context Broker 去重和预算控制。
7. 删除当前长期为空的伪因果日志，改为监控：图规模、边类型分布、查询命中率、空结果率。

### 验收

- 固定样本中实际事件提取准确率和边准确率经过人工标注；
- 因果检索在需要因果信息的 query 上有可测增量；
- 与 rerank RAG 做消融，证明不是重复能力；
- 若无显著增量，保留 EventGraph 作为规划/追踪系统，不宣称因果检索能力。

## Phase 7：角色一致性闭环

### 目标

区分稳定人设、动态状态和角色弧，做到写前约束、写后验证、状态更新。

### 任务

1. CharacterProfile 只保存长期稳定属性和不可违反约束。
2. CharacterState 保存位置、目标、情绪、知识、身体状态及生效章节。
3. CharacterArc 保存允许变化方向和里程碑，不覆盖当前状态。
4. 写前由 Broker 提供：出场人物、当前状态、不可违反项、本节允许变化。
5. 写后验证：
   - 稳定属性违反；
   - 无铺垫状态跳变；
   - 角色知道了不该知道的信息；
   - 关系阶段越级。
6. 验证通过后才更新 CharacterState；失败时保留旧状态并记录候选变更。

### 验收

- 人工角色集上的硬约束违反率下降；
- 合理角色成长不会被全部判成错误；
- 每个动态状态都可追溯到正文证据和生效章节。

## Phase 8：风格稳定性闭环

### 目标

把风格从 Prompt 描述变成可测的控制回路。

### 任务

1. 审计实际 StyleProfile 字段，统一文档和代码，停止混用“4维”和“50维”叙述。
2. 将风格拆为：目标 profile、Writer 指令、可选示例、写后实测。
3. 每小节或每章统计对话率、句长分布、段落长度、感官词密度等可计算指标。
4. 对不可直接计算的风格维度使用固定 Prompt 的 Judge，并保留人工抽样。
5. 区分“有意的节拍变化”和“无意的风格漂移”。
6. 只有偏差超过阈值才触发局部修改，禁止整节无条件重写。

### 验收

- 风格目标与实际值形成每章曲线；
- 固定样本重复生成的关键维度方差下降；
- 风格控制不显著增加角色违反率或上下文 token。

## Phase 9：持续评估与上线门槛

### 目标

让功能失效能自动被发现。

### 任务

1. PR/本地回归运行小型离线集，不调用昂贵模型或使用录制响应。
2. 手动发布前运行完整黄金故事评估。
3. 每个真实任务记录运行指标，但不默认对用户全文做昂贵 Judge。
4. 建立告警：
   - RAG 连续空结果；
   - 因果图有事件但连续无命中；
   - 工具调用循环；
   - Context Broker 裁掉硬约束；
   - 角色状态更新失败；
   - 风格偏差连续超阈值。
5. Prompt、检索策略、Broker 策略都按 task 固定版本，支持回滚和 A/B。

### 发布门槛

- 单元和集成测试全部通过；
- 硬约束召回率 100%；
- RAG Precision/Recall 不低于基线；
- 角色违反率不高于基线；
- 风格偏差不高于基线；
- Writer 上下文 token 至少降低 30%；
- 所有质量变化都有实验数据，不使用主观“看起来更好”作为唯一依据。

## 7. 测试矩阵

| 层级 | 测试对象 | 必测异常 |
|---|---|---|
| 单元 | chunk、query plan、metadata、rerank、budget、ContextItem | 空文本、缺字段、非法 section、超预算、重复来源 |
| 契约 | Writer tools | 参数错误、超时、无结果、来源已删除、版本不一致 |
| 集成 | Chroma、Redis、EventGraph、状态存储 | 重启恢复、重复写入、并发调用、部分失败 |
| 离线质量 | RAG、角色、风格、因果 | 词表陷阱、跨章回忆、多角色、合理成长、节拍变化 |
| E2E | 完整写作任务 | 停止/恢复、模型空响应、工具失败、检查点恢复、版本固定 |
| 消融 | 各子系统增量 | 无RAG/旧RAG/新RAG/Broker/工具/因果 |

## 8. Codex 执行规范

每个阶段按以下格式工作：

1. 开始前列出将修改的文件和不修改的边界。
2. 检查工作树，保留用户现有改动，不进行 destructive git 操作。
3. 先补失败测试或基准，再改实现。
4. 小步提交；一个提交只解决一个可描述问题。
5. 每次交付报告：
   - 修改内容；
   - 测试命令与结果；
   - 指标前后对比；
   - 新增风险；
   - 是否满足阶段验收；
   - 下一阶段是否应该继续。
6. 如果数据不支持原方案，停止实施并更新本计划，不为完成清单强行上线。
7. 不得在同一阶段同时更换 embedding 模型、改 query、改 reranker 和改 chunk；否则无法归因。

## 9. 建议的执行批次

### 批次 A：先让问题可见

- Phase 0：基线；
- Phase 1：状态与废案审计；
- Phase 2：RAG 可观测性。

完成后再决定具体检索策略，不提前重构 Writer。

### 批次 B：修检索和上下文

- Phase 3：两阶段检索；
- Phase 4：Context Broker。

这是第一轮可产生明显质量和 token 收益的改造。

### 批次 C：增加按需能力

- Phase 5：受控工具调用；
- Phase 6：真实事件图和因果检索。

先证明工具调用有增量，再扩工具数量。

### 批次 D：形成质量闭环

- Phase 7：角色一致性；
- Phase 8：风格稳定性；
- Phase 9：持续评估和发布门槛。

## 10. 明确不在本计划范围

- SaaS 多租户、认证、计费；
- SQLite 到 PostgreSQL 迁移；
- ChromaDB 到其他向量数据库迁移；
- Kubernetes 或微服务拆分；
- 新增抽卡、地图、物品等产品功能；
- 无评估依据地更换主 LLM 或 embedding 模型；
- Prompt 管理后台 UI。

## 11. 最终成功标准

项目完成后，应能回答并用数据证明：

1. Writer 写某小节时看到了哪些信息，为什么看到，来源是什么；
2. 必要信息是否被召回，无关信息是否被过滤；
3. 角色变化是合理弧线还是无依据漂移；
4. 风格变化是叙事节拍需要还是模型失控；
5. RAG、交接笔记、工具调用和因果检索分别贡献了多少；
6. 删除任一子系统后质量是否下降；
7. 每次改动能否回滚到已知有效版本。

只有能够回答这些问题，系统才从“功能堆叠的多 Agent Demo”变成“可验证的长篇一致性写作系统”。

## 12. 执行记录

| 日期 | 阶段 | 结果 | 指标变化 | 决策 |
|---|---|---|---|---|
| 2026-07-17 | Phase 0 | 建立离线可复跑基线；复用 18 节黄金故事（SHA-256 固定）；RAG schema v2；3 角色×10 约束；6 个风格样本；输出 JSON/Markdown 报告 | RAG P@5=68.0%，R@5=66.7%，后期 P@5=40.0%；客观风格区间偏离率=0%；历史逐小节 token 覆盖=0%（Redis 不可用）；unit=99/11、integration=7/0 | 不修改检索排序；角色标签仅 Codex 临时标注，人工确认覆盖 0%，不得作为发布门槛 |
| 2026-07-17 | Phase 1 | 新增 ContextItem/StoryChunk/EventEdge 契约和状态所有权审计；ExperienceTimeline 改为 event_store 兼容层；MemoryFuser 标记 deprecated；活动文档改为 4 个主要风格控制量 | ExperienceTimeline 从双写两个 DB 降为单一 events.db 权威源；新增契约/兼容测试全部通过 | 保留 experience.db 作为可恢复备份；因 MemoryFuser 含用户未提交修改，不物理删除；动态 CharacterState 仍是明确遗留项 |
| 2026-07-17 | Phase 2 | `search_with_meta` 增加 ID/distance/score/metadata/filter/耗时/粗候选 trace；增加空块与重复块防护、生命周期清理、Writer 利用率启发式和 Chroma 隔离基准 | 新增定向测试 20/20 通过；全量 unit=115/11（无新增失败），integration=7/0；Chroma 1.5.9 在 100×100 时 filter=3.736ms、per-task=7.667ms，后者 2/30 查询失败 | 保持共享 collection + task_id filter；`RAG_TRACE_CANDIDATE_K=0` 默认维持旧生产查询；完整 1/10/100×100/1000/5000 矩阵未完成，不进入 Phase 3 排序改造 |
| 2026-07-18 | Phase 3 前置收尾 | 为 19 条 hard 角色规则生成留空人工审阅表；ContextManager 保留最近 3 小节原文并兼容旧 checkpoint；风格契约固定为 4 个主旋钮；记录 5,000-chunk 矩阵临时豁免 | 定向契约测试 33/33；全量 unit=127/0，integration=7/0；未恢复 `running_summary` 或旧 50 维字段 | 保持共享 collection + `task_id` filter；完整矩阵在 Chroma 升级、规模/延迟/隔离异常或迁移提案时强制重跑；不开始 Phase 3，等待用户人工确认角色标签 |
| 2026-07-18 | Phase 3 人工标注入口 | 固化 19 条 hard 人工结果：17 条 `human_confirmed`、2 条 `human_flagged_issue`；`linwan-10` 改为关系阶段约束并补第16节；`jiqing-10` 明确现实风险缺口；三类结果分离；新增标注一致性测试 | 人工覆盖率=100%，当前正文 hard 违反率=2/19（10.53%）；标注测试=8/8、unit=127/127、integration=7/7；机械计数/重复句式/情绪层次不足登记为独立风格基线问题 | Phase 3 入口条件满足，可在用户明确指令后启动；不得在 Phase 3 中顺带修改 Writer、恢复旧 50 维风格字段或把两个已知正文缺陷抹掉 |
