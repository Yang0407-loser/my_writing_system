# Phase 4R Batch R3 外部 Agent 执行说明

本执行包比较四个冻结场景 Q4/Q6/Q7/Q8 的三种 Writer 输入：

- A：`legacy_full`
- B：`budgeted_broker`
- C：`budgeted_broker + SceneSpec`

总计 12 次生成。模型、system prompt、当前写作请求、RAG top-5及顺序、角色/关系规则、风格、temperature、top_p 和 max_tokens 已在 `prepare` 阶段冻结。候选顺序按场景确定性随机化，匿名映射保存在 gitignored 私有目录。

## 环境边界

`.phase4r_r3_runtime/` 包含故事正文、规则、RAG、完整 Writer messages、匿名映射和后续生成正文。它不得提交 Git、上传公共附件或复制到未经授权的环境。

只有确认当前执行环境允许处理并发送这些私有输入时，才能运行生成命令。脚本不会因为存在 API key 就自动生成；必须显式传入 `--confirm-private-inputs`。

## 四个命令

在项目根目录执行。

### 1. Prepare

已在当前 checkpoint 执行。需要验证或重建时：

```powershell
& .\.venv\Scripts\python.exe -m tests.benchmarks.phase4r_r3_package prepare
```

该命令只读本地故事、规则和 Chroma，不调用 Writer LLM。它会重新校验冻结 A/B messages hash，并生成私有 messages 和公开 manifest。

### 2. Run

仅在获准处理私有输入的 Agent 环境执行：

```powershell
& .\.venv\Scripts\python.exe -m tests.benchmarks.phase4r_r3_package run --confirm-private-inputs
```

预期调用 12 次。执行前应确认 `prepare.json` 中的模型和生成参数与目标生产配置一致。任何 messages hash 漂移都会在调用前中止。

### 3. Import

如果外部 Agent 在同一 workspace 运行，可直接校验原目录：

```powershell
& .\.venv\Scripts\python.exe -m tests.benchmarks.phase4r_r3_package import --source-dir .phase4r_r3_runtime
```

如果结果来自另一受信环境，将其私有运行目录放到一个安全路径，然后把 `--source-dir` 指向该路径。Import 会校验 query、candidate、messages hash 和输出 SHA-256，再复制到本地 gitignored runtime。

### 4. Evaluate

先生成匿名复核模板：

```powershell
& .\.venv\Scripts\python.exe -m tests.benchmarks.phase4r_r3_package evaluate
```

复核者只能读取 `candidate_*.txt` 和 `blind_review.template.json`，不得读取 `private_mapping.json`。完成后另存为 `.phase4r_r3_runtime/blind_review.completed.json`，填写来源明确的 `review_provenance`，再执行：

```powershell
& .\.venv\Scripts\python.exe -m tests.benchmarks.phase4r_r3_package evaluate --review .phase4r_r3_runtime/blind_review.completed.json
```

## 停止条件

外部 Agent 只负责运行和保留私有结果，不修改 Writer、Prompt、Broker、SceneSpec、RAG 或生成参数。完成 12 次后停止，不切换生产，不开始 Phase 5/6。原始正文继续留在 gitignored runtime，返回时只报告命令状态、12个输出 hash、token 和延迟摘要。
