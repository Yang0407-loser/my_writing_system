# 长篇写作一致性系统重构执行计划

> 状态：Phase 3 已以“实验未晋级、生产保持 legacy”正式收口；Phase 4 状态为 `paused_by_generation_evaluation_infrastructure`；Phase 4R 最终真实写作试验通过，SceneSpec 已完成最小 canary 接入并取得 1 个真实任务样本，生产默认继续 `legacy_full`；Mandatory Event 已经真实验证为默认 warn、自动重写 0；Writer增量Section Review已默认关闭并通过1次真实运行验证；Writer Condense warn 已通过1次真实四小节验证并晋级为默认，legacy保留为显式回退；Shared Typed Post-Write Extraction 的唯一真实shadow任务已完成，但因语义覆盖与端到端延迟门槛未通过而标记为 `real_shadow_not_promoted`，默认保持off且不再追加同形Demo；Character State Update审计因实质更新调用占比50%未达到停用门槛，保持legacy，其Writer→Coordinator→checkpoint→Reviewer传播已通过单次真实任务验收并关闭；角色弧 Contract V2 状态为 `experimental_not_promoted`，生产默认仍为 v1；StateFrame 状态为 `paused_by_upstream_state_contract`；BoundaryValidator继续默认关闭；Phase 5、Phase 6暂停；Phase 8 Batch 1已完成纯离线风格可观测性基线
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

> 2026-07-18 第一批状态：QueryPlanner、多意图粗召回、可解释规则重排和 Writer 影子 trace 已实现。章节级代理指标由旧链路 P@5=44.0% / R@5=43.3% 提升到 P@5=71.1% / R@5=70.0%，后期章节代理 P@5=60.0%；但新版入选的 38 个 chunk 与旧人工候选的精确文本标签复用率为 0%，所以人工 Precision@5 门槛尚不可验证。保持 `RAG_PHASE3_SHADOW=false` 默认值和旧 Writer 输入，不切换生产检索，不开始 Phase 4。

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

> 当前状态：`paused_by_generation_evaluation_infrastructure`。现有执行环境无法继续完成合规的私有输入生成对照，因此暂停结论不等于 Context Broker 架构失败。生产继续使用 `legacy_full`；ContextBroker、ContinuityRiskGuard 和 Batch 1～3.5 全部实验资产保留，等待可用的生成质量评估基础设施。

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

### 原文与摘要决策

- `ContextManager` 继续保存“最近 3 小节原文＋交接笔记”，不恢复旧 `running_summary`。
- 这不是遗漏：Phase 3 Batch 2E 的句子压缩虽然节省 82.84% token，但完整事实证据仅保留 1/11；2F 的结构窗口和 2G 的事件块也未在真实检索中同时通过证据完整性、召回和 token 门槛。
- Phase 4 优先选择完整 `ContextItem`，不在正文内部截断、摘要或改写。若整项选择出现生成退化，必须先区分实测退化与启发式理论风险，并验证最小恢复集合；只有选择性整项恢复仍无法兼顾质量与 token 门槛时，才另行授权测试带 source/hash/version 的可追溯节级摘要。不得直接恢复不可审计的滚动摘要。

### 验收

- 硬约束覆盖率 100%；
- 必需角色状态覆盖率 100%；
- Writer 输入 token 相对基线降低至少 30%；
- 角色违反率和风格偏差不得恶化；
- 能从 task 日志还原 Writer 实际看到的完整上下文。

## Phase 4R：Writer 职责拆分

> 当前状态：最终真实写作试验已完成并通过全部预设门槛，Phase 4R 的最终建议为保留 SceneSpec 路线。该结论只来自 4 个连续真实小节，不能宣称全面生产质量已经证明；SceneSpec 仍未进入生产 Writer messages，BoundaryValidator 继续默认关闭，生产继续使用 `legacy_full`。

### 核心假设

先把当前单体写作内环拆成可验证边界，再单独验证短小、可追溯的 SceneSpec 是否能降低 Writer 的事实推理负担。不得把职责拆分回归与上下文策略效果混在同一批次归因。

### 目标结构

`Coordinator → SubsectionPipeline → StoryStateView → SceneCompiler → ContextBroker → PromptBuilder → ProseWriter → SceneValidator → StateCommitter`

- R1 只提取 `PromptBuilder`、`GenerationController`、`StateCommitter` 和 `SubsectionPipeline`；Writer 保持兼容 facade。
- R2 才允许实现只读 `StoryStateView` 和 shadow-only `SceneSpec`，不新建第二套永久事实库。
- R3 才允许比较 legacy、budgeted Broker 和 Broker+SceneSpec 的生成质量。
- Validation 仅允许单任务有限 canary；每批必须单独授权。

### 存储边界

- EventGraph 当前只可靠承载 `arc_milestone`，不得描述为全部故事事实的权威库。
- WorldState、规则、关系、伏笔、地图、物品、支线、经历、handover、ContextManager 和 Chroma 保持现有所有权。
- R2 的 StoryStateView 只做只读投影和来源追溯，不迁移数据库，不静默解决冲突。
- ContextManager 保持最近 3 小节原文＋交接笔记，不恢复 `running_summary`。

### 批次门槛

- R1：10/10 Writer messages hash 不变；公开接口、生成参数、状态写入顺序和 checkpoint 兼容；全量测试零失败。
- R2：SceneSpec 仅 shadow，字段来源可追溯率 100%，unknown 不得升级为事实，平均 200～500 estimated token，生产 messages hash 不变。
- R3：Broker+SceneSpec 相对 legacy 输入至少下降 20%，连续性不差于 legacy，新增 hard/关系违规为 0。
- R4：只读现有 R3 产物，逐缺陷给出允许枚举内的归因、source/hash 和职责所有者；不得调用 LLM、恢复上下文或修改生产；完成后停止。
- R5：预测阶段禁止读取独立盲审与评估答案，先冻结私有 predictions hash，再由独立 evaluator 计算候选级去重指标；Boundary Recall 必须 100%、Precision 至少 80%、Q7/Q8 状态分类和证据追溯必须全部通过。
- R6A：Validator 只能在状态提交成功后以默认关闭的 shadow hook 运行；迁移后 R5 原始预测 hash 必须不变；异常、skip 和 sink 失败不得影响正文、checkpoint 或重试；真实样本数保持 0。
- 任一批完成后停止；不得自动开始下一批、Phase 5 或 Phase 6。

### Batch R2 结果

- 新增不可变 `SourceEvidence`、`StateAssertion`、`StoryStateSnapshot` 和 `SceneSpec` 契约；状态严格区分 `confirmed/planned/unknown/conflicted`。
- `StoryStateView` 只调用现有 WorldState、EventGraph、规则、关系、伏笔和 handover 的查询接口；不消费 warning、不写 Redis/SQLite/Chroma，也不建立第二套永久事实源。
- Q4/Q6/Q7/Q8 的 shadow SceneSpec 平均 273.0 estimated token（225～311）；来源追溯率 100%，unknown 保留率 100%。
- Writer 未导入 R2 模块，未调用 LLM，R1 冻结的 10/10 production messages hash 保持不变。
- R1 暂留的 `_legacy_generate_with_retry` 测试 oracle 已删除，避免生成逻辑永久双份维护。
- R2 只证明状态契约、保守编译和追溯机制可用；没有生成正文质量证据，不得据此切换生产。R3 仍须另行授权。
- R3 准备阶段已冻结 Q4/Q6/Q7/Q8 的 A=`legacy_full`、B=`budgeted_broker`、C=`budgeted_broker+SceneSpec`，总计12次；公开清单不含私有正文，实际生成必须由获准环境显式确认后执行。

### Batch R3/R4 结果

- 外部 Agent 已按冻结配置完成 12 次生成，独立盲审 provenance 为 `independent_agent_blind_review`；原始正文、messages 与匿名映射继续只保存在 gitignored runtime。
- A/B/C 平均输入分别为 12428.75 / 8591.25 / 8880.50 estimated token；B、C 相对 A 分别下降 30.88% / 28.55%。
- 盲审首选：A 赢 Q4、Q6，C 赢 Q7，B 在 Q8 仅为三组失败中的相对较优；A/B/C 目标完成分别为 2/4、2/4、3/4，但 C 没有满足“连续性不差于 A、无新增 hard/关系问题”的 R3 门槛。
- R4 对 22 条盲审分类标签建立逐项 source/hash 归因，合并重复分类后得到 15 个概念缺陷：`writer_instruction_noncompliance` 4、`writing_request_boundary_ambiguity` 2、`missing_scene_spec_fact` 1、`unrelated_generation_variance` 8；没有证据支持 `dropped_context_dependency`。
- Q4 的 C 明确违反 SceneSpec 的未知亲属禁令；Q8 的 C 明确违反“止于边界反思”的截止点；Q7 的 C 则是唯一完成本节两个关键动作的候选。现有证据不能证明 token 减少直接造成 Q4/Q8 退化，约 9k token 的 budgeted 上下文只保留为 shadow 候选。
- 下一优先级是使用现有 12 份输出离线验证生成后 `boundary_validator` 的检测能力；不得同时加入 Repair、恢复旧小节或修改 SceneSpec。需另行授权，不自动开始。

### Batch R5 结果

- Predictor 只读取 R2 SceneSpec、当前写作需求、R3 匿名生成清单和 12 份正文；不读取独立盲审、R4 归因或 arm 映射。预测冻结原始字节 SHA-256 为 `fb6e21589d362b9e43f8da00ed8f99709c2d90804a2c72be63e691553baa42c0`。
- 冻结后 evaluator 才读取独立盲审：boundary TP/FP/FN/TN=3/0/0/9，Q7 required-event=1/0/0/2，Q4 unsupported-fact exploratory=2/0/0/1；三项 Precision/Recall/F1 均为100%，证据 span 与 source/hash 追溯率100%。
- Q7 的 current/past/future 行为状态三组均分类正确，Q8 三组越界全部检出；unsupported-fact 仍是探索性能力，不作为发布门槛。
- 当前只有 4 场景、12 候选且规则依赖冻结契约锚点，不能宣称通用语义 Validator 成熟。机械门槛通过只允许建议另行授权 shadow 接入；生产、SceneSpec、Writer、Prompt、RAG 均不改变，不开始 Repair、Phase5或Phase6。

### Batch R6A 结果

- R5 v1 确定性逻辑已提取到 `app/writing/boundary_validator.py`，benchmark 改为单向导入；重新生成的原始预测 SHA-256 仍为 `fb6e21589d362b9e43f8da00ed8f99709c2d90804a2c72be63e691553baa42c0`。
- `WRITER_BOUNDARY_VALIDATOR_SHADOW=false` 为默认值；关闭时不构建合同、不运行 Validator、不写记录。hook 严格位于 `StateCommitter` 和 `record_commit` 之后。
- typed SceneSpec 适配保持 unknown 状态和 source/hash；缺少 provider、SceneSpec 或可执行规则时安全 skip。异常只形成脱敏 `shadow_error`，不传播、不回滚、不重试、不 Repair。
- Writer messages、正文返回、Prompt、RAG、ContextManager、checkpoint 顺序和幂等键均不改变；本批 Writer/LLM 调用 0，真实 shadow 样本 0。
- 全量回归 unit=229、integration=9、quality=85、compileall通过。完成后停止；只能等待另行授权 R6B 真实 shadow 采样，不开始 Repair、Phase5或Phase6。

### Phase 4R 最终真实写作试验

- 固定唯一变量：A=`legacy_full`，B=`legacy_full + SceneSpec`；使用接下来真实创作的 4 个连续小节，共完成 8 次 Writer 主调用。Prompt、RAG、ContextManager、规则、风格、模型和生成参数保持相同，候选正文及映射仅保存在 gitignored runtime。
- 人工匿名审阅 provenance 为 `user_real_writing_acceptance`。B 在 4 个场景中 3 个不差于 A，具体首选为 B/A/B/B；B 的目标完成数为 3/4，A 为 2/4。
- A/B 总缺陷分别为 10/4；hard 违规 1/0、关系违规 1/0、连续性错误 3/1、事实错误 3/2、事件顺序错误 1/1、越过停止边界 1/0。B 没有新增任何门槛内错误，3 个场景记录了具体正面作用。
- 人工修改字符数和时间未测量，均明确记录为 `not_measured`，不作为 go/no-go 门槛，也没有把 `null` 当作 0。
- 全部七项门槛通过，最终建议为 `retain_scene_spec_experimental_route`。该结果只支持保留路线并另行设计最小生产接入，不授权直接切换生产、启用 Validator、实现 Repair 或开始 Phase 5/6；Phase 4R 到此强制停止，不追加解释性批次。

### SceneSpec 最小生产 Canary 接入

- 冻结最终真实试验语义，将 outline-only `OutlineSceneSpecProvider` 迁入生产；历史试验 helper 改为调用同一 provider，四个真实 SceneSpec hash 与 230/351/277/93 token 全部保持。
- 新增 `WRITER_SCENE_SPEC_MODE=off` 与空白 task allowlist；`off` 不调用 provider，`shadow` 只编译不注入，`canary` 只对白名单 task 在最终 user message 末尾追加完整 SceneSpec。
- 编译或注入失败、非白名单、结构缺失、超过400 estimated tokens、来源不可追溯时返回原 PromptArtifact；不阻断、重试或改变提交顺序。日志只含 task hash、spec hash/token/source IDs和fallback，不含正文/messages/Prompt。
- 已完成1个真实canary任务，但样本仍不足以授权全面切换；默认生产继续`legacy_full`，不启用StateFrame、ContextBroker、BoundaryValidator或Repair。

## StateFrame：Writer 当前状态职责拆分

> 当前状态：Batch 2 已完成真实冻结状态源覆盖审计，诊断为 `upstream_state_contract_required`。StateFrame 未进入 Writer messages，未调用 LLM，生产继续 `legacy_full`；未获得进入生成 A/B 的条件。

### 职责边界

- StateFrame 只回答“本小节开始前，当前世界是什么状态”，字段限定为时间、地点、人物在场、持久状态、关系阶段、未闭合事件和 unknown/conflicted。
- SceneSpec 继续回答“本小节应该发生什么”，持有 planned events 与写作边界；hard 角色/关系规则继续由现有规则系统与 SceneSpec 消费。
- StateFrame 复用 `StoryStateSnapshot`、`StateAssertion` 和 `SourceEvidence`，不建立第二套数据库，不解析自然语言关键词来猜状态。
- planned events、hard constraints、历史 arc milestone 和未识别 predicate 必须进入 `excluded_assertion_ids`，不得静默混入当前状态。

### Batch 1 结果

- 新增不可变 `StateFrame` 和确定性 `StateFrameCompiler`；分类完全依赖显式 predicate，unknown/conflicted 保持原认识状态。
- 4 类合成契约场景覆盖时间/未知、地点/在场、持久状态/关系、未闭合事件/冲突；来源追溯率 100%，规划事件与 hard 规则排除检查通过。
- 合成结构平均 33 estimated tokens（26～40），仅用于确认渲染器不会异常膨胀，不得解释为真实 Writer token 降幅。
- Writer、Prompt、SceneSpec compiler、ContextManager、RAG、Validator 和生产调用链均未修改；Writer/LLM 调用 0。
- 下一步只有另行授权的“真实状态源覆盖审计”：统计现有 WorldState、handover、关系和事件源中有多少状态具备可分类 predicate，以及缺失来自数据契约还是状态本身。不得直接注入 Writer 或开始生成 A/B。

### Batch 2 结果

- 使用 Phase 4R 最终试验冻结的单一章节生成前 checkpoint 审计4个真实连续小节；没有读取候选正文、A/B映射、人工评审或历史缺陷标签。
- 每帧有7条 explicit、15条 generic 和2～5条 unclassified状态；generic占included state的68.18%，Frame平均1,768 estimated tokens。4个小节共享同一节前状态，不能视为4个独立连续快照。
- WorldState 14条全部只能进入通用`world_fact`（13 unknown、1 confirmed）；5条character arc current state可显式分类但缺少有效期/认识状态；handover为一组3字段长字符串，relations/foreshadowing/locations均无可用状态。
- 追溯、unknown保留、planned/hard排除、重复分类和确定性全部通过；但generic state多数门槛失败。每帧与SceneSpec confirmed/open重复9条、与legacy WorldState/handover重复14/3个source；接管confirmed/open后理论仍净增加约754 token。
- 诊断固定为`upstream_state_contract_required`。不得扩关键词或进入生成A/B；下一步只能另行授权最小上游状态契约设计，不实现迁移或写入。

## Shared Typed Post-Write Extraction：下一优化入口

> 当前状态：`real_shadow_not_promoted`。唯一授权的真实shadow任务已经完成：隔离和逐字证据追溯通过，但字段语义、稳定ID、粒度和消费者时序覆盖不足，且新增56.6秒提取延迟。旧handover/人物/关系/经历提取链及全部权威存储保持不变，配置默认off；不再追加同形Demo，不允许直接替换生产消费者或启动StateFrame生成实验。

### 目标与依据

- 将小节写作闭环收口为“1次正文主调用 + 1次共享结构化状态提取”，Writer 只负责 prose 生成，不再让多个模块分别重读同一正文并独立解释状态。
- 统一现有 handover、人物状态、关系变化、地点/时间状态、未完成事件、经历和伏笔变化的提取结果，为下一小节提供一致、短小、可追溯的状态输入。
- 当前可确认的生产收益主要来自减少重复调用：增量 Section Review 已去除基线7,495 known token，Condense 已去除基线8,998 known token；但 Writer 单次正文输入仍约12,406.4 estimated token，生产仍为`legacy_full`。本路线先补齐可靠结构化上游，再重新评估 StateFrame 或上下文删除，避免重演有损压缩导致事实丢失。
- StateFrame Batch 2 已证明现有上游状态中 generic 占68.18%，handover是长字符串，关系/伏笔/地点缺少可用结构；因此不应先裁最近原文，也不应继续扩关键词猜状态。

### 目标架构

```text
Outline / key points
        -> deterministic SceneSpec
        -> Writer subsection draft (exactly one primary call)
        -> StateCommitter
        -> Shared Typed Extractor (one call)
             -> handover
             -> character state changes
             -> relationship changes
             -> time/location/presence
             -> open/closed events
             -> experience and foreshadowing changes
        -> existing authoritative stores/adapters
```

### 最小契约

- 定义单一`PostWriteStateBundle`，至少包含`task_id`、`section/subsection`、`output_hash`、`schema_version`、`source_refs`和分类后的状态变化。
- 每条状态必须有稳定ID、predicate、subject、typed value、`confirmed/unknown/conflicted`状态、effective range、evidence span、source ID/hash和confidence。
- 不允许把未确认内容升级为事实；解析失败或证据不足时保留`unknown`，不得猜测具体人物、时间、地点或事件结果。
- Bundle 只是统一提取产物，不建立第二套永久数据库；现有权威存储仍由各自适配器负责写入和兼容旧checkpoint。

### 实施顺序

1. 只读审计现有 handover extraction、CharacterManager状态更新、关系/经历/伏笔提取的调用位置、输入hash、输出消费者、同步/后台顺序、token和延迟；不得仅凭调用数量判定可合并。
2. 冻结`PostWriteStateBundle`契约和旧消费者适配边界；先实现纯解析、校验、追溯和NoOp/Shadow sink，不修改Writer Prompt或生产写入。
3. 新共享提取器在shadow中每小节最多调用1次；旧提取链继续生产生效。对同一真实任务并排记录字段覆盖、冲突、unknown、来源追溯、调用数和延迟，不读取人工答案参与运行时提取。
4. 只有共享Bundle覆盖所有仍有生产消费者的旧字段、无confirmed事实丢失、unknown不被升级且真实调用数下降时，才允许另行授权canary替换旧提取调用。
5. 替换时按消费者逐类迁移，不在同一批同时删除legacy上下文、启用StateFrame、修改SceneSpec、Prompt、RAG、Validator、Repair或最终Review。
6. Bundle稳定进入现有权威存储后，才重新审计StateFrame真实覆盖，并决定是否能安全减少最近3小节原文或其他`legacy_full`上下文。

### 真实 shadow 结论（2026-07-21）

- 4个小节产生4次共享提取调用，实际15,141 tokens、56.629秒；占Writer tokens的64.57%、任务总tokens的29.98%、总耗时的15.75%。
- 共接受55条变化、拒绝6条无法逐字回溯的候选；接受项证据追溯率100%，unknown/conflicted均为0。
- 类别分布为：character_state 28、location_state 7、character_presence 5、event 5、relationship 3、temporal_state 3、experience 3、foreshadowing 1、handover 0。
- 同任务潜在可替换legacy链为7次调用、21,703 known tokens；若能完整替换，理论上可省6,562 tokens（30.24%），但当前未证明完整覆盖，因此不得计为生产收益。
- 关键缺口：没有显式handover/open-thread产物；人物状态缺稳定character_id和arc字段；关系、经历、事件尚未满足权威存储的ID、聚合和时序契约；55条中包含大量瞬时动作，不能仅凭证据存在就持久化。
- 决策：本轮不晋级、不替换消费者、不跑第二个同形Demo、不扩Prompt或关键词。若未来重启，只允许先做离线契约与consumer adapter重构，证明durable/transient分类、稳定ID和open-thread语义后再申请真实调用。

### 验收与停止条件

- 每小节正文主调用保持恰好1次，共享状态提取最多1次；不得增加替代重试。
- 所有Bundle及写入结果的source/hash/evidence span追溯率为100%，旧checkpoint和幂等键兼容。
- 现有生产消费者需要的confirmed状态覆盖率为100%；缺失、冲突和unavailable必须单独报告，不能当作成功。
- 相对同生命周期旧链，状态提取HTTP调用数与端到端延迟必须明确下降；无法证明合并收益时停止，不进入canary。
- 一个正常真实任务出现任何confirmed状态丢失、错误关系/事件写入或下一小节连续性退化时，立即保留旧链，不通过扩Prompt、加关键词或重复调用追逐通过。
- 最多允许1个工程/离线契约批次和1个真实shadow验证；未通过即复盘核心假设，不继续扩展纸面测试矩阵。

## Character Arc Contract V2：规划噪声收缩

> 当前状态：`experimental_not_promoted`。生产默认 `CHARACTER_ARC_CONTRACT_VERSION=v1`；续写任务验证了legacy兼容去伪边，但没有从`character_arcs`阶段运行新的V2 Planner，不能评价V2分类质量。

- 角色弧不是纯观测资产：`CharacterFormatter.build_arc_context`、`EventGraph.query_relevant/pre_check` 会把 milestone 注入 Writer，边还参与 RAG 因果扩展；handover提取会额外读取最多10个弧线事件。
- 两个固定真实任务合计27个legacy milestone、188次建边操作；其中171次来自同节两两连接，带方向、类型、来源和因果依据的边为0；弧线事件文本合计约1,795 estimated tokens。
- V2只允许`hard_arc_transition`、`soft_arc_progress`进入事件上下文；只有字段完整且来源可追溯的hard进入`pre_check`强制块。observational、ordinary plot、unsupported和unresolved不进入弧线事件上下文。
- 每角色每章/section最多2个hard，超限或字段不完整时降级为soft并记录原因；旧checkpoint无classification时只通过非持久化兼容视图解释为soft，不回写、不自动升级hard。
- V2禁止同节两两建边和隐式同节因果扩展，只保留explicit causal、explicit dependency和状态连续的ordered hard transition；每条边必须有rationale和source/hash。
- legacy结构缺少before/trigger/after和provenance，因此离线审计中27项均为unresolved、结构可证明hard为0；不复用旧人工/辅助评估标签反向分类。
- 真实续写任务`3454d80e…`从writing开始：12条旧milestone均兼容解释为soft，hard=0，source ID/hash覆盖0/12，V1理论建边73次而V2实际边数0；Mandatory Event重试0。正文目标约完成3/4，第4小节未完成首次相遇且重复上一场景。
- Writer token从33,084降至32,442（约-1.9%），变量并未隔离，禁止归因于V2。本轮不宣称成功或失败，也不自动追加Demo；未来只有用户明确授权“全新任务、从character_arcs阶段开始”时才允许重新验证。

## Phase 5：Writer 受控工具调用

> 当前状态：暂停。前置门槛是 Phase 4 Broker 策略必须已固定，并通过独立生成质量验证或有限 canary。未满足时 Phase 5 保持暂停，禁止把“Broker 是否删错信息”和“Writer 是否正确决定调用工具”两个未验证假设叠加在同一实验中。

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

> 当前状态：暂停。不得在 Phase 4 生成质量验证基础设施缺失、Phase 5 尚未解锁时叠加新的事件图生产假设。

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

> 当前状态：Batch 1 仅建立确定性可观测性基线。它不修改 Writer、Prompt 或正文，不恢复旧 50 维字段，不调用 LLM，也不触发风格纠正。

### 目标

把风格从 Prompt 描述变成可测的控制回路。

### 任务

1. 审计实际 StyleProfile 字段，统一文档和代码，停止混用“4维”和“50维”叙述。
2. 将风格拆为：目标 profile、Writer 指令、可选示例、写后实测。
3. 每小节或每章统计对话率、句长分布、段落长度、感官词密度等可计算指标。
4. 对不可直接计算的风格维度使用固定 Prompt 的 Judge，并保留人工抽样。
5. 区分“有意的节拍变化”和“无意的风格漂移”。
6. 只有偏差超过阈值才触发局部修改，禁止整节无条件重写。

### Batch 1 确定性基线

- 固定黄金故事按 18 章、52 小节统计对话占比、句长/段长分布、机械起句、感官词、心理说明词、完全重复句/段，以及连续短句和连续结构签名。
- `dialogue_ratio` 直接映射对话字符占比；`sentence_preference` 映射句长分布；`sensory_density` 只映射固定词表密度代理。
- `emotion_intensity` 没有可靠的确定性反推指标；心理说明词频只作为观察信号。
- “情绪层次不足”保持人工/LLM 判断项，不使用关键词启发式自动评分。
- IQR 异常只表示章节相对全书的分布离群，不自动等同于质量缺陷；机械计数与重复句式的具体影响仍需人工阅读。

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

### 统一停止规则

1. 同一核心假设连续 3 个批次未通过主门槛，必须暂停并重新评估方向，不得继续扩大同类参数网格。
2. 每个 Phase 默认最多 3 个实现批次＋1个验证批次；超过该上限必须向用户报告累计收益、失败归因和机会成本，并取得明确授权。
3. 新批次必须验证新的可证伪假设；如果只会再次证明已有失败结论，不得立项。
4. 达到停止条件后只能选择：关闭方向、改变核心假设，或等待外部条件变化；不得为完成计划清单而继续 shadow 实验。

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
| 2026-07-18 | Phase 3 第一批（shadow） | 新增四类意图 QueryPlanner、每意图现有向量粗召回、候选合并和可解释规则重排；每条候选记录来源 ID、分项/最终得分及入选/淘汰原因；Writer 只记录新旧结果，仍消费旧 top-5 | 10 条样本章节代理：旧 P@5=44.0% / R@5=43.3%，新 P@5=71.1% / R@5=70.0%，后期新 P@5=60.0%；平均返回 5.0→3.8，估算 token 609.9→470.3，延迟 2568.9→6361.3 ms；新候选人工标签覆盖 0%；unit=135/0、integration=8/0、quality=8/0 | 召回/后期代理指标达到方向性目标，但人工 Precision 门槛无效且延迟约 2.48×；保持 shadow，不切生产，不开始 Phase 3 第二批或 Phase 4；下一入口是人工标注新版候选 |
| 2026-07-18 | Phase 3 新候选人工审阅入口 | 从冻结的 shadow 报告提取 10 条 query 下 38 个入选候选，按 source ID 从现有 Chroma 只读回填完整 chunk 正文；同 query 去重、跨 query 保持独立；保留 query、意图、must-recall facts、得分与原因 | 审阅表 query=10、candidate=38、human_reviewed=0；一致性测试校验数量、ID、正文 hash、查询上下文、分数、原因、空人工字段和防覆盖行为；unit=135/0、integration=8/0、quality=13/0 | 等待用户填写 `human_relevant`、`supports_which_fact`、`review_note`；保持 shadow，不修改权重、Writer 或其他冻结系统，不开始第二批或 Phase 4 |
| 2026-07-18 | Phase 3 第一批人工评估收尾 | 二审并固化 38 条新候选：21 相关、17 不相关；分别计算人工候选精度、人工相关候选的 gold-section recall、事实覆盖率与后期精度；输出逐 query 和失败层级机器报告 | 人工 P@5=55.26%（21/38）；可比章节 R@5=43.33%（13/30）；事实覆盖=23.08%（6/26，独立诊断指标）；后期人工 P@5=26.67%（4/15）；Q3 返回 0；17 个误召回中 13 个角色分饱和；unit=137/0、integration=8/0、quality=15/0 | 三项质量门槛均失败，仅 Writer 不变通过；保持 shadow，不切生产；第二批只建议做意图/角色分/阈值/query 数/token 预算消融，且须显式授权，不开始 Phase 4 |
| 2026-07-18 | Phase 3 Batch 2A/2B 离线诊断及定向复核 | 对冻结 trace 和 38 条人工标签复跑 10,368 个组合并做按 query 留一验证；随后由 Codex 辅助复核 2 条新进入 top-5 的候选，两条均为间接相关但不支持完整 must-recall fact | 40 条已审阅 query-candidate 对中最后 2 条 provenance=`codex_assisted_review`，不是独立人工金标准；去 scene P=65.71%、保留=100%；去 character/max3 P=66.67%、保留=69.57%；事实覆盖不变 | 最佳闭集精度仍低于 68%；辅助标签只作诊断，不能满足人工发布门槛；继续 shadow，不调生产权重、不开始 Phase 4 |
| 2026-07-18 | Phase 3 Batch 2C 真实 V1/V2 shadow 对照 | 新增未接入 Writer 的 QueryPlannerV2：最多 2 条查询，character 必须带动作/关系/状态锚点，scene 仅显式地点/时间触发；角色分按 metadata/标题/正文次数分级并设置 600-token 上限；用现有 embedding 与共享 task-filtered Chroma 真实重跑 10 条 V1/V2 | V1→V2：闭集 P 55.26%→62.50%，池化已知相关保留 91.30%→43.48%，后期 P 26.67%→20.00%，gold-section 候选池代理 76.67%→80.00%，token 470.3→317.6，真实延迟 6107.2→4914.7ms；未知候选=12；unit=145/0、integration=8/0、quality=24/0 | 即使 12 条未知全部相关，池化保留率上界也仅 62.86%，不可能达到 90%；不生成无效人工阅读任务，V2 保持实验，不切生产、不开始 Phase 4 |
| 2026-07-19 | Phase 3 Batch 2D 2×2 损失归因 | 真实执行 V1/V2 Planner×V1/V2 Reranker 四组合；为每条丢失相关候选记录 source ID、正文、V1 排名/得分、V2 状态和损失层级；gold/must-recall 仅用于检索后评估 | P1R1/P1R2/P2R1/P2R2 的闭集 P 分别为 55.26%/58.33%/65.00%/62.50%，保留率 91.30%/60.87%/56.52%/43.48%；完整 V2 丢失 11 条：粗召回 miss=6、低于阈值=4、非角色门槛=1、token/top-k/future=0；unit=146/0、integration=8/0、quality=27/0 | Planner V2 单独损失 9 条，Reranker V2 在 V1 Planner 下损失 7 条；所有组合乐观上界均无法通过全部门槛，不生成复核题；两条 V2 路线均不晋级，继续 shadow，不开始 Phase 4 |
| 2026-07-19 | Phase 3 Batch 2E 去重与证据级压缩 | 固定 V1 Planner/召回/selected source，实现未接入 Writer 的确定性 ContextCompactor；句子 Jaccard 近重复检测、查询相关句＋邻句抽取、精确 source/字符区间和 400-token 软预算；对11个已有事实支持项做 Codex 辅助核查 | source=38/38、已知相关=21/21、事实支持来源=11/11、后期 source=100%、偏移追溯=100%；近重复组=0，去重 token 470.3→470.3；证据压缩 token 470.3→80.7（-82.84%），但事实证据仅保留1/11；unit=150/0、integration=8/0、quality=31/0 | 句子抽取把人物/关键词与关键谓词、金额、回应切开，证据门槛失败；80.7 token 只作为负面基准，不自动扩回全文、不进入 Batch 2F、不切生产、不开始 Phase 4 |
| 2026-07-19 | Phase 3 Batch 2F 结构化证据窗口 | 冻结并复用 Batch 2E 的 V1 selected source；对比自然段、对话叙事块及150/250/350字边界扩展窗口；短块全文回退、软预算和精确 source/字符区间均保持 shadow-only | 五方案 source/已知相关/后期保留均100%；段落288.3 token（-38.70%）保住4/11，对话块346.7（-26.28%）保住7/11，字符150/250/350分别433.8/462.7/470.1 token并保住9/11；q06-679与q07-679原文自身无法独立支持完整标注，严格11/11存在基线上限；unit=155/0、integration=8/0、quality=35/0 | 无方案同时达到11/11与至少20%压缩；区分结构边界损失、当前chunk冗余不足和两条标注上限；不修改原人工标签、不选择方案、不切生产、不开始Phase 4；下一候选仅为另行授权的父子chunk/事件块实验 |
| 2026-07-19 | Phase 3 Batch 2G-A 父子/事件块离线审计 | 仅用冻结审阅表正文建立23个唯一parent、45个确定性event及稳定ID/hash/字符区间；离线按真实query组装，不调用embedding、Chroma或数据库，不使用gold/must-recall参与切分 | 38个query-source出现与23个parent均可重建，偏移/覆盖=100%，空/孤立/重复/hash错误=0，已知对话/邀请/金额/动作链拆断=0；9/9可验证事实保留，470.3→369.5 token（-21.43%），全文fallback=0；两条基线上限保持单列；unit=159/0、integration=8/0、quality=39/0 | 离线可行性门槛全部通过，仅建议等待授权进入2G-B隔离shadow入库；尚无真实event检索P/R/延迟/隔离结论，不写Chroma、不切生产、不修改Writer、不开始Phase 4 |
| 2026-07-19 | Phase 3 Batch 2G-B 事件块隔离 shadow 入库与真实检索 | 以确定性派生 task ID 和 `index_profile`/`chunk_level` 三重过滤，将冻结的45个event幂等写入共享collection；真实执行10条event向量召回、parent合并和上下文组装；生产默认过滤及Writer均未修改 | 双向串库=0，生产149条记录计数/hash变化=0，稳定ID重复=0，追溯=100%；parent闭集P=53.85%，已知相关保留=60.87%，后期P=27.27%，gold-section代理=70%；8/9事实parent被召回；token 470.3→516.2（增加9.76%），真实event延迟2636.083ms | 隔离机制通过但真实检索、事实和token门槛失败；25个未知候选无法修复固定失败门槛，故不制造额外人工审阅；保留45条外部shadow数据用于复跑，清理仅dry-run；继续shadow，不切生产、不修改Writer、不开始Phase 4 |
| 2026-07-19 | Phase 3 最终收口＋Phase 4 入口上下文 census | 冻结生产 legacy 检索并登记全部未晋级实验资产；按现有10条写作请求真实只读检索、重建当前Writer消息，逐块记录来源、位置、字符/token、必需级别和重复关系；不调用LLM、不修改Writer/ContextManager | 平均总输入12406.4 estimated token；最近3小节5127.1（41.33%）、RAG 3068.0（24.73%）、固定Prompt 1104.2；可证明整块重复=0；非必需数学上界7754.7（62.51%，非删除建议）；11项人工事实来源仅4项已在legacy输入 | Phase 3 以实验未晋级关闭；生产继续shared collection＋原task_id＋legacy top-k；45条隔离event保留且不清理；Phase 4可在另行授权后以shadow Broker启动，优先治理最近原文的注入而非做有损句子压缩 |
| 2026-07-19 | Phase 4 Batch 1 整项选择与预算 shadow | 新增独立 ContextBroker，将完整上下文项分为 P0–P3；对10条冻结场景真实只读执行 legacy top-5，比较 legacy_full、continuity_first 与8500-token软预算；人工证据只在全部选择后验收；Writer/ContextManager/生产消息均不变 | budgeted平均12406.4→8392.4 token（-32.35%）；hard、紧邻上一小节、交接笔记、后期必需项、追溯均100%；legacy已有人工证据4/4保留，另7项继续记为检索上限；2条因P0/P1/P2本身超预算而软溢出；unit=175/0、integration=8/0、quality=51/0、compileall通过 | budgeted通过Batch 1机械门槛，但19个较早recent均未进入预算，尚无正文生成质量证据；继续shadow，不接入Writer、不切生产、不开始Batch 2；下一入口是另行授权的小规模同模型生成质量A/B |
| 2026-07-19 | Phase 4 Batch 2 生成质量 shadow A/B | 固定 Batch 1 keep/drop、模型、Prompt、legacy RAG、规则及风格，对10个冻结场景生成20个匿名候选；先做确定性检查，再做 Codex 辅助盲审；原始生成正文仅存临时目录且不提交 | 真正渲染输入12406.4→8390.4 token（-32.37%）；legacy/Broker胜场6/3、平1，Broker胜+平=40%；目标完成均10/10，hard/关系违规均0，连续性缺陷1→2，因果缺陷1→1，事实错误2→1；后期Broker胜2/3但Q1映射提前暴露且Q4有世界事实错误；生产hash 10/10不变；unit=179/0、integration=8/0、quality=52/0、compileall通过 | 胜平与连续性门槛失败，不建议canary；失败集中在删除较早recent后丢失相对日期、死亡状态和事件顺序；保持shadow，不回调本批参数、不开始Batch 3或Phase 5，等待用户选择整项预算调整、可追溯节级摘要或终止Broker路线 |
| 2026-07-20 | Phase 4 Batch 3 连续性风险保护 shadow | 在冻结B选择上新增确定性 ContinuityRiskGuard；遇到时间锚点、持久状态、未完成链、当前唯一来源、交接引用或无法排除风险时恢复完整旧小节；按冻结source ID只读取回legacy RAG，不重新查询、不调用LLM | A/B/C平均输入12406.4/8390.4/11871.6 token；C仅下降4.31%，10/10软预算溢出；19/19较早recent全部被保护；Q4/Q6/Q7/Q8问题小节均识别；P0/P1/P2、hard/关系、紧邻原文、交接、RAG、人工证据4/4、后期必需项、追溯及生产hash均100%；unit=185/0、integration=8/0、quality=57/0、compileall通过 | token主门槛失败，只证明“任一风险即恢复全文”的保守Guard不可用；19项保护尚非实测退化，不能关闭全部整项选择路线或直接跳到节级摘要；不切生产、不开始Phase 5 |
| 2026-07-20 | Phase 4 Batch 3.5 Guard 实测复核 | 原计划新跑Q4/Q6/Q7/Q8四次B生成；用户明确授权后，租户策略仍禁止向DeepSeek外发私有正文/规则/RAG/Prompt，故未绕过；改为复用Batch 2已完成且与Batch 3 B messages hash 4/4一致的四次真实Writer输出和A对照，只审计缺陷差值，不提交正文 | Q4新增世界事实错误、Q6新增连续状态错误、Q7新增日期顺序错误；Q8因果/越界缺陷A/B各1，不是Broker净退化；4场景中3个实测净退化、1个共同缺陷；目标完成、hard和关系违规均未新增；A/B还差异于部分world facts/软规则/风格项，故19个理论保护项及具体责任来源均无法据此归因；unit=185/0、integration=8/0、quality=60/0、compileall通过 | 撤销“19/19均必要”和“整项路线已失败”的过强结论；保持shadow与legacy生产，不开始Phase5；下一步须在允许真实生成的环境中对全部被删ContextItem做最小恢复验证，失败后才考虑可追溯节级摘要 |
| 2026-07-20 | Phase 8 Batch 1 确定性风格可观测性 | 将 Phase 4 标记为 `paused_by_generation_evaluation_infrastructure`，保留 legacy_full、Broker/Guard及实验记录并暂停Phase5/6；复用固定SHA黄金故事，按18章/52小节离线统计对话、句长、段长、重复、机械起句、感官/心理词及连续结构；不调用LLM、不改Writer/Prompt | 全书58963可见字符、对话12.30%、机械时间/序数/数字起句7.79%、感官词22.54/千字、心理说明词2.48/千字、完全重复句组124、重复段组5；第8章感官词密度偏高，第10章机械起句偏高；`emotion_intensity`和情绪层次不做伪自动评分；unit=189/0、integration=8/0、quality=63/0、compileall通过 | 仅建立基线、报告和回归测试；异常是分布信号而非自动质量判决；旧50维字段保持删除，生产行为不变，不启动Phase5/6或正文重写 |
| 2026-07-20 | Phase 4R Batch R1 Writer 职责边界提取 | 新增 typed artifacts、纯 PromptBuilder、GenerationController、StateCommitter 和 SubsectionPipeline；Writer.run/revise_subsection 保持兼容；Prompt、生成参数、RAG、ContextManager和存储模型不变；不调用 Writer LLM | 10条冻结场景 content/messages/runtime 三重 hash 均10/10不变；模拟小节 facade、生成重试参数、提交顺序、部分失败、幂等、checkpoint和依赖边界均有测试；修改前 unit=192/integration=8/quality=63，修改后 unit=208/integration=8/quality=65，compileall通过 | R1门槛全部通过；生产仍为legacy_full，未接入ContextBroker/SceneSpec；旧生成实现仅作为R1测试oracle暂留，R2前应删除；停止并等待R2明确授权，不开始Phase5/6 |
| 2026-07-20 | Phase 4R Batch R2 StoryStateView/SceneSpec shadow | 增加只读状态投影、四态事实契约、确定性 SceneCompiler 和 source/hash/span provenance；覆盖 Q4/Q6/Q7/Q8；删除 R1 暂留生成 oracle；不调用 LLM、不接入 Writer | 4个 SceneSpec 平均273.0 token（225～311）；source trace=100%，unknown保留=100%；10/10冻结生产hash不变；unit=213、integration=8、quality=68、compileall通过 | R2机械门槛通过，但未验证生成质量；生产继续legacy_full，ContextBroker仍暂停；完成后停止，不自动开始R3、Phase5或Phase6 |
| 2026-07-20 | Phase 4R Batch R3 生成实验准备 | 构建Q4/Q6/Q7/Q8的legacy/Broker/Broker+SceneSpec三臂可移交包；提供prepare/run/import/evaluate命令、匿名顺序、双hash校验和外部Agent说明；私有内容仅进gitignored runtime | 计划12次、实际0次；A/B/C输入合计49715/34365/35522 token，C较A预计下降28.55%；A/B冻结hash逐项不变；公开manifest无Prompt/query/正文；unit=218、integration=8、quality=71、compileall通过 | 只完成准备，尚无生成质量结论；等待获准环境执行run，随后import和匿名评估；不切生产，不开始Phase5/6 |
| 2026-07-20 | Phase 4R Batch R3 外部生成与独立盲审 | 获准外部Agent按冻结包完成Q4/Q6/Q7/Q8三臂共12次生成；新对话独立Agent只读取匿名候选并完成盲审；揭盲前评审文件不含arm映射，私有正文不进Git | A/B/C平均输入12428.75/8591.25/8880.50 token；目标完成2/4、2/4、3/4；盲审首选A/B/C=2/1/1；C虽降28.55%，但出现新增亲属扩写和更多连续性缺陷；Q8三臂均越界 | R3质量门槛失败，不建议canary；SceneSpec对Q7有局部收益，但未稳定约束Q4事实与Q8边界；生产继续legacy_full，不开始Phase5/6 |
| 2026-07-20 | Phase 4R Batch R4 SceneSpec失败归因 | 只读R3三臂messages manifest、12份生成hash、SceneSpec、ContextItem和独立盲审；逐条建立允许枚举内的因果归因、confidence、source/hash及Writer职责所有者；不调用LLM、不重新生成或修改生产 | 22条盲审标签合并为15个概念缺陷：Writer明确指令不服从4、写作边界含糊2、SceneSpec局部事实缺失1、其他生成波动8；已证实的dropped_context_dependency=0；Q4/Q8的SceneSpec明确违规簇=2；unit=218、integration=8、quality=76、compileall通过 | token删减不是Q8失败的必要原因，也不能由当前样本证明直接造成Q4退化；保留约9k budgeted shadow候选，下一优先级仅为另行授权的生成后boundary validator离线检测；Repair和上下文恢复均不自动开始 |
| 2026-07-20 | Phase 4R Batch R5 BoundaryValidator离线基线 | Predictor只读冻结SceneSpec、当前需求、匿名生成清单和12份正文，先冻结私有预测hash；独立evaluator随后读取盲审并按候选概念缺陷去重；不调用LLM、不修改生产、不实现Repair | boundary TP/FP/FN/TN=3/0/0/9；Q7 required-event=1/0/0/2；Q4 unsupported-fact exploratory=2/0/0/1；三项P/R/F1均100%，Q7状态分类、Q8越界检出和证据追溯均100%；原始字节预测hash=`fb6e2158…a42c0`；unit=223、integration=8、quality=81、compileall通过 | 小样本机械门槛通过，只建议等待另行授权Validator shadow接入；不宣称通用Validator成熟，不切生产，不开始Repair、Phase5或Phase6 |
| 2026-07-20 | Phase 4R Batch R6A Validator默认关闭shadow接入 | R5规则迁入app并保持冻结hash；Writer在StateCommitter和record_commit成功后调用失败隔离runner；flag默认false，NoOp sink，不创建数据库，不调用LLM | R5原始预测hash完全不变；disabled调用/记录为0；异常不回滚正文或checkpoint；记录无全文/messages/Prompt且excerpt≤140字；真实样本0；unit=229、integration=9、quality=85、compileall通过 | 结构接入完成但不切生产；仅建议等待另行授权R6B真实shadow采样，不开始Repair、Phase5或Phase6 |
| 2026-07-20 | Phase 4R 最终真实写作试验 | 对真实连续4小节固定A=`legacy_full`、B=`legacy_full+SceneSpec`，共8次主调用；人工匿名审阅原始输出，不测试budgeted Broker，不测人工改稿成本，不修改生产 | B不差于A为3/4；目标完成A/B=2/3；总缺陷A/B=10/4；hard=1/0、关系=1/0、连续性=3/1、事实=3/2、事件顺序=1/1、越界=1/0；3个场景有具体正面作用；七项门槛全部通过；定向测试22 passed、compileall通过 | 最终建议保留SceneSpec实验路线，但4小节不足以证明全面生产质量；生产继续legacy_full，Validator默认关闭，不开始Repair、Phase5/6，不追加Phase4R批次 |
| 2026-07-20 | StateFrame Batch 1 只读契约基线 | 在现有StoryStateSnapshot上新增StateFrame与确定性compiler，只表达时间、地点、在场、持久状态、关系、open loops和unknown/conflicted；planned/hard/arc与未知predicate显式排除；不接Writer、不调用LLM | 4类合成契约场景追溯率100%，unknown/conflicted保留，planned/hard排除；平均33 estimated token（26～40，仅结构指标）；unit含SceneSpec回归12 passed、quality 3 passed、compileall通过 | 只证明契约和责任边界成立，不证明真实状态覆盖或生成质量；下一入口仅为另行授权的真实状态源覆盖审计，不直接注入Writer |
| 2026-07-20 | StateFrame Batch 2 真实状态源覆盖审计 | 只读Phase4R最终试验单一节前checkpoint及4个真实小节outline；审计WorldState、EventGraph、character arcs、handover和冻结上下文，不读取候选/映射/人工结果，不调用LLM | 每帧explicit/generic/unclassified=7/15/2～5，generic占68.18%，平均1768 token；WorldState 14条均为generic，handover 3字段非结构化，relations/foreshadowing/locations无可用状态；追溯、unknown保留、planned/hard排除和hash确定性通过；接管SceneSpec confirmed/open后仍净增约754 token；unit=11、quality=4、compileall通过 | 诊断`upstream_state_contract_required`；不得进入生成A/B或扩关键词。只提出state_id/predicate/subject/value/status/effective range/source/hash最小上游契约，等待用户授权 |
| 2026-07-20 | SceneSpec 最小生产 Canary 接入 | 将最终真实试验 outline-only 语义迁入生产 provider；新增默认off、shadow只编译、task_id白名单canary三态；在PromptBuilder后、GenerationController前可选追加完整SceneSpec；所有失败回退原PromptArtifact | 四个真实golden hash及230/351/277/93 token全部保持；off provider调用0且messages不变，shadow messages不变；生成参数、返回正文、checkpoint顺序/版本和幂等键相关回归通过；定向测试68 passed，compileall通过，Writer/LLM调用0 | 默认生产继续legacy_full；真实canary样本0，只允许用户显式指定单一task_id后有限运行；StateFrame继续`paused_by_upstream_state_contract`，Validator默认关闭，不开始其他优化 |
| 2026-07-21 | 真实 SceneSpec Canary 约束检测归因 | 只读任务 `e7cb9ac2…` 的真实日志、终稿、outline 和15条 EventGraph milestone；严格分离 mandatory 重试与角色弧 post-check；未保存的8份中间候选全部记为 unavailable，不调用LLM、不改生产 | 4个计划正文调用实际变12个，mandatory重试8次、确认流式耗时242.2秒；mandatory Precision因缺中间稿/缺失事件ID/实际关键词不可估；15条弧线告警中完整误报8、部分6、真缺失1，严格P=6.67%；15个milestone仅3个hard，115条边=10条时间相邻+105条同章两两互连，已证实因果边0 | 唯一下一步建议A：先将mandatory自动整节重写降级为告警；角色弧post-check继续只告警，随后另行授权收缩角色弧契约；本轮不执行建议、不开始Phase5/6 |
| 2026-07-21 | Mandatory Event 自动重写降级 | 新增默认`warn`、`off/warn/retry`三态和规范UUID精确白名单；warn只对角色/重复检查后的最终候选记录脱敏hash与计数，检测异常fail-open；检测算法、事件来源、SceneSpec和角色弧均冻结 | 默认无其他重写时Writer正文调用固定1次、mandatory额外调用0；白名单retry保留旧版最多2次；输出/messages/生成参数/checkpoint除移除默认mandatory重写外保持，私有正文不入观测；定向测试与compileall通过，真实warn样本0 | 完成执行权限降级，不宣称检测准确率改善；下一次只需正常Demo观察would-have-retried、实际mandatory retry=0及outline完成度；不修改角色弧预编排，不开始Repair、Phase5/6 |
| 2026-07-21 | Mandatory Event真实warn验证 | 两个完整任务均保持mode=warn；其中最新任务4个小节不发生自动重写；中断任务的Redis重投递单独归因，不混算检测器成本 | 每任务第2/3/4小节would-have-retried=true，actual_retry_count均0；最新任务总Token 44,686、耗时355.7秒；第4小节存在真实目标遗漏，但第2/3小节同时显示literal检测过严 | 工程降级真实生效，检测器继续只告警；不恢复自动整节重写，不再扩大该检测器Demo |
| 2026-07-21 | Character Arc Contract V2影响审计与实现 | 审计CharacterManager→CharacterFormatter/EventGraph→pre/post-check→handover/commit链；新增默认v1、V2分类/来源/降级和显式边契约；不调用Writer/LLM | 固定任务`e7cb9ac2…`/`b5ddb41c…`共27 milestones、188建边操作，其中同节两两连接171，已证实因果边0，弧线事件约1,795 estimated tokens；legacy结构可证明hard=0、unresolved=27；V1默认且旧存储不回写 | 角色弧确实增加Writer上下文和handover输入，满足V2实施门槛；下一步最多授权1个真实V2 Demo，不自动开始Phase5/6 |
| 2026-07-21 | 真实写作运行收尾：伏笔类型、调用成本、Character Arc V2 | 对`3454d80e…`固定日志建立25次HTTP调用账本；为resolve_chapter增加统一读写归一化但不迁移数据库；关闭本轮V2续写实验，不调用LLM、不重新生成 | 39,010 token精确对上Writer32,442+Continuity263+Reviewer6,305，但另有后台线程11,012 known token未入总数、4次流式正文token unavailable；最大可恢复成本为review13,800、handover extraction9,608、长度精简7,732；V2续写12条legacy milestone均soft、hard=0、边=0 | 伏笔健康失败来自字符串章节号且不影响任务completed/正文保存；V2=`experimental_not_promoted`，默认继续v1，不追加Demo；下一成本候选仅为审阅去重、handover brief按输入hash缓存、共享typed extraction，均未执行 |
| 2026-07-21 | Writer后台Section Review去重 | 审计BlackBoard/API/UI与最终Review消费者；新增默认false的`WRITER_INCREMENTAL_SECTION_REVIEW`，只关闭Writer循环内后台Reviewer线程并记录脱敏观测；true保留旧行为，不调用LLM | 增量结果仅供写作中进度展示，不参与生成、重试、提交、checkpoint、最终评分或任务成败；基线2次重复review合计7,495 known token/15.1秒；真实任务`cd340fcc…`有4次正文、0次Mandatory重试、0次增量review，experience/final section/global review、正文和checkpoint均保留 | 真实任务共22次HTTP调用；因其为新首章，相对25次续写基线同时少4次handover brief、多3次上游调用，故只能将其中2次减少归因于本优化；调用消除和成本方向已验证，精确同输入token节省未测量；优化验收完成，不自动开始handover缓存或共享extraction |
| 2026-07-21 | Writer Condense可控真实验证与晋级 | 固定`cd340fcc…`审计3次二次压缩并实现legacy/warn两态；随后用真实任务`4ce7e82f…`运行warn，warn只记录超长并保留完整初稿，不构造condense请求 | 基线3次condense耗费8,998 known token/46.1秒，仅删除859字；真实任务4/4小节均would-have-condensed但实际condense=0，HTTP=19，Mandatory重试=0，最终Review、正文和checkpoint正常，用户确认正文“可用” | 实测门槛通过，warn晋级默认，legacy保留为CMD显式回退；跨任务token/延迟不作精确因果比较；handover在长度调整前提取的顺序风险仍仅登记，不追加阈值网格、handover缓存或shared extraction实验 |
| 2026-07-21 | 下一优先级决策：Shared Typed Post-Write Extraction | 基于真实调用成本、Writer职责拆分和StateFrame上游契约缺口，确定下一方向为“1次正文主调用 + 1次共享结构化状态提取”；统一handover、人物/关系、时间地点、事件、经历与伏笔变化 | 当前只写入目标架构、typed契约、实施顺序、验收和两批停止上限；未修改代码、未调用LLM、未替换任何生产提取链；Writer单次输入仍约12,406.4 estimated token | 先补可靠结构化上游，再决定是否重启StateFrame或删除legacy上下文；不得同时改Prompt/RAG/SceneSpec/Validator/Review，无法证明真实调用与延迟收益即停止 |
| 2026-07-21 | Shared Typed Post-Write Extraction工程shadow接入 | 审计handover、人物状态、关系、经历和伏笔消费者；新增`PostWriteStateBundle`、逐字证据校验、默认off的post-commit shadow runner、独立cost label及任务Blackboard私有sink；旧链继续生产生效 | off模式零构建/零调用/零记录；shadow每个已提交小节额外1次提取，只记录Bundle，不写WorldState/EventGraph/character arcs/relations/events/foreshadowings/checkpoint；错误fail-open；定向测试43 passed、compileall通过、实现期LLM调用0 | 状态为`engineering_shadow_ready`，尚不宣称节省token或延迟；下一步且只允许1个真实shadow任务验证旧字段覆盖、unknown保留、100%追溯和潜在调用减少，失败即停止，不扩Prompt/关键词/测试矩阵 |
| 2026-07-21 | Shared Typed Post-Write Extraction真实shadow收口 | 对唯一授权真实任务只读核对4个私有Bundle、legacy handover、角色状态、关系和经历存储；不重新生成、不修改生产、不提交正文或私有状态值 | 4次共享提取=15,141 tokens/56.629秒，接受55条、拒绝6条、追溯率100%；handover=0；潜在legacy链7次/21,703 tokens，理论差额6,562 tokens但未实现；角色状态旧调用可观察变化0但原响应缺失、不能认定no-op，关系写入0、经历写入9，仅作成本信号 | 状态改为`real_shadow_not_promoted`；失败项为完整语义覆盖和端到端延迟，默认off、legacy不变；不跑第二个同形Demo。未来若重启，只先做离线稳定ID、durable/transient、open-thread及consumer adapter契约，不直接调用模型 |
| 2026-07-21 | Character State Update有效性与停用门槛审计 | 只读4个真实任务的日志、Blackboard、checkpoint和必要正文短证据；审计调用、解析、消费者和状态传播，不调用LLM、不修改生产 | 4次调用共26,044 known tokens/25.8秒；2次确认产生10个实质人物状态变化，2次因缺原始响应或可靠前态记为unavailable；全样本实质调用率50%、可判定样本100%，停用门槛失败；另发现Writer更新只进入局部变量/Blackboard，未返回Coordinator，3个差异任务的checkpoint仍全部保持starting_state | 不新增`WRITER_CHARACTER_STATE_UPDATE_MODE`，生产保持legacy，不跑新Demo；状态传播断链作为独立缺陷登记，若修复须另行授权且不得与停用实验混做；Shared Extractor继续off，不开始StateFrame/Repair/Phase5/6 |
| 2026-07-21 | Character State Update状态传播断链修复 | 沿现有节级提交边界补齐原子解析、Writer返回、自动/交互checkpoint、Coordinator采用和Reviewer读取；不修改Prompt或增加LLM调用 | 新checkpoint将已提交整节正文与对应`character_arcs`放入同一payload；旧Writer结果和旧checkpoint兼容回退；规范JSON SHA-256覆盖Writer/Coordinator/checkpoint/Reviewer；定向测试52 passed、compileall通过 | 状态为`engineering_fix_complete_real_demo_pending`；Blackboard只作运行时共享，不是唯一持久化源；不宣称跨存储事务回滚；下一步最多1个正常真实任务验证hash、恢复和Reviewer输入，不自动启动其他优化 |
| 2026-07-21 | Character State传播首次真实运行回归修复 | Worker实际执行2个任务，Writer均完成状态更新，但Coordinator传播日志在后部局部`_json`导入执行前引用该局部变量，触发`UnboundLocalError` | 两次Demo均在Coordinator/Reviewer贯通前失败，不能计为成功；删除`_phase_writing`内重复导入，统一模块级`_json`，增加局部遮蔽回归测试 | 状态为`logging_scope_regression_fixed_real_demo_rerun_required`；重启Worker后最多重跑1个任务，不追加其他变量或实验 |
| 2026-07-21 | Character State传播最终真实验收 | 重启后以单个续写任务验证Writer更新、节级checkpoint、Coordinator、Reviewer与恢复链；Shared Extraction保持off | 任务completed；状态`e5a408…→4a3b725…`，四个目标hash完全一致；Mandatory Event实际重试0；恢复状态未倒退 | 状态改为`real_demo_passed_closed`，不再追加同类Demo；伏笔健康度的`resolve_chapter`混合类型异常作为独立缺陷，不计入本项失败 |
| 2026-07-21 | Foreshadowing章节类型归一化收口 | 纠正早期根因：最新异常为已归一化`resolve_chapter`整数与字符串`current_chapter`比较；统一规范比较基准，并移除active/unresolved查询中的原始SQLite混合类型比较 | 写入仅正整数/null；所有读取比较路径均先归一化；非法历史值不改库且不参与逾期判断；定向unit 20项及quality契约通过，Writer/LLM调用0 | 状态=`production_normalization_complete`；任务`3530d835…`本身completed，只有Review伏笔健康摘要缺失；无需新增写作Demo，后续任务前重启Worker加载代码 |
| 2026-07-21 | Writer首稿执行契约工程接入 | 复用现有outline、SceneSpec和target_words，以默认off的确定性短契约表达事件顺序、unknown、停止边界及85%～130%篇幅范围；canary不重复注入完整SceneSpec | 最近真实基线为1124/2399/1720/4273字符；off零构建，shadow不改messages，canary只追加最终user区块；超5事件保留并标记overplanned；定向测试74项及compileall通过，实现期Writer/LLM调用0 | 状态=`engineering_canary_ready_default_off`；最多只允许1个正常四小节canary和1次go/no-go收口，不扩阈值网格、不启动Validator/Repair/Phase5/6 |
