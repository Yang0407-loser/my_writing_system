# SceneSpec → BoundaryValidator 真实工件贯通

日期：2026-07-24

状态：工程贯通完成，真实 shadow 样本仍为 0

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
- 真实生成正文：0
- 完整正文、SceneSpec rendered、Prompt/messages、数据库或密钥写入公开报告：0

## 唯一后续入口

只允许在专用 CMD worker 上运行一个自然真实写作任务：

```cmd
set WRITER_EXECUTION_CONTRACT_MODE=off
set WRITER_SCENE_SPEC_MODE=shadow
set WRITER_BOUNDARY_VALIDATOR_SHADOW=true
set WRITER_INCREMENTAL_SECTION_REVIEW=false
set WRITER_CONDENSE_MODE=warn
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing
```

该配置下 SceneSpec 只构建、不注入 Writer；Validator 读取同一 typed 工件，且不能改变正文或任务结果。任务结束后必须立即停止并按真实警告价值判断，不得用旧 Q4/Q6/Q7/Q8 样本重算指标，也不得追加 Repair、阻断或自动重写。

恢复默认：

```cmd
set WRITER_SCENE_SPEC_MODE=off
set WRITER_BOUNDARY_VALIDATOR_SHADOW=false
```
