# Subsection Handover Contract V2.1 紧凑输出契约

状态：`engineering_gate_passed_one_v21_demo_authorized`。生产默认继续使用 V1，本轮未调用 Writer/LLM，也未运行真实 Demo。

## 根因与字段成本

V2 唯一真实 Demo 的根因保持为 `model_output_truncated_before_json_parse`。四次提取均在600输出token处结束，typed validator尚未执行。

确定性结构账本显示：V2最小空结构约36 estimated tokens，代表性典型结构约1,371，代表性最坏结构约3,030。最坏结构中重复出现9次source ID、14次source hash、9份evidence excerpt和27个长枚举字段。next boundary和accepted/rejected结论在V2中本来就不是模型输出，输出成本为0；但已经本地编译的boundary仍被重复放入V2输入Prompt。

## V2.1传输层

V2.1新增独立`CompactHandoverPayloadV21`，只传输：

- `s`：最多4条节尾状态；
- `o`：最多3条未完成事件；
- `f`：最多3条新事实；
- `a`：最多2条角色弧进展；
- source registry短索引、精确start/end、短枚举和最长16字的结构化语义。

状态和事实短语使用`主体|动作/状态|对象`，open event使用`人物,人物|动作|对象`，因此压缩后仍可恢复typed contract的主体、谓词、对象和actors，而不是把完整语义压成一段不可分文本。

模型不再输出source ID、source hash、evidence excerpt、完整milestone对象、next boundary、validator状态或拒绝理由。boundary继续由outline确定性编译；validator语义与legacy adapter保持V2实现。

## Source Registry与恢复

Source registry按`generated/current/next/arc`固定优先级和source ID排序，索引从0开始。Prompt只展示索引、来源类型、可选milestone event/character和来源文本，不展示source hash。

解析后，本地根据索引取回权威source，再验证`0 <= start < end <= len(source.text)`，从`source.text[start:end]`生成最长140字的evidence excerpt并补回source ID/hash。非法索引、空区间、越界区间或不合法短语只拒绝对应item。arc进展的event ID、character ID和milestone source/hash全部从本地registry恢复。

Source registry属于输入成本，不计入输出容量。固定真实V2任务的四次Handover输入合计9,961 actual tokens；V2.1尚未真实运行，因此不能伪造精确输入降幅。本轮优化结论只针对输出契约容量。

## 容量

使用项目现有`estimate_tokens`：

| Payload | 字符 | estimated tokens |
|---|---:|---:|
| V2.1典型 | 148 | 127 |
| V2.1最坏合法 | 452 | 444 |

空wrapper为29 tokens。输出上限继续为600，最坏合法payload保留156 tokens安全余量，超过100-token工程门槛。第一次24字短语设计曾得到579 tokens并被测试拒绝，随后在不删除证据引用的情况下收紧为16字结构化短语。

## 运行与失败语义

V2.1每小节仍只复用一次Handover调用，temperature、JSON模式和600-token上限不变。LLM client只增加可选的同步metadata sink，用于把实际finish reason和token数返回给当前调用者，不改变其他调用默认行为。

当`finish=length`时，V2.1不解析、不修补残缺JSON、不发起第二次模型调用、不触发Writer重试，也不提交半成品Handover。正文、checkpoint和Review继续走fail-open路径。

新增可选sidecar字段只保存版本、hash、token、finish reason、截断状态和计数，不保存模型原始输出、正文、Prompt、messages或source registry文本。旧V1/V2记录继续可加载，数据库结构不变。

## 验证与决策

V1/V2/V2.1、Writer兼容、sidecar持久化和quality定向测试共60项通过，受影响模块compileall通过；既有sealed V1失败类别仍由未修改的V2 validator策略拦截：3条无依据心理推断、1条stale fact、15条无milestone来源arc pending；4个outline boundary仍由本地构建，2个已知boundary conflict仍只记录。该回归不等于真实V2.1生成质量已经通过。

工程条件允许另行授权唯一一次真实四小节V2.1 Demo，但本轮没有自动执行。默认仍为`WRITER_HANDOVER_CONTRACT_VERSION=v1`，V2.1不接入StateFrame、OutcomeBundle或Writer输入。
