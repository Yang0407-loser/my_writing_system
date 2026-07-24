# StateFrame V1：小节状态快照与生产质量基线

## 结论

StateFrame V1 的只读数据契约、确定性构建器、状态差异、三维质量观察和只读 API 已完成。现有 `StateFrameCompiler` 继续服务 SceneSpec，未修改其语义；新实现没有进入 Writer messages，也没有替换交接、人物状态、关系或伏笔存储。

最新已完成真实任务包含 4 个小节。所有实际进入 Frame 的事实均具备 `source_id/source_hash`，追溯率为 100%；但必要状态源只覆盖 3/7，8 个 before/after Frame 全部只能标记为 `partial`。因此当前不能建议把 StateFrame 注入 Writer。

最主要的缺口不是 StateFrame 分类规则，而是历史持久化粒度：`tasks.db` 只保存任务结束后的当前状态和节级交接，不保存每小节生成前/提交后的 checkpoint，也不保存 post-write typed bundle。使用最终状态倒推每小节历史会造成时间泄漏，本实现明确禁止这样做。

## 现有状态源与调用顺序

实际写作链路为：

1. Writer 组装现有 legacy 上下文并生成小节正文。
2. Writer 从初稿提取 handover，并通过 `StateCommitter.commit_handover_effects` 更新 EventGraph/WorldState 的现有副作用。
3. 长度处理、确定性检查和其他现有流程保持原样。
4. `StateCommitter.commit_subsection` 提交正文、ContextManager、向量 chunk 和运行数据。
5. BoundaryValidator（默认关闭）及 Shared Post-Write Extraction（默认关闭）在 commit 后以 shadow 方式观察。
6. 节尾才合并 section handover、更新 Character State，并保存节级 checkpoint。
7. Coordinator 接收 Writer 返回的 Character State，继续交给 Reviewer 和最终 checkpoint。

由此可知：handover 是下一节承接报告，不是完整事实库；Character State 是节尾当前态，旧任务没有小节级历史态；关系和伏笔存储是当前快照，没有小节级生命周期快照；post-write typed bundle 若未显式开启则不存在。

## 数据契约

新增契约包括：

- `StateFact`：事实类型、主体、谓词、值、认识状态、有效期、持久性、来源和最长 140 字证据。
- `StateExpectation`：计划事件和人物转变；与已确认事实严格分离。
- `StateFrameSnapshot`：某小节生成前或提交后的只读快照。
- `StateDelta`：added、changed、resolved、unchanged 和 expectation 状态。
- `StateFrameQualityObservation`：交接连续性、人物状态转变、伏笔健康度三个独立维度。

`planned` 不是 `StateFact.status` 的合法值。大纲事件和角色弧只能进入 `StateExpectation`，不得因计划存在而升级为已发生事实。

## 现有模块如何进入 StateFrame

### 交接笔记

- 人物连续状态进入 `continuity_state`，未取得独立证据时保持 `unknown`。
- 待承接链进入 `open_event_chain/pending`。
- 交接中的伏笔提醒进入 `foreshadow_state/pending`。
- 整段交接文字不会升级为 confirmed，也不会写入公开报告。

### 人物与关系状态

- 已有 typed 人物字段映射为 `character_state`。
- 当前关系阶段映射为 `relationship_state`。
- 当前存储没有历史 subsection，因此不能单独证明某次小节变化。
- Character Arc 和 OutlineEventContract 继续只产生 expectation。

### 伏笔

- 使用现有 Foreshadowing Store 及 `resolve_chapter` 正整数/null 归一化。
- 当前 planted/hinted/resolved 状态进入 `foreshadow_state`。
- planned resolution 不会记作 resolved。
- 当前存储没有逐小节生命周期历史，不能从最终表推断某小节的新增、推进或回收。

## 真实任务覆盖

真实任务按“最近完成”规则自动选择，报告只保留 task ID 的 SHA-256。

| 项目 | 结果 |
|---|---:|
| 小节 | 4 |
| before/after Frame | 8 |
| complete | 0 |
| partial | 8 |
| 可用必要来源 | 3/7 |
| 来源覆盖率 | 42.86% |
| 已进入 Frame 的事实追溯率 | 100% |
| Writer/LLM 调用 | 0 |
| 数据库写入 | 0 |

可用来源是 outline、节级 handover 和当前 foreshadow store。缺少可用于小节历史重建的 Character State、当前任务 Relationship State、post-write typed bundle 和每小节 before/after checkpoint history。

当前 9 条伏笔状态在 4 个 Frame 中形成 36 个“快照出现次数”，不是 36 条不同伏笔，不能用于推断逐小节生命周期变化。

## 三项生产质量基线

### 交接连续性

当前章节只有指向下一章节的节级 handover，没有每小节入场前的 typed handover 项。本次 4 个小节的可评估分母为 0，不能计算覆盖率，也不能据此判断 Writer 连续性好坏。

### 人物状态转变

大纲适配得到 2 个 state-transition/decision 预期，但缺少可确认人物归属和小节级状态增量，均为 `unassessable`，不得记为 Writer 遗漏。

### 伏笔健康度

当前 store 中可读取 9 条伏笔当前态，但缺少小节级 lifecycle history，所以本节 planted/advanced/resolved 变化不可评估。`resolve_chapter` 可安全读取，但没有依据计算某个小节的回收完成率。

三项结果都是生产可观测性基线，不是质量真值或人工金标准。

## 发现的断链

发现的是“历史状态可观测性断链”，不是本次正文生成失败：

- 写作时存在人物状态、交接和伏笔处理；
- 最终任务记录没有保存每小节的 before/after 状态工件；
- post-write typed extraction 默认关闭，其 bundle 不会进入任务历史；
- 因而旧任务无法可靠恢复小节级 StateDelta。

下一步如获授权，应只修复这一条：在现有 checkpoint/Blackboard 工件中保存脱敏、可追溯的小节状态快照引用，不能同时重构三个提取器，也不能用最终状态倒推历史。

## API 与生产影响

新增只读接口：

- `GET /tasks/{task_id}/state-frame/{section}/{subsection}`
- `GET /tasks/{task_id}/state-frame/{section}/{subsection}/before`
- `GET /tasks/{task_id}/state-frame/{section}/{subsection}/after`
- `GET /tasks/{task_id}/state-frame/{section}/{subsection}/delta`
- `GET /tasks/{task_id}/state-frame/{section}/{subsection}/quality`

接口不写入状态、不触发 Writer、不调用提取器或 LLM，不返回完整正文、Prompt 或 messages。

## 晋级判断

结论：`do_not_inject_fix_single_subsection_snapshot_gap_first`。

StateFrame V1 暂不具备 Writer shadow 注入条件。原因是来源覆盖率只有 42.86%，而不是契约或追溯失败。现有交接、Character State、Relation State 和 Foreshadowing Store 均继续保留为权威来源。

## 验证

- 新旧 StateFrame、只读 API、数据契约、公开报告与隐私边界定向测试：34 passed。
- 受影响模块 `compileall`：通过。
- Writer/LLM 调用：0。
- 生产 Writer messages、checkpoint 和 retry：未修改。
