# StateFrame Batch 1：只读契约与结构基线

> 日期：2026-07-20  
> 状态：完成并停止  
> 生产行为：未改变，继续 `legacy_full`

## 目的

StateFrame 从 Writer 拆走“写前恢复当前状态”的职责，但不负责本节事件规划。它与 SceneSpec 的边界是：

- StateFrame：现在是什么状态；
- SceneSpec：这一小节应该发生什么；
- 规则系统：哪些行为不可违反。

本批只验证 typed contract、确定性分类和来源追溯，不注入 Writer，不调用 LLM，也不评价生成质量。

## 实现

新增不可变 `StateFrame`，包含：

- `temporal_state`；
- `location_state`；
- `character_presence`；
- `persistent_state`；
- `relationship_state`；
- `open_loops`；
- `unknowns_and_conflicts`；
- `evidence`、`source_hash`、`frame_hash`；
- `excluded_assertion_ids`。

`StateFrameCompiler` 只按显式 predicate 分类，不扫描文本关键词。confirmed 状态才能进入当前事实分类；unknown/conflicted 原样保留。planned events、hard constraints、历史 `arc_milestone` 和未识别 predicate 被明确排除，避免与 SceneSpec 和规则上下文重复。

## 离线结构结果

4 类合成契约场景覆盖时间/未知、地点/人物在场、持久状态/关系、open loop/冲突：

| 指标 | 结果 |
|---|---:|
| 场景数 | 4 |
| 来源追溯率 | 100% |
| unknown/conflicted 保留 | 通过 |
| planned/hard 排除 | 通过 |
| 平均 estimated tokens | 33 |
| 最小/最大 estimated tokens | 26 / 40 |
| Writer/LLM 调用 | 0 / 0 |

33 tokens 仅来自合成的短契约值，用于验证渲染结构，不代表真实故事状态的 token 成本或 Writer 输入降幅。

## 验证

- StateFrame + SceneSpec 定向 unit：12 passed；
- StateFrame quality：3 passed；
- 定向 `compileall`：通过；
- 生产 Writer 导入 StateFrame：0；
- Redis/SQLite/Chroma 写入：0；
- 私有故事正文进入报告：0。

## 限制与下一入口

当前无法从结构测试推断现有状态源是否提供足够细粒度的 predicate。特别是 WorldState 目前大量内容统一映射为 `world_fact`，可能导致真实 StateFrame 过宽；handover 也可能只有非结构化字符串。

下一步只能另行执行真实状态源覆盖审计，回答：

1. 现有事实中多少能进入明确分类；
2. 多少只能落入通用 `world_fact` 或 excluded；
3. 时间、地点、人物在场和持久状态各自缺失多少；
4. 问题来自 StateFrame 分类器，还是上游根本没有结构化状态。

在覆盖审计完成前，不接入 Writer，不生成 A/B，不扩充关键词表，不创建新状态库。
