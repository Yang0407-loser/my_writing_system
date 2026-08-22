# CharacterArcProjection V1

## 结论

已完成从 `OutlineEventContract` 到角色弧候选的确定性投影层。它解决的是“同一个大纲事件应如何成为角色弧规划证据”，不是直接生成生产角色弧。

当前状态为 `offline_projection_ready_not_promoted`。生产仍使用 `CHARACTER_ARC_CONTRACT_VERSION=v1`；CharacterManager、Writer、Coordinator 和 EventGraph 均未读取该投影。本轮没有调用 Writer/LLM，没有写 Redis、数据库或 EventGraph。

## 现有生产链审计

- 当前角色弧由 `CharacterManager.plan_arcs` 调用 LLM生成，Redis 和 checkpoint 保存 `character_arcs`。
- Writer 通过 CharacterFormatter 读取 milestone；EventGraph 会把 milestone 变成规划事件。V1 还会建立同角色相邻边和同章两两边。
- V2 已能区分 hard/soft/observational/ordinary/unsupported，并只建立显式依赖或状态连续边，但默认仍为 v1，且此前没有在全新任务上证明 Planner 分类质量。
- 新 OutlineEventContract 是大纲事实层，不能自行回答角色变化的 before/after state。直接把 event hard 等同于 arc hard 会重新制造过密里程碑。

## 投影契约

每个候选包含稳定 projection ID、角色 ID/姓名及角色来源 hash、event ID/type/text hash、分类建议、requiredness、before/trigger/after/evidence/rationale、outline contract hash、来源、状态、置信度与失效原因。

权威链被分成三层：

1. Legacy 大纲事件只是 proposed 事实；
2. typed 事件经作者确认后，投影仍只给出 proposed arc 分类；
3. 只有再次明确角色弧分类并补齐状态转变字段，才成为 confirmed arc candidate。

普通动作、对话和场景切换默认归入 `ordinary_plot_event`；观察归入 `observational_texture`；明确决定或状态变化最多建议为 `soft_arc_progress`。任何项目都不会自动成为 hard。hard 必须同时具有 before state、trigger、after state、observable evidence 和 rationale。

## 固定真实案例

对任务 `019fc0c8-1a20-4be3-8252-54aaf1c5aa27` 的五个角色和四小节 typed contract 进行离线投影：

- 候选 8 条，来源追溯率 100%，重复 projection ID 为 0；
- 林晚 4、周野 3、顾衍 1；季晴和吴阿姨在该章没有精确人物事件；
- ordinary plot 6、observational texture 1、unsupported 1；
- authoritative=0，hard=0；
- 9 个事件因没有精确角色 actor 或 actor 不在角色表而排除。

这里的 0 条 authoritative 不是算法故障：真实任务仍是 Legacy proposed contract，而且关键决定/状态句大量使用代词。按照“不猜代词、不把普通动作提升为角色弧”的约束，当前证据不足以宣称角色发生了可靠状态转变。

## 增量更新

- 新增小节只增加新候选，旧候选 ID/text hash 保持；
- 事件文本、来源或角色卡变化时，已确认投影变为 stale 并取消可注入性；
- 来源事件删除时保留历史候选并标记 superseded；
- 不重建旧 EventGraph，也不创建隐式因果边。

已确认的 soft/hard 候选可以导出为 V2 兼容数据，但本轮没有任何生产调用方。导出不添加 depends_on/causes，因此不会恢复旧的同章两两建边。

## 决策

投影层自身通过确定性、追溯和增量失效门槛，但不具备替换生产角色弧的真实数据条件：尚无作者确认的真实事件契约，也没有完整 before/trigger/after 状态记录。

下一步只能二选一并需另行授权：

1. 在现有大纲弹窗上增加小规模角色弧确认，作者只审阅 decision/state-transition 候选；或
2. 建立 contract-grounded Character Arc Planner shadow，让模型只消费 typed contract，再对少量候选做确认。

在此之前不得把投影接入 Writer/EventGraph，不得开启 V2 生产默认，也不得自动开始 StateFrame。
