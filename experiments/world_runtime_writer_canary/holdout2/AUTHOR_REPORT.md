# Procedural Blind Holdout Author — Batch 2

- author: procedural-author-v1
- seed: 20260807
- corpus: 20f02dc7-dc64-4233-bd6c-06a6d8647dbe (378 chunks, 18 sections)
- blindness: generator reads only the new corpus snapshot; pure standard library; no LLM, no Chroma, no dev gold, no variant code.

## 生成规则

- 14 条 = continuity_fact 8 + wr_key_evidence 6；写作点由种子随机采样，证据全部来自 prior-context（section < 当前或同 section 更早 subsection）。
- 事实 = 证据 chunk 中命中所属主题词表的一句话（8–90 字），逐字 span 绑定；查询为模板文本（只含写作点与主题提示，不含答案句）。
- WR 键 6 个语义：open_days/weekday、clock、operation_state、access/light、knowledge、employment/status；knowledge 与 employment 标记因果检索。
- 局限：金标为机械生成，事实是原文句子而非人工转述；查询为模板风格，不代表真实 Writer 输入的多样性。

## 条目

| query | tier | cur | gold sections | facts | causal |
|---|---|---|---:|---:|---|
| H1 | continuity_fact | 4.2 | 2 | 3 | False |
| H10 | wr_key_evidence | 8.2 | 3,6 | 2 | False |
| H11 | wr_key_evidence | 13.3 | 2,3 | 2 | False |
| H12 | wr_key_evidence | 6.2 | 5,6 | 2 | False |
| H13 | wr_key_evidence | 16.1 | 7,9 | 2 | True |
| H14 | wr_key_evidence | 9.2 | 3,8 | 2 | True |
| H2 | continuity_fact | 9.1 | 3,4,8 | 3 | False |
| H3 | continuity_fact | 16.3 | 1,13 | 2 | False |
| H4 | continuity_fact | 11.1 | 3,4,10 | 3 | False |
| H5 | continuity_fact | 17.1 | 5,15 | 2 | False |
| H6 | continuity_fact | 15.1 | 3,9 | 2 | False |
| H7 | continuity_fact | 10.1 | 4,5 | 2 | False |
| H8 | continuity_fact | 14.3 | 4,5,12 | 3 | True |
| H9 | wr_key_evidence | 5.2 | 3,4 | 2 | False |

seal fixture_sha256: c3466783a742bb7d37797d28c395dcc6f2dfa36a47dd8e1a75fee28185854509（fixture 文件原始字节）