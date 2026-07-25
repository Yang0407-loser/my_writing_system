# Subsection Handover Contract V2 最小修复

状态：核心定向验证通过，剩余兼容测试与 compileall 因当前执行额度限制未运行；生产默认仍为 V1。

## 现有链路

现有 `HANDOVER_EXTRACTION_PROMPT` 位于 `app/utils/prompt_templates.py`。Writer 在每个小节正文主调用后，将正文前 3000 字、角色上下文和当前弧线事件交给一次 Handover 提取调用。

旧结果中的 `new_facts` 会由 `StateCommitter` 写入 WorldState；`arc_progress=done/deviated` 会更新 EventGraph；`found_contradictions` 会进入回溯建议。小节结果先进入 `section_handover_parts`，整章结束后才汇总为 `handover_chain`。本任务没有改变同章下一小节不消费 Handover 的既有行为。

## V2 设计

V2 把模型输出视为“待验证建议”，而不是权威工件。每条状态、事实、开放事件和角色弧进展都必须带 source ID、source hash、精确字符区间和最长 140 字的原文证据。验证失败只丢弃单条内容，不让整次 Handover 失败，也不触发 Writer 重试。

心理解释采用两层防护：首先要求主体、动作/状态和对象都能在证据中定位；其次对已知高风险解释性表达做拒绝检查。关键词只用于拒绝风险项，不用于证明事实正确。

角色弧进展必须同时具有 event ID、milestone source ID/hash、当前正文证据和明确完成状态。没有来源的 `pending` 不会再进入权威工件或修改 EventGraph。

下一场景边界由当前与下一小节 outline 确定性编译，不交给 Handover 模型自由推断。冲突只记录为 `boundary_status=conflicted`，不自动选择或修复任一方。

## 兼容与持久化

`WRITER_HANDOVER_CONTRACT_VERSION` 默认是 `v1`，非法值回退到 `v1`。V1 的 Prompt、返回 note、调用次数和副作用保持不变。V2 仍最多使用现有的一次 Handover 调用，并把通过验证的内容适配为普通 legacy note，因此无需同时修改 WorldState、EventGraph 或章末汇总消费者。

`SubsectionHandoverRecord` 只增加可选 V2 元数据。旧记录继续可加载，不新增数据库表或列，不迁移历史记录。

## 固定负面回归边界

sealed V1 审计材料只用于验证已知失败类别被新契约拦截，不会把 V1 输出伪装为 V2 输出，也不宣称 V2 的真实生成 Precision 已达标：

- 3 条已确认无依据心理推断：V2 策略全部拦截；
- 1 条 stale `new_fact`：V2 策略拦截；
- 15 条缺少 milestone 来源的 `arc_progress=pending`：不进入 V2 权威 arc progress；
- 4 个真实 outline 边界可确定性构建；
- 既有审计中的 2 个边界风险继续被记录，不能据此声称 Writer 已修复连续性。

## 决策

核心定向测试 32/32 通过，覆盖 V1 Prompt hash、V1/V2 单次调用、旧记录恢复、V2 证据校验、可选持久化字段和 sealed 负面回归。追加的 Writer 兼容测试与 compileall 因当前执行额度限制未获运行许可，未伪装为通过。

因此当前仍停在 `engineering_gate_pending_remaining_verification`：暂不授权真实 Demo。补完剩余验证后，即使工程门槛通过，也只允许建议一次真实四小节 V2 Demo；在 Demo 完成前不得把 V2 设为默认。
