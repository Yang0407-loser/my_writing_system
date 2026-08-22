# 独立 holdout 金标作者（第三批·人工作者）任务契约

你是“独立 holdout 金标作者（第三批·人工作者）”，为长篇写作系统离线检索评测创建密封（sealed）holdout 金标。产出将用于盲测一个 metadata 增强检索变体；你必须在完全不知晓调参实现、开发金标、前两批 holdout（含程序化金标）的条件下独立完成。这是正式任务，请直接开工，不要询问确认。

## 工作目录

工作目录：`E:\writer\my_writing_system`。所有新文件必须创建在 `E:\writer\my_writing_system\experiments\world_runtime_writer_canary\holdout3\` 下（目录不存在则创建）。不得修改任何已有文件，不得读取或覆盖 `holdout2\` 下的任何文件。

## 盲测纪律（硬性约束，禁止读取以下任何文件/目录）

- `experiments\world_runtime_writer_canary\fixtures\wr4_gold_retrieval_v1.json`
- `experiments\world_runtime_writer_canary\fixtures\wr4_gold_retrieval_v1.freeze_manifest.json`
- `experiments\world_runtime_writer_canary\fixtures\wr4_gold_retrieval_holdout_v1.json`
- `experiments\world_runtime_writer_canary\fixtures\wr4_gold_retrieval_holdout_v1.freeze_manifest.json`
- `experiments\world_runtime_writer_canary\fixtures\gold_retrieval_corpus_snapshot_v1.json`
- `experiments\world_runtime_writer_canary\holdout2\`（整个目录，含程序化金标）
- `experiments\world_runtime_writer_canary\gold_retrieval_build_v1.py`、`gold_retrieval_baseline_v1.py`、`gold_retrieval_tune_v1.py`、`wr4_tuning_components.py`、`wr4_metadata_benchmark.py`、`wr4_sealed_holdout_v1.py`、`wr4_holdout_spec_v1.py`、`wr4_metadata_holdout_v1.py`
- `reports\` 下任何 `wr4-*` 或 `world-runtime-wr4-*` 文件
- `tests\unit\test_wr4_gold_retrieval*.py`
- `.world_runtime_wr4_*` 目录（全部运行时目录）

同样禁止读取上述路径的目录列表来推断内容。只允许读取：

- 新语料快照 `E:\writer\my_writing_system\experiments\world_runtime_writer_canary\fixtures\wr4_metadata_holdout_corpus_snapshot_v1.json`（唯一语料来源）
- 你创建的文件
- 本任务契约文件

## 离线与生产约束

- 零 LLM 调用、零网络、零 Chroma 写入、生产 off；只允许读取快照 JSON 与用标准库/项目 `.venv` 做纯数据计算；不得导入 app 生产模块生成金标。
- 运行 python 用 `.\\.venv\\Scripts\\python.exe`（沙箱拒绝时用 require_escalated）。

## 语料

- task_id: `20f02dc7-dc64-4233-bd6c-06a6d8647dbe`（378 个唯一 chunk，18 个 section，约 18 万字符）
- 这是《周六面包店与凌晨三点半》的完整分块实例；chunk 元数据含 `section/subsection/title/topic/content_hash/text`；section 1..18，每节 3 小节（section 1 仅 1 小节），标题如 凌晨驳回/周六蹲守/暖黄初现/微咸余味/破晓等候/暗涌初现 等，具体以快照为准。
- `corpus_hash` 直接使用快照中 `tasks[task_id].corpus_hash`（`05420ab03eb3ec11ac42f07b334ca434ba6a79b9250cbec505f2857b99317878`）；快照文件 sha256 为 `6e52a0f738fb89f0b43fdc8207b63abd4e052f9694b4747c560b7b3b8b8a309a`（自行核对）。

## 金标条目要求

- 共 12–16 条（建议 14 条），`query_index` 用 H1..Hn。
- 两类 tier：
  1. `"continuity_fact"`：连续性事实类，覆盖意图 event/character/scene/foreshadowing 的组合；
  2. `"wr_key_evidence"`：WR-only 键类，覆盖至少 5 个不同键语义，从池中选：营业日/open_days、星期/weekday、开门或开工时间/clock、店面营业状态/operation_state、操作间 access/light、角色知情/knowledge、角色位置/location、职业或离职/employment/resignation、林晚职业状态/employment:lin-wan/status。`wr_keys` 用 `["entity_group","subject","predicate"]` 三元组（可含空串）。
- 至少 2 条 `requires_causal_retrieval=true`。
- prior-context 契约（硬约束）：每条 query 设定写作点 `(section, subsection)`；`gold_sections` 中每个 `s` 必须满足 `s < current_section`，或 `s == current_section` 且证据 subsections 均 `< current_subsection`。证据不得位于当前小节自身或未来内容。
- 写作点应覆盖不同阶段（建议至少 3 个不同 section 区间：前段 2–6、中段 7–13、后段 14–18），不要全部集中在早期。
- 每条字段：
  - `gold_sections`：证据章节（int 列表，全部满足 prior-context）；
  - `gold_chunk_keys`（可选）：`"S<section>:<title>"`；
  - `gold_anchor_hashes`：证据短语所在 chunk 的 `content_hash`（wr_key_evidence 必须穷尽该事实证据 chunk）；
  - `gold_chunk_hashes`：gold_sections 内全部 chunk 的 `content_hash`；
  - `must_recall_facts`：2–3 条**人工转述**事实（一句话，描述连续性所需信息，不要整句抄原文）；
  - `fact_evidence`：`{fact: [span]}`，`span={"phrase","chunk_hash","start","end","excerpt"}`，`phrase` 必须是语料中的**原文片段**（可含标点，长度 8–80），`row["text"][start:end]==phrase` 逐字相等，`excerpt=text[max(0,start-24):end+24]`；每条事实至少 1 个 span，建议 1–3 个；
  - `query`：自然写作要求文本（模拟真实 Writer 输入：本节要写什么、需要什么上下文），**不得直接抄 `must_recall_facts` 或原文答案句**，可含人物名与主题词；
  - `query_intent`、`section`、`subsection` 为写作点。
- 查询措辞必须由你独立撰写，风格自然多样，不得使用模板句；不得与任何已知金标相同（你也看不到它们）。

## 交付文件（全部新建在 holdout3\ 目录）

1. `wr4_metadata_holdout_gold_v1.json`：

```json
{
  "schema_version": "wr4-metadata-holdout-gold-v1",
  "author": "independent-holdout-author-human-batch3",
  "created_at": "2026-08-07",
  "k": 5,
  "corpus": {
    "task_id": "20f02dc7-dc64-4233-bd6c-06a6d8647dbe",
    "snapshot_file": "wr4_metadata_holdout_corpus_snapshot_v1.json",
    "snapshot_sha256": "6e52a0f738fb89f0b43fdc8207b63abd4e052f9694b4747c560b7b3b8b8a309a",
    "corpus_hash": "05420ab03eb3ec11ac42f07b334ca434ba6a79b9250cbec505f2857b99317878",
    "chunk_count": 378
  },
  "character_names": ["林晚", "周野", "季晴", "顾衍", "吴阿姨"],
  "entries": [ ... ]
}
```

2. `wr4_metadata_holdout_gold_v1.seal.json`：

```json
{
  "schema_version": "wr4-metadata-holdout-seal-v1",
  "fixture": "wr4_metadata_holdout_gold_v1.json",
  "fixture_sha256": "<hex>",
  "corpus_task_id": "20f02dc7-dc64-4233-bd6c-06a6d8647dbe",
  "corpus_hash": "05420ab03eb3ec11ac42f07b334ca434ba6a79b9250cbec505f2857b99317878",
  "corpus_snapshot_sha256": "6e52a0f738fb89f0b43fdc8207b63abd4e052f9694b4747c560b7b3b8b8a309a",
  "entry_count": 14,
  "tier_counts": { "continuity_fact": 8, "wr_key_evidence": 6 },
  "llm_calls": 0,
  "production_authorized": false,
  "author": "independent-holdout-author-human-batch3",
  "sealed_at": "2026-08-07"
}
```

注意：`fixture_sha256` 必须等于**你交付的 fixture 文件原始字节**的 SHA-256（读取 bytes 计算，不要用重新序列化后的文本）。

3. `AUTHOR_REPORT.md` — 语料、条目构成、写作点分布、每条 prior-context 判定依据、证据措辞取舍、难点说明。
4. `holdout_self_check.py` — 自写校验脚本（schema、gold hash 存在、span 逐字、prior-context、写作点覆盖、fixture 与 seal 字节哈希一致），交付前运行全部通过。

## 收尾

- 交付前必须运行 `holdout_self_check.py` 通过。
- 最终回复给出：文件绝对路径、条目数与 tier 分布、写作点 section 分布、seal fixture_sha256（字节哈希）、self-check 结果、主要判断（尤其 prior-context 修正与证据措辞取舍）。
