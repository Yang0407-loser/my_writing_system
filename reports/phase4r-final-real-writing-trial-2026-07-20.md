# Phase 4R 最终真实写作试验

> 日期：2026-07-20  
> 状态：最终 go/no-go 验证完成  
> 决策：保留 SceneSpec 实验路线，不切换生产

## 实验设计

本轮使用接下来真实创作的 4 个连续小节，不复用 Q4/Q6/Q7/Q8 或旧人工标签。唯一变量为：

- A：`legacy_full`
- B：`legacy_full + SceneSpec`

两臂的 Writer Prompt、完整 legacy 上下文、RAG 顺序、ContextManager、角色与关系规则、风格、模型和生成参数保持一致，共完成 8 次 Writer 主调用。候选正文、messages 和 A/B 映射仅保存在 gitignored runtime，公开报告不包含正文。

## 人工盲审结果

人工审阅 provenance 为 `user_real_writing_acceptance`，没有测量人工改稿字符数或时间。

| 指标 | legacy_full | legacy_full + SceneSpec |
|---|---:|---:|
| 目标完成 | 2/4 | 3/4 |
| hard 违规 | 1 | 0 |
| 关系违规 | 1 | 0 |
| 连续性错误 | 3 | 1 |
| 事实错误 | 3 | 2 |
| 事件顺序错误 | 1 | 1 |
| 越过停止边界 | 1 | 0 |
| 总缺陷 | 10 | 4 |

B 在 4 个场景中有 3 个不差于 A，场景首选顺序为 B/A/B/B；3 个场景记录了 SceneSpec 版相对另一候选的具体正面作用。

## 门槛

以下门槛全部通过：

- B 至少 3/4 不差于 A；
- B 没有新增 hard、关系或事实错误；
- B 没有增加连续性、事件顺序或停止边界问题；
- B 的目标完成率不低于 A；
- 至少 2 个场景存在人工确认的具体正面作用。

`edit_characters` 和 `edit_minutes` 均为 `null`，明确表示 `not_measured`，没有转换成 0，也没有参与最终决策。

## 决策

Phase 4R 的最终建议是 `retain_scene_spec_experimental_route`。结果表明，在完整 legacy 上下文不变时，将场景规划职责外置为短 SceneSpec 值得保留。

这不是生产切换授权。4 个连续真实小节只能支持有限路线决策，不能证明全面生产质量。当前生产继续 `legacy_full`，SceneSpec 不进入生产 Writer messages，BoundaryValidator 保持默认关闭；不实现 Repair，不开始 Phase 5 或 Phase 6，也不追加 Phase 4R 解释性批次。

## 验证

- 定向 unit/quality：22 passed；
- `compileall`：通过；
- Writer/LLM 在协议纠偏和评估阶段调用数：0；
- 生产 Writer、Prompt、SceneSpec compiler、Validator 和 Context Broker：无修改；
- 私有正文进入 Git/公开报告：0。
