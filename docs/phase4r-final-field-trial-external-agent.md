# Phase 4R 最终真实写作试验：外部 Agent 执行说明

本包使用真实任务 `8e92759b-3644-42d4-a473-492bfe6b0830` 在正文生成前冻结的 checkpoint。私有故事状态、messages、候选正文、A/B 映射和用户评审只允许存在于 `.phase4r_final_trial_runtime/`，不得提交、粘贴到公开报告或发送到未获授权的服务。

## 变量

- A：`legacy_full`
- B：`legacy_full + SceneSpec`

两臂共享冻结的规则、关系、伏笔、世界状态、前文、RAG top-5、风格参数、模型参数和写作大纲。两条分支都从同一个 pre-generation checkpoint 独立顺序生成 4 个小节。正文主调用为 8 次；风格行为和上一节交接简报各生成一次并由两臂共享，单独记为辅助调用。

## 执行

在项目根目录运行：

```powershell
& .\.venv\Scripts\python.exe -m tests.benchmarks.phase4r_final_field_trial run --confirm-private-inputs
```

执行前必须确认：

- `.phase4r_final_trial_runtime/package.public.json` 的 `status` 为 `prepared_not_generated`；
- 当前 `LLM_MODEL` 和 endpoint host 与 package 一致；
- 私有数据允许发送到当前配置的模型 endpoint；
- 不打开或修改 `arm_mapping.private.json`。

执行后应得到：

- `run_manifest.json`；
- `scene_01` 至 `scene_04`，每个目录两份匿名候选；
- `user_review.template.json`；
- `arm_mapping.private.json`。

不要运行生产 Writer 任务，不要修改 Writer、Prompt、SceneSpec、Validator 或 ContextBroker。不要增加第三臂、重试网格或旧冻结样本。

## 用户评审

用户只查看 `scene_01` 至 `scene_04` 的匿名正文和 `user_review.template.json`。`preference` 与 `better_continuation_candidate` 只允许：

- `candidate_01`
- `candidate_02`
- `tie`
- `both_unusable`

除修改成本外，所有质量判断字段都必须填写。`positive_effect_note` 只描述候选相对另一版的具体优点，盲审时不要猜测它属于哪个 arm。

`edit_characters` 和 `edit_minutes` 是可选诊断项：

- `null` 表示未测量；
- `0` 表示实际确认无需修改；
- 正数表示实际测量值。

不得把未测量的值写成 `0`。修改成本不参与本轮 go/no-go；如果没有实际改稿，两项保持 `null`。评审完成后保存为 `.phase4r_final_trial_runtime/user_review.completed.json`。

最后执行：

```powershell
& .\.venv\Scripts\python.exe -m tests.benchmarks.phase4r_final_field_trial evaluate --review .phase4r_final_trial_runtime\user_review.completed.json
```

评估结束后停止，不自动修改生产代码或开始任何后续 Phase。
