# 真实 Canary 约束检测归因审计

## 结论

下一步只建议 **A：先将 mandatory-event 自动整节重写降级为告警**。本轮不执行该修改。

这次运行暴露的是两条互相独立的链：GenerationController 的 mandatory-event 检测造成了 8 次额外整节生成；EventGraph 的角色弧 post-check 只在终稿后告警，没有触发重写。不能把后者的 15 条告警当作前者的重试原因。

mandatory 检测的 Precision 无法诚实计算。系统没有持久化初稿和第一次重写稿，也没有记录每轮究竟是哪几条事件、哪 5 个关键词被判缺失；因此 8 次检查全部记为 `unavailable_generation_attempt`，不得用最终稿倒推中间稿。现有证据不支持继续让它自动触发昂贵重写：它使用 `list(set(words))[:5]`，关键词选择随 Python hash seed 变化；检测只做 50% 字面命中；第二次重写完成后直接退出循环，终稿甚至不会再次校验。

角色弧 post-check 可以用终稿核验。15 条“未体现”中，8 条实际完整出现但被字面匹配漏掉，6 条只完成部分，1 条确实缺失。按“完整缺失”计算，告警 Precision 为 **6.67%（1/15）**；若把部分完成也算成有行动价值的告警，则为 **46.67%（7/15）**。完整事件误报率为 **53.33%（8/15）**。这是本次 4 小节的任务内结果，不代表通用准确率。

## 真实成本

| 项目 | 结果 |
|---|---:|
| 原计划小节正文调用 | 4 |
| 实际小节正文调用 | 12 |
| mandatory 重试调用 | 8 |
| 额外调用占正文调用 | 66.67% |
| 重试流式生成耗时 | 242.2 秒 |
| 重试占正文流式生成耗时 | 64.86% |
| 重试占总墙钟时间 | 37.35% |
| 总耗时 | 648.5 秒 |
| 总 Token | 46,280 |
| Writer Agent Token | 34,413 |
| 总预估成本 | $0.0201 |

日志没有保存每次流式生成的 token usage，因此不能把额外 token 或费用从总账中可靠拆出。242.2 秒是 8 次重试流式调用的可确认耗时，不含无法单独归属的外围处理时间。

## Mandatory 检测

| 小节 | 初稿判缺失 | 第一次重写判缺失 | 中间稿 | 归因 |
|---|---:|---:|---|---|
| 1.1 | 1 | 1 | 未保存 | `unavailable_generation_attempt` |
| 1.2 | 3 | 3 | 未保存 | `unavailable_generation_attempt` |
| 1.3 | 3 | 3 | 未保存 | `unavailable_generation_attempt` |
| 1.4 | 5 | 5 | 未保存 | `unavailable_generation_attempt` |

四节全部重试两次的直接原因，是每次前两稿都未通过短语字面检测。为什么具体没通过无法回放：日志只保存缺失数量，未保存缺失事件、实际选中的无序关键词或候选正文。代码层仍能确认四项结构风险：

1. 小节 `key_points`、节级 `key_points` 和完整 `description` 全被提升为“缺失任一即重写”。
2. 长自然语言事件被切成任意 2–4 字连续片段，而不是人物—动作—对象结构。
3. 片段先进入 `set`，再任取前 5 个，实际 Worker 的选择不可复现。
4. 最多两次重写后无条件接受最后一稿，不再验证是否修复。

因此 mandatory Precision 为 `null`，不是 0，也不是“多数误报”。我们能确认它造成了成本，却不能在缺少中间稿时确认 8 次重试中有几次由误报造成。

## 角色弧告警

| 小节 | 角色 | 核心事件 | 终稿判断 | 归因 | 建议级别 |
|---|---|---|---|---|---|
| 1.1 | 吴阿姨 | 楼下广场舞声音被林晚听见 | 完整 | `semantic_paraphrase_false_negative` | observational |
| 1.1 | 季晴 | 得知裸辞并质问逃避现实 | 完整 | `cross_sentence_false_negative` | soft |
| 1.1 | 林晚 | 第20版、辞职、面包香、小黑板 | 完整 | `cross_sentence_false_negative` | hard |
| 1.1 | 周野 | 揉面香气触动林晚且本人未察觉 | 部分 | `partial_event_completion` | observational |
| 1.2 | 顾衍 | 指点四点半拍摄并递茶 | 完整 | `cross_sentence_false_negative` | observational |
| 1.2 | 林晚 | 三次蹲守并克服自我怀疑 | 部分 | `partial_event_completion` | hard |
| 1.2 | 周野 | 默许拍摄并递水 | 完整 | `cross_sentence_false_negative` | soft |
| 1.3 | 季晴 | 阅读首篇草稿并留下问号 | 缺失 | `true_missing_event` | soft |
| 1.3 | 顾衍 | 调亮暖灯并在打烊后交谈 | 部分 | `partial_event_completion` | observational |
| 1.3 | 林晚 | 台阶写首篇记录并反思生活 | 完整 | `cross_sentence_false_negative` | hard |
| 1.3 | 周野 | 禁闪光、继续揉面、动作接纳 | 完整 | `cross_sentence_false_negative` | soft |
| 1.4 | 吴阿姨 | 与夜归人交谈并关心作息 | 部分 | `partial_event_completion` | observational |
| 1.4 | 顾衍 | 推荐诗集并意识到社区连接 | 部分 | `partial_event_completion` | soft |
| 1.4 | 林晚 | 记录众人并意识到并非独自孤独 | 完整改写 | `semantic_paraphrase_false_negative` | soft |
| 1.4 | 周野 | 默默递面包并以眼神表示理解 | 部分 | `partial_event_completion` | soft |

季晴 1.3 是唯一确认的完整缺失：正文只写林晚把文档转发给她，阅读、问号评论和复杂情绪都没有在该小节发生。1.4 的回复对应下一篇记录，不能倒算为 1.3 已完成。

其余 8 条完整误报的共同点是人物、动作、对象或结果被拆在相邻句中，或者使用代词和叙事改写。当前正则把“林晚在凌晨”切成“林晚在凌”“晨……”一类固定字符窗，本身并不代表语义单元。

## 角色弧预编排

本次 5 个角色生成 15 个 milestone：吴阿姨 2、季晴 2、顾衍 3、林晚 4、周野 4；四个小节分别承载 4、3、4、4 条。逐条按职责重分后只有 3 条属于 `hard_arc_transition`，另有 7 条 `soft_arc_progress`、5 条 `observational_texture`。普通配角动作和环境声音被上游 Prompt 的“覆盖所有出场小节”要求提升成了“本节必须体现”。

日志中的 115 条“因果边”可精确拆为：

- 同一角色的相邻 milestone：10 条；它们只证明时间相邻，没有因果类型或证据。
- 同一章节 15 个 milestone 两两互连：105 条，计算即 `15 × 14 / 2`。
- 具有明确方向、因果类型和来源证据的边：0 条。

`link_events` 实际创建双向 `related_events`，因此这些边不能被称为已验证因果边。角色弧预编排确实过密，但它没有造成此次 8 次 mandatory 重写；两类问题应分开修改。

## 责任边界

- **mandatory 检测器**：对 8 次额外 Writer 调用负责；Precision 因证据未持久化而不可估；当前不应继续拥有自动整节重写权限。
- **角色弧检测器**：对 15 条终稿告警负责；其中 8 条完整误报；仅告警，没有增加调用。
- **角色弧预编排**：对约束密度负责；12/15 并非硬状态转变，105/115 边仅由同章共现产生。
- **正文真实遗漏**：确认 1 条季晴回应缺失，来源是角色弧 soft milestone 未执行；它不在 1.3 outline key points 中，也不是 SceneSpec 必须事件。

## 唯一下一步

选择 **A：先将 mandatory-event 自动重写降级为告警**。理由不是已经证明 8 次全是误报，而是该链实际消耗最大、结果不可审计、关键词不可复现、最后一次重写不复验。完成降级后，再单独授权收缩角色弧规划契约；本次不同时修改两个核心变量。
