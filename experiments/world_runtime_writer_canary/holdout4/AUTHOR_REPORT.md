# WR4 metadata holdout 金标（第四批·人工作者）作者报告

作者：independent-holdout-author-human-batch4
创建日期：2026-08-07

## 1. 语料

- task_id：`20f02dc7-dc64-4233-bd6c-06a6d8647dbe`
- 快照文件：`fixtures/wr4_metadata_holdout_corpus_snapshot_v1.json`
- 快照 SHA-256：`6e52a0f738fb89f0b43fdc8207b63abd4e052f9694b4747c560b7b3b8b8a309a`（读取字节核对一致）
- corpus_hash：`05420ab03eb3ec11ac42f07b334ca434ba6a79b9250cbec505f2857b99317878`
- chunk 数：378（rows 378，content_hash 全部唯一）；section 1..18，S1 仅 1 小节，S2..S18 各 3 小节
- 人物：林晚、周野、季晴、顾衍、吴阿姨
- 语料新颖性说明：当前可用快照中无同书第四实例（已知实例 07d1391e / 3a4e561a / 20f02dc7），本批与第三批同一语料；独立性与“未见过金标”由查询字符串与证据短语对前三批（dev、第一 holdout、holdout2 程序化、holdout3 人工作者）零重叠保证，语料级新颖性缺失是明确局限。

## 2. 条目构成

- 共 14 条：`continuity_fact` 8 条（J1–J8）、`wr_key_evidence` 6 条（J9–J14）
- `requires_causal_retrieval=true` 共 2 条（J3 图文失控链、J8 匿名帖围堵链）
- `query_intent` 全部为规范词表列表（character/event/foreshadowing/scene），供 runner preflight 校验；评测查询构造镜像生产（全部 intent 参与），本批不再用散文 intent 传参
- WR 键语义覆盖 7 种（≥5 要求）：open_days/weekday（J9）、clock（J10）、operation_state（J11）、access/light（J12）、knowledge（J13）、employment/employment:lin-wan/status（J14）

## 3. 写作点分布

| 区间 | 条目 | 写作点 |
| --- | --- | --- |
| 前段 2–6 | J1 | S3.2 |
| 前段 2–6 | J9 | S4.2 |
| 前段 2–6 | J2 | S5.1 |
| 前段 2–6 | J10 | S6.2 |
| 中段 7–13 | J3 | S7.1 |
| 中段 7–13 | J11 | S7.2 |
| 中段 7–13 | J12 | S8.3 |
| 中段 7–13 | J4 | S9.2 |
| 中段 7–13 | J5 | S10.1 |
| 中段 7–13 | J13 | S10.3 |
| 中段 7–13 | J6 | S11.1 |
| 中段 7–13 | J7 | S12.3 |
| 后段 14–18 | J8 | S15.1 |
| 后段 14–18 | J14 | S17.1 |

前段 4、中段 8、后段 2，三个区间均覆盖。

## 4. 每条 prior-context 判定依据

规则：`s < current_section`，或 `s == current_section` 且证据 subsections 均 `< current_subsection`。

- J1（写 S3.2）：证据 S2.3（凌晨三点十五分醒、富士相机、声控灯台阶）→ 2 < 3。
- J2（写 S5.1）：证据 S4.1（六点四十马路牙子、相机搁膝盖）→ 4 < 5。
- J3（写 S7.1，因果）：证据 S4.2（周五深夜点赞破万）、S6.1（第五天破十万）→ 4、6 < 7。
- J4（写 S9.2）：证据 S8.1（塔模、涂层、活底模）→ 8 < 9。
- J5（写 S10.1）：证据 S9.1/S9.2/S9.3（面团怕冷、哼调）→ 9 < 10。
- J6（写 S11.1）：证据 S10.2/S10.3（笔记本常客记录）→ 10 < 11。
- J7（写 S12.3）：证据 S12.2（季晴“你火了”、对话框“七点”）→ s=12 == 当前节，证据小节 2 < 3，符合同节先置。
- J8（写 S15.1，因果）：证据 S14.1（匿名帖标题、季晴截图）→ 14 < 15。
- J9（写 S4.2）：证据 S1.1（周六才营业）、S3.3（周六下午书店、四十七张照片）→ 1、3 < 4。
- J10（写 S6.2）：证据 S3.3（4:07 灯灭/4:28 开窗）、S4.1（6:40 到店外）→ 3、4 < 6。
- J11（写 S7.2）：证据 S4.2/S6.1/S6.2（限购纸板与标语）→ 4、6 < 7。
- J12（写 S8.3）：证据 S1.1（门缝橘光）、S3.2（能拍吗、不开闪光灯）→ 1、3 < 8。
- J13（写 S10.3）：证据 S5.2（老爷爷孙女九岁）、S7.3（建筑设计等不了五天）→ 5、7 < 10。
- J14（写 S17.1）：证据 S2.1（下周日前交接完毕）、S11.3（父亲来电停机）→ 2、11 < 17。

## 5. 证据措辞取舍

- 全部 span 为原文片段（8–80 字符、无换行），逐字校验 `row["text"][start:end] == phrase`，`excerpt` 为 ±24 窗口。
- 为与第三批隔离，全部证据短语重新选句：第三批已用辞职信原文、粉笔字、店员邀请、删图文、爆文标题、裸辞消息等，本批改选富士相机/台阶、马路牙子、破万/破十万、塔模/钢丝球、面团怕冷/五个音、笔记本常客、你火了/七点、匿名帖标题、周六才营业/四十七张、4:07/4:28、限购纸板、能拍吗/橘光、建筑设计等不了五天、交接完毕/父亲停机；自检与 runner preflight 均验证与 holdout3 查询/短语零重叠。
- WR 条目按“所选事实短语在全部 prior-context chunk 中的命中”穷尽 `gold_anchor_hashes`（如 J11 的限购告示组合短语覆盖 S4.2/S6.1/S6.2 全部 9 个命中）。
- 每条查询为独立撰写的自包含写作要求（含人物名、事件词、场景词、时间词），未直接抄 must_recall_facts 或原文答案句。

## 6. 难点说明

1. 语料重复实例导致大量近义变体 chunk：每条事实需选择足够特异的组合短语（如“每人限购两个。面包是给人吃的，不是给人拍的”），避免把仅含单句的无关 chunk 卷进证据集。
2. 与第三批共用同一语料，隔离只能靠“证据短语与查询字符串零重叠”保证；本批有意选择不同事件/物件/时间点，并在自检中硬校验。
3. J7 使用同节先置（S12.2 → S12.3），验证 runner 对“s == current 且小节先前置”路径的判定；其余全部为严格章节先置。
4. 盲测纪律：作者只读取语料快照与本目录文件；dev/第一 holdout/holdout2/holdout3 的交付内容不作为作者参考（重叠校验由自检与 runner preflight 执行）。

## 7. 交付与自检

- fixture：`wr4_metadata_holdout_gold_v1.json`
- seal：`wr4_metadata_holdout_gold_v1.seal.json`
- `fixture_sha256`（交付文件原始字节 SHA-256）：`18f0ed1ad1c72a9b922b99061e3aebe756c5015dcbfb6d471cafc92918cb8a78`
- 自检：`holdout_self_check.py` 全部通过（schema / 语料哈希 / span 逐字与 excerpt / prior-context / 写作点覆盖 / query_intent 规范列表 / 与 holdout3 零重叠 / seal 字节哈希一致）。
