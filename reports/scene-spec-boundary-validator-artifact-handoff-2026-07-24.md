# SceneSpec → BoundaryValidator 真实工件贯通

日期：2026-07-24

状态：工程贯通成功；真实 shadow 已完成，Validator 不晋级

生产默认：SceneSpec `off`，BoundaryValidator shadow `false`

## 结论

原断点位于 SceneSpec 构建与 post-commit Validator 之间：`SceneSpecCanaryController` 已在生成前构建 typed `SceneSpec`，但 `SceneSpecApplication` 没有保存该工件；`ShadowBoundaryValidationRunner` 又只支持一个生产中从未注入的 provider，因此启用 Validator 后只能得到 `scene_spec_provider_unavailable`。

本次修复将同一份 typed `SceneSpec` 作为小节局部工件直接传递：

`PromptBuilder → SceneSpecCanaryController → Writer → 既有确定性检查 → StateCommitter → ShadowBoundaryValidationRunner`

没有在提交后重新编译 SceneSpec，没有读取 outline、数据库、Redis、Chroma 或人工标签，也没有增加缓存或状态库。

## 工程变更

- `SceneSpecApplication` 新增 `spec` 与 `source_manifest`。
- shadow/canary 构建成功时携带 provider 返回的同一 `SceneSpec` 对象。
- off、白名单拒绝、构建失败及安全回退时不携带半成品。
- Writer 在每个小节开始显式将 `scene_spec_application` 置空，防止跨小节泄漏。
- `observe_committed()` 新增可选 `scene_spec` 参数；显式工件优先，兼容 provider 仅供旧测试或旧调用使用。
- 无显式工件且无兼容 provider 时统一记录 `scene_spec_unavailable`。
- shadow 记录新增 `scene_spec_hash` 与 `scene_spec_delivery`，后者只取：
  - `explicit_artifact`
  - `compatible_provider`
  - `unavailable`

## 保持不变

- `WRITER_SCENE_SPEC_MODE=off`
- `WRITER_BOUNDARY_VALIDATOR_SHADOW=false`
- `WRITER_EXECUTION_CONTRACT_MODE=off`
- SceneSpec typed 语义、渲染文本、hash 算法和 400-token 上限
- BoundaryValidator V1 规则、关键词、阈值和语义
- Writer messages、正文返回值、模型参数、调用次数和重试行为
- StateCommitter、checkpoint 版本、内容、顺序和幂等键
- Validator 只观察，不阻断、不重写、不 Repair，`production_effect=false`

## 冻结回归

四个真实试验 SceneSpec hash 保持：

1. `8f59121b834a7587b3e42eb75cc91a976d580d159ef5cce3c828c075cb46a2d8`
2. `3d144ed009b624331321c5c40daa519c4bfe4ca92c0b5626e03a07a64a0ba7d2`
3. `5150786e3fcd504e42e4bc56282cb4cc8ce15a43d29afebc2098515363249f9b`
4. `6e4b3567c391777b8f2736ce5fd5518fbfd95cc6e7f39879c06fc3927e460459`

R5 冻结预测原始字节 hash 保持：

`fb6e21589d362b9e43f8da00ed8f99709c2d90804a2c72be63e691553baa42c0`

## 验证

- 定向 unit/integration/quality：51 passed
- 受影响模块 compileall：passed
- 实现期间 Writer/LLM 调用：0
- 工程实现阶段真实生成正文：0
- 完整正文、SceneSpec rendered、Prompt/messages、数据库或密钥写入公开报告：0

## 唯一真实 shadow 结果

真实任务：`c3fd7953-acfd-4dca-9ee8-f9a907680e23`

- 任务状态：`completed`
- 小节数：4
- SceneSpec 构建：4/4，均为 shadow、未注入 Writer、无 fallback
- Validator 记录：4/4
- `scene_spec_delivery=explicit_artifact`：4/4
- SceneSpec hash 与对应 Validator 记录一致：4/4
- `scene_spec_provider_unavailable`：0
- shadow error：0
- Writer 正文主调用：4
- Mandatory Event 实际重试：0
- Validator 引入的 Writer/LLM 调用：0
- `production_effect=false`：4/4
- 正文、checkpoint、final review 与任务完成链正常

检测结果为 2 pass、1 fail、1 skipped。唯一 `fail` 位于第 1.2 小节，规则
`boundary:q08:current_photograph` 将“林晚按下快门”判为边界后事件。但该小节
outline 明确要求第三个周六拍到周野揉面的背影；下一小节目标是退出店外并完成
记录草稿。因此“按下快门”属于当前小节必须完成的事件，不是越界。这是一条
已确认误报，根因是 R5/Q8 场景特化的固定规则被用于一般 `future_boundary`
SceneSpec。

另外两条 `pass` 的 `required_event_results` 均为空，不能证明 planned event
检测有效；末小节因 `no_executable_deterministic_rules` 跳过。这个自然任务没有
产生用户可确认有用的 Validator 问题。

## Go/No-Go

根据既定规则选择 No-Go：

- 工件传递修复真实通过，可以保留为工程资产。
- BoundaryValidator V1 生产路线关闭，继续默认关闭。
- 不扩大关键词、阈值或场景矩阵追逐该样本。
- 不实现非阻断 UI 警告、Repair、阻断或自动重写。
- 若未来重启 Validator，必须先用通用 typed event/boundary 契约替代 Q4/Q7/Q8
  场景特化规则，并作为新的独立方向重新授权。

## 默认配置

关闭专用 worker 后恢复：

```cmd
set WRITER_SCENE_SPEC_MODE=off
set WRITER_BOUNDARY_VALIDATOR_SHADOW=false
```
