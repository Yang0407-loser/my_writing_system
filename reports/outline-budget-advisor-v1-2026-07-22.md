# Outline Budget Advisor V1

## 结论

Outline Budget Advisor V1 已完成只读生产接入，并在 2026-07-24 改为只消费 `SubsectionEventContract`。description/key_points 的兼容解释集中到 `LegacyOutlineEventAdapter`，Advisor 不再维护第二套事件拆分逻辑。默认只展示建议；“应用推荐字数”和“确认事件结构”是两个独立操作，均由作者决定。

公式是 **provisional advisory**，不是质量真值。本轮没有调用 Writer/LLM，没有生成正文，也没有把固定任务最终约 8250 字反向用作理想篇幅标签。WriterExecutionContract 继续默认 `off`。

## 调用链审计

- Planner 当前以章节目标字数和小节数做平均分配，标准化阶段会再次写入统一的小节 `target_words`。
- 前端树状编辑器直接编辑叶节点 `target_words`，保存时经现有 outline API 持久化。
- Writer 直接读取小节 `target_words` 决定生成预算；Advisor 未进入 Writer messages、Prompt、retry 或 checkpoint 链。
- 新 API 仅分析请求中的未保存 outline 草稿，不读取或写入 Redis、数据库或 Chroma。未确认事件只以 `proposed` 返回；作者确认后才经现有 outline 保存流程持久化。
- 风格长度系数只读取 `sentence_preference`、`sensory_density`、`dialogue_ratio`；`emotion_intensity` 只记录。旧兼容风格字段不参与计算。
- style brief 中的段落字数指令只登记冲突，不参与推荐值计算。

调用顺序为：作者编辑 outline → 点击“篇幅” → Legacy Adapter/已确认 contract 形成统一 typed 输入 → Advisor 返回建议 → 前端弹窗展示 → 作者可分别确认事件结构或应用推荐字数 → 经现有保存流程持久化 → 后续才可开始写作。

## 固定任务结果

固定任务 `019fc0c8-1a20-4be3-8252-54aaf1c5aa27` 重新通过统一事件契约离线计算。章节预算为 4000 字，四节 preferred 合计为 3950 字；章节最大余数分配仍严格守恒为 4000 字，不会静默扩大章节目标。

| 小节 | 当前 | 建议 min/preferred/max | 事件 | 时间跳转 | 场景变化 | 人物 | 密度 | 动作 | 置信度 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| S1.1 | 1000 | 1150 / 1350 / 1550 | 5 | 1 | 0 | 1 | dense | review_structure | low |
| S1.2 | 1000 | 700 / 850 / 1000 | 3 | 2 | 0 | 3 | balanced | keep | medium |
| S1.3 | 1000 | 700 / 800 / 900 | 3 | 0 | 1 | 2 | light | review_structure | medium |
| S1.4 | 1000 | 800 / 950 / 1100 | 5 | 0 | 0 | 0 | balanced | review_structure | low |

- S1.1 从无 key points 的长 description 中降级拆出 5 个动作链，没有误判成单一简单事件；因结构来源不可靠，建议先复核结构，而非盲目增加字数。
- S1.2 从三个编号周六识别出两次明确时间推进，并记录三人物协调压力。其复杂度高于 S1.3，但当前 1000 字仍位于 provisional 范围上沿，因此不强制拆分。
- S1.3 为单场景记录与反思，复杂度没有高于 S1.2。
- S1.4 四条 key points 较宽泛，且 description 仅能形成低置信补充候选，因此建议结构复核。
- 四节均检出 style brief 的“段落长度”与“小节全文目标”可能竞争；该文本没有参与公式。
- 16/16 事件单元都有稳定 event ID、source ID、source hash 和 text hash。公开报告不保存原 outline 文本副本。

## 公式与限制

V1 使用：每个事件 180 字；额外时间跳转、场景变化各 100 字；第二人之后每人 80 字；完整互动链 120 字；持久状态变化或关键决定 150 字。四个风格旋钮合计系数被限制在 0.90～1.15，结果取最近 50 字，范围为 preferred 的 85%～115%。

该公式尚未经过统计校准，也不判断文学质量。确定性规则可能低估隐含人物、地点或省略主语的事件，因此低结构置信度会返回 `review_structure`，而不是伪造精确推荐。作者始终拥有最终篇幅决定权。

## 下一步

下一步只允许作者在一个真实 outline 上查看建议，选择性应用并正常保存；不得由 Advisor 自动修改 outline 或自动启动 Writer，也不据此恢复 WriterExecutionContract canary。
