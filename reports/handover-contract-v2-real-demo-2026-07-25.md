# Subsection Handover Contract V2 真实 Demo 验收

状态：`real_demo_failed_output_truncation`。生产默认继续使用 V1，V2 不晋级。

## 固定任务

- task_id：`cd830826-61b0-4840-b2a7-45cf807599e0`
- 任务状态：`completed`
- 实际完成小节：4
- 正文主调用：4
- Mandatory Event 实际重试：0
- 总耗时：242.7 秒
- 总 Token：36,525
- Writer Token：25,277

任务正文、checkpoint 和最终 Review 均正常完成。V2 Handover 的失败没有阻断正文提交，也没有触发 Writer 重试，说明 fail-open 隔离行为符合设计。

## V2 提取结果

| 小节 | 输入 Token | 输出 Token | 延迟 | finish reason | 持久化状态 |
|---|---:|---:|---:|---|---|
| S1.1 | 2,130 | 600 | 5.9s | `length` | `error/ValueError` |
| S1.2 | 2,491 | 600 | 6.3s | `length` | `error/ValueError` |
| S1.3 | 3,029 | 600 | 6.1s | `length` | `error/ValueError` |
| S1.4 | 2,311 | 600 | 6.5s | `length` | `error/ValueError` |

四次调用全部精确耗尽 600-token 输出上限并以 `finish=length` 结束，随后 JSON 解析失败。合计消耗 12,361 known tokens 和约 24.8 秒，但没有形成 typed contract。

TaskStore 中存在4条小节记录，`pending=0`、记录级 error envelope 为0；四条记录均为 `execution_status=error`、`error_type=ValueError`，且 `typed_contract_hash`、accepted/rejected claim count 均为空。确定性的 next-boundary 编译仍完成，四条记录均保留 boundary hash 和 source manifest；`handover_chain_count=0`。

## 验收判断

- 单小节仅一次 Handover 调用：通过。
- 未增加正文调用或重试：通过。
- 失败不影响正文、checkpoint 和任务完成：通过。
- accepted claim 的 source/hash/span 追溯：不可评估，解析前已失败。
- 无依据心理推断、stale fact、无来源 arc 的真实拦截效果：不可评估。
- 下一小节承接覆盖：未形成 handover chain，未通过。
- V2 可作为生产默认：未通过。

这次失败发生在“模型输出长度与结构契约不匹配”这一层，早于 typed validator。它不能证明 validator 规则错误，也不能证明 V2 内容质量好或坏。

## 决策

V2 状态由“工程门槛通过、允许一次真实 Demo”收口为 `real_demo_failed_output_truncation`：

- `WRITER_HANDOVER_CONTRACT_VERSION` 默认继续为 `v1`；
- 不再运行同配置 Demo；
- 不把 V2 接入下游 StateFrame、OutcomeBundle 或 Writer；
- 不以本次结果修改 validator、角色弧、SceneSpec 或正文生成链；
- 下一步若另行授权，只允许先做一个独立的 V2 输出契约紧凑化修复，在离线证明可稳定容纳于既有上限后，才讨论是否值得再次真实验证。

本报告不包含正文、Prompt、messages、数据库内容或私有 Handover 文本。
