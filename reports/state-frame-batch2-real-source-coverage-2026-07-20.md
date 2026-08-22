# StateFrame Batch 2：真实状态源覆盖审计

> 日期：2026-07-20  
> 状态：完成并停止  
> 诊断：`upstream_state_contract_required`  
> 生产行为：未改变，继续 `legacy_full`

## 结论

StateFrame compiler 的机械边界成立，但当前真实状态源不足以直接组成精炼、可靠的 Writer 状态输入。问题不在分类器缺少关键词，而在上游数据主要由通用 `world_fact`、非结构化 handover 和缺少有效期/认识状态的字符串组成。

本批不进入生成 A/B，不把 StateFrame 注入 Writer，也不通过扩关键词表追逐覆盖率。

## 固定输入与隔离

审计只读取 Phase 4R 最终试验冻结的一个章节生成前 checkpoint 及其 4 个真实连续小节 outline。没有读取候选正文、A/B 映射、人工审阅、缺陷标签、gold 或 must-recall 字段。

冻结物只有一个章节生成前状态，不包含每个小节完成后的独立状态。因此4个小节可以重复审计同一状态源，但不能被描述成4个独立时点的连续状态快照。

## 真实来源

| 来源 | 真实形态 | 数量 | 主要问题 |
|---|---|---:|---|
| WorldState | facts + `verified` 标志 | 14 | 14/14 只能映射为通用 `world_fact`；13条未验证，1条已验证 |
| EventGraph | typed `arc_milestone` | 15 | 全部 pending；只表达规划/弧线，不是当前事实 |
| Character arcs | `current_state` 字符串 | 5 | 可映射为人物状态，但缺少认识状态及有效 section/subsection |
| Handover | 结构化外壳中的长字符串 | 1组3字段 | `character_state/open_threads/foreshadowing` 不是逐条状态；4帧累计12条非结构化输入 |
| Rules | 已渲染字符串 | 1 | 属于 hard 规则，应排除出 StateFrame |
| Relations | 空字符串 | 0 | 无当前关系阶段来源 |
| Foreshadowing | 空字符串 | 0 | 无结构化 open-loop 来源 |
| Locations | 空字符串 | 0 | 无当前地点状态来源 |
| Characters | 静态人物档案 | 5 | 有人物资料，但没有当前在场/有效状态契约 |

## 覆盖与结构

4个小节得到相同的节前 StateFrame 基础状态：

| 指标 | 结果 |
|---|---:|
| 每帧 explicit structured state | 7 |
| 每帧 generic state | 15 |
| 每帧 unclassified state | 2～5 |
| generic state 占 included state | 68.18% |
| 平均/最小/最大 estimated tokens | 1768 / 1768 / 1768 |
| 空 Frame | 0 |
| 只有 generic 的 Frame | 0 |
| 来源追溯率 | 100% |
| unknown/conflicted 保留率 | 100% |
| planned/hard/arc 误入 | 0 |
| 重复分类 | 0 |
| frame hash 重跑一致 | 100% |

显式状态主要来自5条 character arc current state 和2条 handover open-loop字段，并不意味着时间、地点和人物在场已被覆盖。实际审计中 temporal、location、presence 和 relationship 等关键当前状态仍为空或缺少权威来源。

## 重复账本

每个小节：

- StateFrame 与同快照 SceneSpec confirmed/open 重复9条 assertion；
- StateFrame 与 legacy WorldState 重复14个 source ID；
- StateFrame 与 handover 重复3个 source ID；
- 直接叠加 StateFrame 理论增加1,768 tokens；
- 即使让 StateFrame 接管 SceneSpec confirmed/open，理论上仍净增加约754 tokens。

原因是 StateFrame 仍携带14条通用 world facts、unknown 状态和3条非结构化 handover；重新包装没有减少事实范围，也没有解决职责重复。

## 机械门槛

通过：

- source/evidence追溯率100%；
- unknown/conflicted保留率100%；
- planned/hard误入为0；
- 重复分类为0；
- frame hash确定性；
- 4/4 Frame包含至少一个显式状态；
- 未使用关键词推断。

未通过：

- generic state 不是多数：实际为68.18%。

因此不能给出 `ready_for_composition_contract`，也不是没有任何真实数据的 `insufficient_real_source_data`；正确诊断是 `upstream_state_contract_required`。

## 最小上游契约

若后续另行授权，上游每条可持续状态至少需要：

- `state_id`；
- `predicate`；
- `subject`；
- `value`；
- `epistemic_status`；
- `effective_from`；
- `effective_until`；
- `section/subsection`；
- `source_id`；
- `text_hash`。

本批不实现写入迁移、不建立第二套数据库、不解析历史正文、不调用LLM补结构，也不修改 WorldState 写入行为。

## 验证与停止

- 定向 unit：11 passed；
- 定向 quality：4 passed；
- `compileall`：通过；
- Writer/LLM调用：0；
- Redis/SQLite/Chroma写入：0；
- 生产 messages 变化：0；
- 私有正文进入公开报告：0。

Batch 2 到此停止。不得开始 StateFrame 生成 A/B；下一步只能由用户另行决定是否设计最小上游状态契约，或关闭 StateFrame 生产路线。
