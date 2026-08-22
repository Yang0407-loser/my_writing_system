# Phase 4R Batch R6A：BoundaryValidator 默认关闭的 shadow 接入

> 日期：2026-07-20
> 状态：已完成并停止
> 生产行为：继续 `legacy_full`，feature flag 默认关闭

## 接入结果

稳定 hook 位于小节正文成功执行 `StateCommitter.commit_subsection()` 并完成 `SubsectionPipeline.record_commit()` 之后。固定顺序为：

`GenerationController → StateCommitter → record_commit → ShadowBoundaryValidationRunner → 返回原始 Writer 结果`

`WRITER_BOUNDARY_VALIDATOR_SHADOW` 默认值为 `false`。关闭时不构建 ValidationContract、不运行 Validator、不写 shadow 记录。Writer messages、Prompt、RAG、ContextManager、生成参数、正文返回值、checkpoint 版本、幂等键和提交顺序均未改变；本批 Writer/LLM 调用为 0。

## 生产级提取

R5 的通用确定性规则已迁至 `app/writing/boundary_validator.py`，benchmark 现在单向导入生产实现，`app` 不导入 `tests`。迁移后重新生成 R5 冻结预测，原始字节 SHA-256 仍为：

`fb6e21589d362b9e43f8da00ed8f99709c2d90804a2c72be63e691553baa42c0`

typed `SceneSpec` 通过 `ValidationContract.from_scene_spec()` 适配；unknown 状态不升级为 confirmed。生产 Writer 不直接导入 R2 SceneCompiler、StoryStateView 或 SceneSpec，也不把 SceneSpec 注入 messages。当前默认没有 SceneSpec provider，因此即使另行显式打开 flag，也只会记录安全 skip；R6B 必须另行授权真实 provider 和采样范围。

## 隔离行为

- 默认 sink 为 `NoOpShadowValidationSink`，测试使用 `InMemoryShadowValidationSink`，未新建数据库。
- 缺少 provider、缺少 SceneSpec 或没有可执行确定性规则时分别记录明确 `skip_reason`。
- Validator 或 provider 异常转为 `shadow_error`，只记录异常类型、规则版本、耗时和 output hash，不传播给 Writer，不回滚提交，不触发 retry 或 Repair。
- 相同 task/section/subsection/output hash 的重复观察不重复写记录。
- 记录不含完整正文、完整 messages、Prompt、API key、数据库或 Chroma 内容；证据短摘录最长 140 字，`production_effect=false`。

## 验证与结论

全量回归：unit 229 passed、integration 9 passed、quality 85 passed、compileall passed。R5 冻结 hash 门禁通过。

本批只证明默认关闭的 post-commit shadow 边界和失败隔离成立，尚未获得任何真实运行样本，也不证明 Validator 已具备生产阻断能力。只能建议等待用户另行授权 R6B 真实 shadow 采样；不切生产，不开始 Repair、Phase 5 或 Phase 6。
