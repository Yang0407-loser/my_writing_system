# OutlineEventContract V1

## 结论

已建立统一、可追溯、可由作者确认的 typed 大纲事件事实层，并让 Outline Budget Advisor 只消费该契约。它不是角色弧：契约描述“大纲明确写了什么”，不推断角色成长意义，也不接入 Writer、SceneSpec、Validator、StateFrame 或 Mandatory Event。

旧 outline 仍可直接使用。Legacy Adapter 只产生 `proposed` 事件，最高 `medium` 置信度，默认 `requiredness=unspecified`，不会自动升级为 hard。只有作者在现有篇幅建议弹窗中确认后，事件才成为 `confirmed`；确认事件结构与应用推荐字数是两个独立动作。

## 权威存储与调用链

- 当前权威大纲是 Redis 中的 `outline_tree` 与其 `outline`/outline_v2 表示；活动 checkpoint 同步保存两者。
- `tasks.db` 只作为历史恢复 fallback，不是活动编辑权威。
- 小节原有字段为 title、description、key_points、target_words；本轮只增加可选 `event_contract`，旧任务和旧 checkpoint 无需迁移。
- 前端编辑 → 只读 Budget API → 编译或复用 typed contract → Advisor 计算 → 弹窗展示。未点击确认不会保存；点击确认后复用现有 outline 保存、版本和 checkpoint 流程。
- Writer 继续读取原有 outline 字段，不读取 event_contract，生产 messages、参数、retry 与写作流程未改变。

## 契约结构

`OutlineEventUnit` 记录稳定 event ID、来源槽位及 hash、事件类型、人物、动作/对象/结果、时间地点、时态、强度、状态、置信度和失效原因。`SubsectionEventContract` 区分当前、可选、必需、下一小节 deferred 事件和唯一停止点；`ChapterEventContract` 汇总章节预算、来源与 stale 小节。

ID 与内容 hash 分离。相同输入产生相同 ID/hash；文本修改时保留已确认 ID但将受影响事件置为 stale；删除事件保留历史 ID并置为 superseded；插入事件不会重编号仍存在的已确认事件。

## Legacy 适配与边界

- key point 先作为候选动作链；description 只补充可独立追溯的单动作段，宽泛多动作摘要仍保留为 objective，但不重复计数。
- 只做标准化完全重复、明确包含和确定性结构判断，不做语义相似度。
- 无 key points 时允许从 description 降级拆分，但整份 contract 为 low confidence。
- 只精确匹配已有角色名，不解析代词，不生成角色弧意义。
- 当前小节 events 与下一小节 proposed events 分开；后者只进入 `deferred_event_ids`。Legacy 模式不自动设置 stop boundary。

## 固定真实案例

任务 `019fc0c8-1a20-4be3-8252-54aaf1c5aa27` 的四个小节均成功生成 contract，16/16 事件具有 source ID/source hash/text hash，event ID 无重复。

| 小节 | 事件 | 时间跳转 | preferred | 章节分配 | 置信度 | 建议 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| S1.1 | 5 | 1 | 1350 | 1367 | low | review_structure |
| S1.2 | 3 | 2 | 850 | 861 | medium | keep |
| S1.3 | 3 | 0 | 800 | 810 | medium | review_structure |
| S1.4 | 5 | 0 | 950 | 962 | low | review_structure |

- S1.1 未被压成单一事件。
- S1.2 的三个周六形成两次时间推进；拍摄事件为 `current`，没有被标为 future。
- S1.3 三个事件作为 S1.2 的 deferred 候选表达，其复杂度没有高于 S1.2。
- S1.4 的宽泛要点和低置信补充触发结构复核。
- preferred 合计 3950，但最大余数分配严格守恒为章节预算 4000。

报告不保存完整 outline 或正文，也没有将历史模型输出长度作为理想篇幅标签。

## UI 与失效

现有篇幅建议弹窗增加可折叠“事件结构”。作者可以校正 temporal scope、hard/soft/unspecified 和唯一停止事件，再点击“确认事件结构并保存大纲”。若之后修改 title、description 或 key points，普通保存会把相关 confirmed contract 置为 stale，不会把旧确认静默套到新文本。未受影响小节的 event ID/text hash 保持。

新增小节只产生 proposed contract；本轮不传播角色弧或状态依赖。跨小节的未来依赖图传播被明确登记为后续工作，不在本轮实现。

## 边界与下一步

篇幅公式仍是 provisional advisory，权重、四个风格旋钮和章节预算守恒规则没有改变。作者拥有事件结构与篇幅的最终决定权。本轮没有生成质量结论，也没有开始 Character Arc 或 StateFrame。

停止门槛已满足：来源追溯 100%、ID 无重复、hash 确定性 100%、当前/下一小节可表达、Legacy 不自动 hard、来源变化可失效、Advisor 无第二套解释、Writer 行为不变。因此可以在用户另行明确授权后进入 `CharacterArcProjection`；本轮不自动开始。
