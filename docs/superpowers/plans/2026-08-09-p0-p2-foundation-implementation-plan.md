# P0 + P1/P2 Foundation 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not dispatch subagents unless the user explicitly authorizes delegation.

> 日期：2026-08-09  
> 状态：待执行  
> 上位计划：`docs/superpowers/plans/2026-08-09-narrative-os-server-to-client-master-plan.md`

**Goal:** 在不启动 World Runtime authoritative、Story Runtime、正式客户端或 P3B 安全平台建设的前提下，把一个真实小节迁入 `Generate → Candidate → Validate → State Transition → Canonical Commit → Revision/State Heads → Outbox → Critical Projection Barrier` 的唯一写入链路，并证明重复重试、进程终止、Redis/Chroma 故障不会造成正文、世界状态与生成上下文分叉。

**Architecture:** 保留现有 Coordinator/Writer 外部行为，在内部以 Canonical Store + Commit Runtime 建立绞杀者接缝。Writer 的新路径只产生不可变 Candidate，纯 `StateTransition` 把 base state 与 mutation 编译为完整 next-state snapshot；同一 SQL 事务检查并移动 Subsection Revision Head 与 Project State Head，同时原子写入 Revision、State、Ledger、Idempotency 与 Outbox。事务成功后先追平 critical projections，只有 Projection Barrier ready 才能生成下一小节；完整 Projection/Rebuild 平台仍留到 P3A。

**Tech Stack:** Python 3.11、Pydantic v2、SQLAlchemy 2.0、Alembic、PostgreSQL + Psycopg 3、SQLite 单元测试适配、pytest、Celery、Redis、Chroma、uv 锁文件。

## Global Constraints

- Candidate 和 StateTransition 必须是确定性、不可变、无外部副作用的值对象。
- Commit Runtime 只原子持久化准备好的 Canon 对象，不解释小说 ontology，不访问 Redis、Chroma、文件系统、LLM 或 Legacy API。
- 每次 commit 同时执行 Revision Head OCC 与 Project State Head OCC；任一 head 过期即拒绝。
- P2 的最强运行模式命名为 `internal_required`，只表示内部新任务必须经过 Canon，不表示对外 production-ready。
- 每个任务执行 Red → Green → Refactor → Verify；Postgres Gate 被 skip 视为未完成。

---

## 0. 冻结的范围与决策

### 本计划包含

- P0：工作区保护、测试归零、环境隔离、密钥检查点治理、数据所有权矩阵、外部 contract snapshot、Golden Slice 冻结。
- P1：最小 Canonical Schema v0、版本化迁移、本地 SQLite 适配、生产 Postgres 事务验证、Canonical Repository、可移植快照恢复。
- P2：`SubsectionCandidate`、Legacy Candidate Adapter、`StateTransitionResult`、双 Head OCC、Atomic Commit Service、数据库冲突式幂等、Transactional Outbox、Critical Projection Barrier、Writer 新路径、Coordinator 接线、最小 Legacy Projection、故障注入与恢复演练。

### 本计划明确不包含

- 不把 Character、Relationship、Item、Location、Timeline、Arc、Foreshadow、KnowledgeState 正规化成 Canon 表。
- 不让当前 World Runtime 获得 production authority，不开启 WR4 retrieval。
- 不实现完整 Outbox worker 集群、租约/分片、所有投影的全量 rebuild；这些属于 P3A。
- 不实现租户成员关系、正式鉴权、凭据托管、分布式限流、备份平台；这些属于 P3B。
- 不建设 Story Runtime、Context Broker 切换、Structured Reviewer、Narrative Editing API 或客户端。

### 冻结的 Foundation 数据链

```mermaid
flowchart LR
    A["Coordinator：一次小节工作"] --> B["Writer.generate_candidate"]
    B --> C["SubsectionCandidate：含两个 base head"]
    C --> D["Candidate Validator"]
    D --> E["Pure StateTransition Compiler"]
    E --> F["PreparedCanonicalCommit"]
    F --> Q["CanonicalCommitService"]
    Q --> R["锁定 Revision Head + State Head"]
    R --> G["同一 SQL 事务"]
    G --> H["DocumentRevision"]
    G --> I["CanonicalStateVersion"]
    G --> J["EventLedger"]
    G --> K["IdempotencyRecord"]
    G --> L["Critical + Non-blocking Outbox Events"]
    L --> M["事务后 MinimalProjectionDispatcher"]
    M --> N["Critical: Legacy World/Event/Handover/Context/Chroma"]
    N --> O{"Projection Barrier ready?"}
    O -->|yes| P["允许下一 subsection"]
    O -->|no| S["暂停并从 commit_id 恢复投影"]
    M --> T["Non-blocking: Stream/Preview/Markdown/Analytics"]
```

### Foundation 不变量

1. Candidate validation 完成前不能 commit。
2. Canon commit 事务不得调用 Redis、Chroma、文件系统、LLM 或 Legacy World/Event API。
3. Commit 必须同时校验 `Subsection.current_revision_id/revision_number` 与 `Project.current_state_version_id`；任一 base 过期分别产生 `RevisionConflict` 或 `StateVersionConflict`。
4. 一个 commit 可产生一条 subsection revision；未来允许扩展为多 revision，但 P2 不提前设计批量编辑。
5. `DocumentRevision` 保存该 versioned unit（当前为 subsection）的完整正文快照，不依赖 patch 回放。
6. 当前作品由 `Subsection.current_revision_id` 指向的快照按稳定 ordinal 物化；因此仅凭 Canonical DB 可恢复全文。
7. `CanonicalStateVersion` 是版本化 JSON blob；Project 用显式 State Head 指向当前版本，禁止通过时间或最后 commit 推断当前态。
8. `StateTransition` 在 SQL 事务前以纯函数生成完整 next-state snapshot；Commit Service 只验证和持久化，不解释 narrative ontology。
9. 相同 `idempotency_key + candidate_hash` 永远返回同一个结果；并发首次请求由数据库 unique reservation 冲突协议裁决，相同 key 携带不同 candidate hash 必须显式冲突。
10. Outbox 与 Canon 同事务；投影失败只改变投影状态，不回滚或改写 Canon。
11. 下一小节生成前，当前 commit 的全部 critical projections 必须 published；非关键投影不得阻塞继续写作。
12. Legacy 路径是显式回退，不得在同一 canary subsection 上与 Canonical 路径同时拥有写权威。
13. checkpoint、测试 fixture、日志与 contract snapshot 不得持久化明文 API key。

## 1. 固定契约

### 1.1 `SubsectionCandidate v0`

放在 `app/canonical/contracts.py`，使用 `extra="forbid"`、`frozen=True`：

```python
class SubsectionCandidate(FrozenArtifact):
    schema_version: Literal["subsection-candidate-v0"]
    tenant_id: str
    project_id: str
    document_id: str
    subsection_id: str
    task_id: str
    section: int
    subsection: int
    ordinal: int
    title: str
    topic: str
    base_revision_number: int
    base_state_version_id: str
    draft: str
    draft_hash: str
    prompt_hash: str
    validation: CandidateValidation
    handover_candidate: dict[str, Any] | None
    world_mutations: tuple[WorldMutationCandidate, ...]
    events: tuple[CanonicalEventCandidate, ...]
    state_frame: dict[str, Any] | None
    generation_metadata: dict[str, Any]
    candidate_hash: str
```

要求：hash 使用 UTF-8、排序键、紧凑分隔符的 canonical JSON；`draft_hash`、`candidate_hash` 必须由模型校验器重算；不接受调用方自报但无法复算的 hash；`validation.complete` 必须为 `True`；`base_state_version_id` 必须来自生成该 Candidate 时实际读取的 Project State Head，不能在 commit 前临时补当前值。

### 1.2 `StateTransitionResult v0`

`StateTransitionCompiler` 是事务外的纯函数边界：

```python
class CanonicalStateSnapshot(FrozenArtifact):
    version_id: str
    project_id: str
    schema_version: str
    state_json: dict[str, Any]
    state_hash: str

class StateTransitionResult(FrozenArtifact):
    schema_version: Literal["state-transition-result-v0"]
    transition_version: str
    candidate_hash: str
    base_state_version_id: str
    next_state_json: dict[str, Any]
    state_hash: str
    ledger_events: tuple[CanonicalEventCandidate, ...]

class PreparedCanonicalCommit(FrozenArtifact):
    candidate: SubsectionCandidate
    state_transition: StateTransitionResult

class StateTransitionCompiler(Protocol):
    def compile(
        self,
        *,
        base_state: CanonicalStateSnapshot,
        candidate: SubsectionCandidate,
    ) -> StateTransitionResult: ...
```

编译器必须验证 `base_state.version_id == candidate.base_state_version_id`，生成完整 next-state snapshot，并把所有可入 Ledger 的事件作为值返回。P2 的 `LegacyStateTransitionAdapter` 只把 generic mutations/events 确定性追加到 opaque JSON snapshot，不理解 inventory、关系或人物 ontology；P4 可以替换 compiler，但不能改变 `PreparedCanonicalCommit → CanonicalCommitService` 的接口。

### 1.3 `CanonicalCommitResult v0`

```python
class CanonicalCommitResult(FrozenArtifact):
    schema_version: Literal["canonical-commit-result-v0"]
    commit_id: str
    revision_id: str
    revision_number: int
    state_version_id: str
    content_hash: str
    outbox_event_ids: tuple[str, ...]
    idempotency_key: str
    candidate_hash: str
    skipped_as_duplicate: bool
```

### 1.4 稳定幂等键与并发 reservation

新生成小节固定为：

```text
subsection-commit:v0:{tenant_id}:{project_id}:{document_id}:{subsection_id}:{base_revision_number}:{generation_attempt_id}
```

- Celery retry 必须复用 `generation_attempt_id`，不得每次 retry 新建 UUID。
- Celery retry 进入 LLM 前必须先用稳定 key 查询已完成 `IdempotencyRecord`；已完成则直接恢复原 `CanonicalCommitResult` 并进入 Projection Barrier，只有不存在时才允许重新生成。
- 同 key + 同 candidate hash：返回第一次结果，`skipped_as_duplicate=True`。
- 同 key + 不同 candidate hash：抛 `IdempotencyConflict`，HTTP/任务层映射为显式冲突，不静默选择任一版本。
- “先 SELECT 再 INSERT”不是并发协议。实现必须在同一事务内先尝试插入 `status=reserved` 的唯一记录；以 `UNIQUE(tenant_id, project_id, idempotency_key)` 为最终裁判。冲突请求等待首事务完成后读取 record、比较 hash，并返回原结果或冲突；首事务回滚时 reservation 一并消失。

### 1.5 Schema v0 表

| 表 | 关键字段与约束 | 所有者/性质 |
|---|---|---|
| `canonical_projects` | `id`, `tenant_id`, `owner_id`, `name`, nullable-at-bootstrap `current_state_version_id`, timestamps | Canon + Project State Head |
| `canonical_documents` | `id`, `tenant_id`, `project_id`, `title`, timestamps | Canon |
| `canonical_subsections` | `id`, scope, `document_id`, `ordinal`, legacy section/subsection refs, nullable `current_revision_id`; unique document + ordinal | Canon |
| `document_revisions` | scope, `commit_id`, `subsection_id`, `revision_number`, `parent_revision_id`, full `content`, `content_hash`, status, creator, metadata; unique subsection + revision number | Canon immutable snapshot |
| `canonical_state_versions` | scope, `project_id`, nullable-for-genesis `commit_id`, `origin=genesis/commit`, `parent_state_version_id`, `transition_version`, `schema_version`, full `state_json`, `state_hash`; unique project + id; CHECK genesis/commit shape | Canon versioned blob |
| `canonical_commits` | scope, `id`, `candidate_hash`, `base_revision_number`, `base_state_version_id`, `status=committed`, timestamps | Canon transaction envelope |
| `event_ledger` | scope, `commit_id`, `event_type`, `payload_json`, evidence refs, `ordinal`; unique commit + ordinal | Canon append-only ledger |
| `idempotency_records` | scope, `idempotency_key`, `candidate_hash`, `status=reserved/completed`, nullable-until-complete `commit_id/result_json`; unique tenant + project + key | Commit concurrency control |
| `outbox_events` | scope, `id`, `commit_id`, `projection_name`, `barrier_kind=critical/non_blocking`, event type, payload, status, attempts, available/published times, last error; unique commit + projection name | Durable projection queue + Barrier source |

所有外键和唯一约束在 SQLite 与 Postgres 都必须生效。UUID 在 Python 侧生成并以 36 字符字符串持久化，避免首版引入方言分支；时间统一为 UTC aware datetime。

`canonical_projects.current_state_version_id` 与 `canonical_subsections.current_revision_id` 都是明确权威 Head。Project 创建事务必须生成 `origin=genesis, commit_id=NULL, parent_state_version_id=NULL` 的 genesis state version 并在返回前移动 State Head；普通 commit 产生的 state version 必须为 `origin=commit` 且带非空 `commit_id/parent_state_version_id`。任何对外可读 Project 不得处于无 Head 状态。普通 `revision_id` FK 不足以防止跨 subsection 指针，必须通过 composite FK 或等价数据库约束确保 `(subsection.id, current_revision_id)` 只引用 `(document_revisions.subsection_id, document_revisions.id)`；Project State Head 同理只能指向本 Project 的 state version。

### 1.6 Projection Barrier v0

P2 每个 commit 生成固定 projection manifest，并为每个 projector 创建独立 OutboxEvent：

| 分类 | P2 projector | 继续下一小节前的要求 |
|---|---|---|
| critical | `legacy_world_event` | published |
| critical | `handover_context` | published |
| critical | `chroma_story_chunks` | published |
| non_blocking | `redis_stream` | 不阻塞 |
| non_blocking | `task_preview` | 不阻塞 |
| non_blocking | `markdown_export` | 不阻塞 |
| non_blocking | `analytics` | 不阻塞 |

`ProjectionBarrier.ensure_ready(commit_id: str) -> Literal["ready", "pending", "failed"]` 只有在该 commit 的全部 critical events 为 published 时返回 `ready`。`pending/failed` 时 Coordinator 进入 `awaiting_critical_projection`，保存 `last_commit_id` 并暂停；Celery retry 先通过 `IdempotencyRepository.get_completed(idempotency_key) -> CanonicalCommitResult | None` 恢复原 commit result、补偿 critical projection，不能重新调用 LLM。P3A 可以细化 projector/lease/rebuild，但不得放松这个 read-after-write 条件，直到 Writer 的对应读取已切到 Canon。

### 1.7 迁移模式

```text
legacy   旧任务和紧急回退专用；现有 StateCommitter 保持旧行为
canary   allowlist task/subsection 走 Canonical Commit，并把 Legacy 写入改为 post-commit projection
internal_required 内部 dogfood 的所有新任务走 Canonical Commit；不满足 Canon/critical barrier 前置条件时 fail closed
```

配置项：

- `CANONICAL_DATABASE_URL`
- `CANONICAL_COMMIT_MODE=legacy|canary|internal_required`
- `CANONICAL_CANARY_TASK_IDS`
- `CANONICAL_CANARY_SUBSECTIONS`（`task_id:section:subsection` 逗号分隔）

P2 Gate 前默认 `legacy`；Gate 通过后只允许内部 dogfood 的“新建任务”切到 `internal_required`，带 pre-Foundation checkpoint version 的恢复任务继续显式走 `legacy`。`internal_required` 只说明 Canonical Commit 是内部新任务的必经链路，不表示 Outbox/Projection、P3B 安全与外部用户开放已 production-ready。

---

## 2. P0：可信基线

### Task 0：保护当前工作区并建立隔离执行面（人工确认 Gate）

**文件：**

- Review: 当前 `git status --short`
- Preserve: `docs/superpowers/plans/2026-08-09-narrative-os-server-to-client-master-plan.md`
- Preserve: 本实施计划
- Create after approval: isolated worktree `E:\writer\my_writing_system-foundation`

- [x] **Step 1：只读生成变更清单**

运行：

```powershell
git status --short
git diff --name-status
git diff --check
rg -l -i "(api[_-]?key|authorization|bearer|secret).{0,20}[:=]" experiments tests reports .env* 2>$null
```

执行记录（2026-08-09）：当前 normal checkout 位于 `main`，检测到 32 个 tracked changed status entries、551 个 untracked entries，`git diff --check` 为 0；密钥扫描只记录文件名与脱敏属性，未输出值。

- [x] **Step 2：按四类人工确认纳入范围**

- A：当前生产源码/测试，纳入 baseline snapshot。
- B：可晋级实验源码/fixture/report，逐文件审核后纳入。
- C：运行态目录、数据库、缓存、reviewer worktree，不纳入 Git，但不删除。
- D：含密钥或来源不明 fixture，先隔离/脱敏，不纳入。

禁止执行 `git add -A`、`git clean`、`git reset --hard` 或递归删除。

执行分类（用户已于 2026-08-09 批准）：A 为当前 tracked 生产源码/测试/文档与两份 Foundation 计划；B 为经扫描后纳入的 untracked `app/`、`tests/`、`docs/`、`experiments/`；C 为 `.world_runtime_*`、reviewer/cowork runtime、`outputs/` 与未复核报告；D 至少包含被忽略但含非空凭据的 `.env`、未忽略且含非空 `api_key` 的 `experiments/scene_reality_contract_v0/fixtures/baseline_task_state.json`，以及秘密扫描命中的待审文件。C/D 均原地保留，未删除、未纳入 baseline commit。

- [x] **Step 3：经用户确认后建立保护分支与 checkpoint commit**

只对批准路径执行显式 `git add <paths...>`，创建 `chore: freeze pre-foundation baseline` 提交。若用户不批准提交，停止实施；不要在当前脏工作区继续 Foundation 代码改造。

执行记录（2026-08-09）：创建保护分支 `foundation/baseline-2026-08-09` 与提交 `903c14a chore: freeze pre-foundation baseline`，冻结 549 个批准文件。暂存审计结果为高置信秘密命中 0、超过 5 MiB 文件 0、未批准新增根目录 0；两份未复核 `reports/` 变更和三个敏感待审文件均未纳入。`git diff --cached --check` 记录到既有尾随空格/文件末空行，作为基线卫生债保留，未在 checkpoint 中批量改写历史实验材料。

- [x] **Step 4：创建隔离 worktree**

```powershell
git worktree add E:\writer\my_writing_system-foundation -b feat/p0-p2-foundation
git -C E:\writer\my_writing_system-foundation status --short
```

预期：新 worktree 干净，后续所有命令在该目录执行。

执行记录（2026-08-09）：已创建分支 `feat/p0-p2-foundation` 与隔离工作树 `E:\writer\my_writing_system-foundation`，起点为 `903c14a`。原工作区中未纳入 checkpoint 的用户资产保持原位。

新鲜基线验证（2026-08-09）：完整 unit 首先因已排除的敏感模块 `experiments/world_runtime_writer_canary/wr310_reviewer_real_task_side_by_side.py` 仍被测试直接导入而产生 1 个 collection error；显式忽略该单测后，其余结果为 `1305 passed / 156 failed / 15 skipped / 2 warnings`。失败主要集中在无凭据实验、缺失敏感 fixture 与历史 hash/provenance/产物耦合。integration 在约 10 分钟内无任何进度摘要、进程处于低 CPU 外部等待，按有界基线策略终止并记录为 hang/timeout，不能记为通过。pytest 生成的缓存和单个未跟踪报告已清理，未修改源码。以上结果是 Task 1 的真实 Red baseline，不把旧工作区参考数冒充新隔离基线。

### Task 1：隔离测试环境并复现已知失败

**文件：**

- Modify: `app/config.py`
- Modify: `tests/conftest.py`
- Create: `tests/unit/test_test_environment_isolation.py`
- Create: `reports/foundation/p0-test-baseline.md`

- [x] **Step 1：写失败测试**

用 subprocess 在临时 cwd 放置带非默认 `WRITER_HANDOVER_CONTRACT_VERSION` 的 `.env`，同时设置 `WRITER_TESTING=1`；断言导入 `app.config` 后仍取测试明确设置的值或代码默认值，而不是临时 `.env`。

- [x] **Step 2：证明测试先失败**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_test_environment_isolation.py -q
```

预期：当前无条件 `load_dotenv()` 导致失败。

- [x] **Step 3：最小实现**

- `tests/conftest.py` 必须在任何 `app.*` import 前设置 `WRITER_TESTING=1`。
- 测试显式设置 Handover v1、World Runtime off、WR4 off、Canonical legacy 和临时 SQLite 路径。
- `app/config.py` 在 `WRITER_TESTING=1` 时不调用 `load_dotenv()`；生产启动行为不变。

- [x] **Step 4：运行单元与集成基线**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit -q
& .\.venv\Scripts\python.exe -m pytest tests\integration -q
```

将结果写入 `reports/foundation/p0-test-baseline.md`。当前审计参考是 `1452 passed / 28 failed` 与 `54 passed / 1 failed`；执行时以新鲜输出为准。

执行记录（2026-08-09）：dotenv 隔离与全局 SQLite 测试资源清理均完成 Red→Green，定向隔离测试 `3 passed`。完整 unit 仍被 1 个敏感依赖 collection error 阻断；忽略且仅忽略该节点后为 `1308 passed / 156 failed / 15 skipped / 2 warnings`。完整 integration 在 Hugging Face 模型缓存可用且强制 offline 后为 `55 passed / 6 warnings`。详细证据、失败分类和 Gate 解释见 `reports/foundation/p0-test-baseline.md`；Task 1 完成不表示 P0 Gate 通过。

### Task 2：处理 29 个已解释失败，不用“更新期望值”掩盖回归

**文件：**

- Modify: `experiments/scene_reality_contract_v0/prompting.py`
- Modify: `tests/unit/test_writing_pipeline.py`
- Create: `docs/adr/0001-foundation-public-contract-baseline.md`
- Modify: `experiments/writer_boundary_v12_r34/builder.py`
- Modify: `experiments/writer_boundary_v12_r35/builder.py`
- Modify: `experiments/writer_boundary_v12_r36/builder.py`
- Create: `experiments/writer_boundary_v12_shared/fixtures/llm_client_<expected-sha256>.py` only if exact historical bytes can be recovered
- Modify: affected r34/r35/r36 tests and fixture manifests

- [ ] **Step 1：Prompt contract 的 Red/Green**

运行两条当前失败测试，确认缺少 `anti_ai_expression_constraints`；在 prompt fixture values 中补空字符串兼容值，不改 production prompt 语义。

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_scene_reality_contract_v0.py -q
```

- [ ] **Step 2：冻结 Writer 公共签名**

ADR 记录 `rag_metadata_provider` 是已上线的可选只读参数；更新唯一 contract snapshot，禁止借此调整其他参数的名称、顺序和默认值。

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_writing_pipeline.py::test_writer_public_signatures_remain_frozen -q
```

- [ ] **Step 3：修复实验 hash 的错误耦合**

先从实验 provenance 对应 Git commit 恢复 `llm_client.py` 原始字节并验证 hash 等于 manifest 中固定值，再让 r34/r35/r36 builder 指向不可变 snapshot；不得把三份历史 manifest 的 hash 直接改成当前 live `app/utils/llm_client.py` hash。

如果无法恢复匹配字节：把三组实验标记为 `archived_invalid_source_pin`，测试改为明确验证“禁止执行”，不能伪造新 hash 让旧结果显得仍有效。

- [ ] **Step 4：定向与全量验证**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_writer_boundary_v12_r34.py tests\unit\test_writer_boundary_v12_r35.py tests\unit\test_writer_boundary_v12_r36.py -q
& .\.venv\Scripts\python.exe -m pytest tests\unit tests\integration -q
```

预期：零失败；warnings 分类记录但不在本任务顺手升级 jieba/Chroma。

### Task 3：阻断 checkpoint 密钥并修复 Redis stream 生命周期错误

**文件：**

- Create: `app/checkpoint_sanitizer.py`
- Modify: `app/blackboard.py`
- Modify: `app/coordinator.py`
- Modify: `app/routers/tasks.py`
- Create: `tests/unit/test_checkpoint_secret_hygiene.py`
- Modify/Create: `tests/unit/test_task_routes.py`
- Modify: `.gitignore`

- [ ] **Step 1：写失败测试**

- 向 `Blackboard.save_checkpoint()` 传入嵌套的 `api_key`、`llm_api_key`、`authorization`，断言持久化 mapping 中不存在明文值。
- 新任务与 resume checkpoint 均断言没有 `api_key` 字段。
- 删除 task 时断言调用 `bb.stream_delete(task_id)`，而不是删除错误的 `stream:{task_id}` key。

- [ ] **Step 2：运行并确认失败**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_checkpoint_secret_hygiene.py tests\unit\test_task_routes.py -q
```

- [ ] **Step 3：最小实现**

- Coordinator 不再把 API key 放入 state/checkpoint；resume 只使用本次显式传入的 key。旧 checkpoint 即使含 key，也在迁移读取时丢弃且不再回写。
- `Blackboard.save_checkpoint` 做 defense-in-depth 递归清洗；清洗器只删除凭据字段，不模糊删除业务字段。
- 修正 route 使用 `stream_delete`。
- `.gitignore` 只新增已确认的运行态目录模式；不删除任何现有目录。

注意：Celery broker 中的临时凭据传递与正式 credential reference 属于 P3B；P0 的承诺仅是“持久 checkpoint/fixture/log 无明文 key”。

- [ ] **Step 4：验证**

运行定向测试与 `tests/unit tests/integration` 全量。

### Task 4：冻结无密钥 Golden Vertical Slice

**文件：**

- Create: `scripts/foundation/build_golden_slice.py`
- Create: `tests/fixtures/foundation_golden_slice_v1.json`
- Create: `tests/unit/test_foundation_golden_slice.py`
- Modify: `tests/quality/style_baseline_registry.v1.json`

- [ ] **Step 1：写 fixture contract 测试**

fixture 必须包含：

- 固定 `tenant_id/project_id/document_id/subsection_id/task_id` 测试标识。
- 来源 `output/周六面包店与凌晨三点半_20260715_143857.md` 和整书 SHA-256 `0B1E...F96`。
- 从真实作品提取的第 1 个小节正文、正文 hash、稳定 ordinal。
- 最小 outline/style/initial canonical state/handover expected shape。
- `authorization=internal_generated_regression_artifact`。
- `contains_secret=false`，以及脚本扫描结果。

测试要验证 hash、schema、字符非空、来源授权与无常见凭据字段。

- [ ] **Step 2：确认 fixture 尚不存在导致失败**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_foundation_golden_slice.py -q
```

- [ ] **Step 3：实现可重放 builder**

builder 只读既有 golden story，按确定性边界提取同一文本，输出排序键 JSON；不得复制任何 `experiments/**/baseline_task_state.json` 中的运行时凭据。

- [ ] **Step 4：双次构建一致性验证**

连续构建两次并断言 fixture byte-for-byte 相同，然后运行 style baseline 与 Golden fixture 测试。

### Task 5：冻结外部行为与数据所有权

**文件：**

- Create: `docs/architecture/foundation-data-ownership-v0.md`
- Create: `tests/contracts/openapi-pre-foundation-v0.json`
- Create: `tests/contracts/writer-pre-foundation-v0.json`
- Create: `scripts/foundation/snapshot_contracts.py`
- Create: `tests/contract/test_pre_foundation_contracts.py`

- [ ] **Step 1：写 contract 测试**

- 规范化 OpenAPI（排序、移除非语义时间字段）后与 snapshot 比较。
- 冻结 Writer.run 参数顺序/default、task status/history/result 的既有响应字段。
- 允许后续 additive 字段，但删除/改名/类型变化必须失败。

- [ ] **Step 2：生成并审核 snapshot**

snapshot 禁止包含环境值、API key、真实绝对输出路径和任务内容。

- [ ] **Step 3：完成所有权矩阵**

至少逐项记录 Writer 当前读取、写入时点、分类、P2 目标：正文 draft、section_text/full_draft、Handover、WorldState、EventGraph、Chroma、ContextManager、Redis hash/stream/checkpoint、TaskStore、Markdown、StateFrame/observations。

- [ ] **Step 4：P0 Gate**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit tests\integration tests\contract -q
git status --short
git diff --check
```

Gate 条件：零未解释失败、Golden fixture 可重建、checkpoint 无明文 key、contract snapshot 通过、所有权矩阵完成。达不到则不进入 P1。

---

## 3. P1：Canonical Schema v0 与恢复

### Task 6：引入锁定依赖、配置与数据库 ADR

**文件：**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Regenerate: `requirements.txt`
- Modify: `.env.example`
- Modify: `app/config.py`
- Create: `docs/adr/0002-canonical-database-and-transaction-boundary.md`
- Create: `tests/unit/canonical/test_database_config.py`

- [ ] **Step 1：写失败配置测试**

覆盖 SQLite URL、Postgres URL、`legacy|canary|internal_required`、非法 commit mode、canary allowlist 解析和 test default；确保 production 未配置数据库时 fail closed，而 test 可显式用临时 SQLite。

- [ ] **Step 2：添加依赖并锁定**

```powershell
uv add "sqlalchemy>=2.0.51,<2.1" "alembic>=1.18.5,<2" "psycopg[binary]>=3.3.4,<4"
uv lock
uv export --no-dev --no-hashes --locked --output-file requirements.txt
uv sync --locked --extra dev
```

`uv.lock` 是唯一依赖事实；`requirements.txt` 只供 Docker，从 lock 导出，不手工双写。

- [ ] **Step 3：写 ADR**

记录：SQLAlchemy 2 sync session、Postgres 生产权威、SQLite 单元适配、Alembic 唯一 schema 迁移、禁止 runtime CREATE/ALTER、事务不得触碰外部系统、JSON blob 边界、P3B 前不声明完整 tenant security。ADR 的版本依据只引用官方文档：[SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/intro.html)、[Alembic](https://alembic.sqlalchemy.org/en/latest/index.html)、[Psycopg 3 安装](https://www.psycopg.org/psycopg3/docs/basic/install.html) 与 [uv lock/sync](https://docs.astral.sh/uv/concepts/projects/sync/)。

- [ ] **Step 4：验证**

运行 config tests、`uv lock --check`（或当前 uv 等价 locked check）和 Docker build dependency smoke。

### Task 7：实现不可变 Candidate/Commit contracts

**文件：**

- Create: `app/canonical/__init__.py`
- Create: `app/canonical/hashing.py`
- Create: `app/canonical/contracts.py`
- Create: `app/canonical/errors.py`
- Create: `tests/unit/canonical/test_contracts.py`

- [ ] **Step 1：写失败测试**

覆盖 frozen/extra forbid、draft hash 重算、candidate hash 稳定性、字段顺序无关、正文变化导致 hash 变化、`base_state_version_id` 必填且进入 candidate hash、validation incomplete 拒绝、`StateTransitionResult/PreparedCanonicalCommit` hash 与 base/candidate 对齐、序列化 round-trip、commit result duplicate flag；在 `app/canonical/errors.py` 明确定义 `RevisionConflict`、`StateVersionConflict`、`IdempotencyConflict` 与 `ProjectionBarrierPending`，不得复用模糊 `RuntimeError`。

- [ ] **Step 2：实现 canonical JSON/hash 与 Pydantic contracts**

复用概念但不要从 `repr()` 生成权威 hash；实现 `StateTransitionCompiler` protocol、`StateTransitionResult` 和 `PreparedCanonicalCommit`，但本任务不实现 Legacy transition 算法。不要继续扩展现有 `CommitArtifact` 充当 Canonical result；旧 artifact 保持 compatibility，适配发生在边界。

- [ ] **Step 3：验证**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_contracts.py -q
```

### Task 8：建立 SQLAlchemy models 与 Alembic 0001 migration

**文件：**

- Create: `app/canonical/database.py`
- Create: `app/canonical/models.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_canonical_schema_v0.py`
- Create: `tests/unit/canonical/test_schema_v0.py`

- [ ] **Step 1：写 SQLite schema 失败测试**

使用临时文件数据库运行 `alembic upgrade head`，断言九张表、两个明确 Head、FK/composite FK、unique/index、JSON round-trip、foreign_keys pragma 和 downgrade/upgrade 可重复。数据库必须拒绝 Subsection A 把 `current_revision_id` 指向 Subsection B revision，也必须拒绝 Project A 把 `current_state_version_id` 指向 Project B state version。

- [ ] **Step 2：实现 models 与 migration**

- Migration 明确创建/回滚九张表；不调用 `Base.metadata.create_all()` 代替 migration。
- `canonical_subsections.current_revision_id` 必须使用 same-subsection composite FK；`canonical_projects.current_state_version_id` 必须使用 same-project composite FK。为解决两组建表环，先创建 head owner 与 version 表的正向 FK/unique pair，再用 Alembic `batch_alter_table`（SQLite）/`ALTER TABLE`（Postgres）增加反向 composite FK；不得退化为仅靠应用约定的悬空或跨 scope 指针。
- 所有 scoped repository 查询未来必须具备 tenant/project 条件；索引先覆盖当前 Golden reads，不为未来 UI 预建大量索引。

- [ ] **Step 3：验证迁移**

```powershell
$env:CANONICAL_DATABASE_URL='sqlite:///./.foundation_tmp/canonical-migration-test.db'
New-Item -ItemType Directory -Force -Path .foundation_tmp | Out-Null
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -m alembic downgrade base
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_schema_v0.py -q
```

本地命令必须指向专用临时/开发数据库，禁止对现有 `tasks.db` 或其他用户 DB 执行 downgrade。

### Task 9：实现 scoped Repository、全文物化与 portable snapshot

**文件：**

- Create: `app/canonical/repositories.py`
- Create: `app/canonical/snapshot.py`
- Create: `scripts/foundation/export_canonical_snapshot.py`
- Create: `scripts/foundation/import_canonical_snapshot.py`
- Create: `tests/unit/canonical/test_repositories.py`
- Create: `tests/integration/canonical/test_snapshot_restore.py`

- [ ] **Step 1：写失败测试**

- 任何读取缺 tenant/project scope 都无法调用或明确失败。
- 创建 Project 时同事务生成 genesis state version 并移动 Project State Head；Repository 不得返回 headless Project。
- 创建 Document/Subsection，写入多个 subsection 当前 revision 后，按 ordinal 物化全文。
- parent/revision_number 连续且 current pointer 正确。
- 导出 Golden DB → 新空 DB 导入 → 全文 hash、Project State Head/state hash、ledger hash 对账。
- Redis/Chroma/output 目录不存在时恢复仍成功。

- [ ] **Step 2：实现 Repository 与 snapshot format v0**

Repository 不自行 commit session；写入只接受调用方 transaction/session。Snapshot 是 Foundation 恢复证据，不替代 P3B 的 Postgres backup/restore 平台。

- [ ] **Step 3：验证**

运行 unit/integration 定向测试，两次导出需 deterministic。

### Task 10：用真实 Postgres 验证 Schema v0 事务语义

**文件：**

- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Create: `tests/integration/canonical/conftest.py`
- Create: `tests/integration/canonical/test_postgres_schema_v0.py`
- Create: `scripts/foundation/reset_test_database.ps1`（仅允许明确的 test DB）

- [ ] **Step 1：先写标记清晰的 Postgres integration test**

测试必须验证 FK、same-subsection revision Head、same-project State Head、unique、并发可见性、transaction rollback 和 Alembic current=head；没有 `TEST_CANONICAL_DATABASE_URL` 时明确 skip，P1 Gate 不允许用 skip 结果冒充通过。

- [ ] **Step 2：增加 compose Postgres service**

Web/worker 使用 `CANONICAL_DATABASE_URL`；Postgres healthcheck 后再启动依赖服务。生产密码只从环境引用，不把真实凭据写入 compose。

- [ ] **Step 3：执行真实 Gate**

```powershell
docker compose up -d postgres
$env:TEST_CANONICAL_DATABASE_URL='postgresql+psycopg://writer_test:writer_test@localhost:5432/writer_test'
$env:CANONICAL_DATABASE_URL=$env:TEST_CANONICAL_DATABASE_URL
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_postgres_schema_v0.py -q
```

- [ ] **Step 4：P1 Gate**

- SQLite 与 Postgres migration/constraints 全绿。
- Canonical DB 单独恢复 Golden 全文与显式 Project State Head，hash 一致；测试不得用 `ORDER BY created_at` 猜 current state。
- 没有 Narrative Ontology 表混入。
- 所有表的创建者、更新者、删除/归档与 rebuild 规则已写入所有权文档。

达不到则不进入 P2。

---

## 4. P2：Commit Runtime 与 Document Runtime Foundation

### Task 11：把 Legacy Handover 解释为 Candidate，而不是先写状态

**文件：**

- Create: `app/canonical/legacy_candidate_adapter.py`
- Create: `tests/unit/canonical/test_legacy_candidate_adapter.py`
- Modify: `app/writing/contracts.py` only for compatibility exports if required

- [ ] **Step 1：写失败测试**

用 Golden handover 覆盖：`new_facts` → generic world mutation candidates；`arc_progress done/deviated` → event candidates；空/非法值被拒绝或形成 warning；输入对象不被修改；调用期间 WorldState/EventGraph mock 零调用。

- [ ] **Step 2：实现纯适配器**

适配器只解释现有 payload 并附 provenance/evidence，不能 import 或调用 `WorldStateManager.add_fact`、`EventGraph.update_arc_status`、Redis、Chroma。

- [ ] **Step 3：验证**

运行 adapter tests 和现有 Handover contract tests。

### Task 12：实现纯 Legacy StateTransition Compiler

**文件：**

- Create: `app/canonical/state_transition.py`
- Create: `tests/unit/canonical/test_legacy_state_transition.py`
- Modify: `tests/fixtures/foundation_golden_slice_v1.json`

**接口：**

- Consumes: `LegacyStateTransitionAdapter.compile(base_state: CanonicalStateSnapshot, candidate: SubsectionCandidate) -> StateTransitionResult`
- Produces: `PreparedCanonicalCommit(candidate, state_transition)`，供 Task 13 的 `CanonicalCommitService.commit()` 使用。

- [ ] **Step 1：写失败测试**

- `base_state.version_id != candidate.base_state_version_id` 时抛 `StateVersionConflict`，不生成 transition。
- 相同 base snapshot + 相同 candidate 的 `next_state_json/state_hash/ledger_events` byte-for-byte 稳定。
- 编译器不 import/call SQLAlchemy session、WorldState、EventGraph、Redis、Chroma、LLM 或文件系统。
- `next_state_json` 是完整 snapshot，不是只含 delta；保留未知 opaque 字段，并把 generic mutations/events 以稳定 ID、稳定 ordinal 写入 Foundation v0 envelope。
- 输入 base/candidate 不被修改；transition 的 `candidate_hash/base_state_version_id/state_hash` 都可复算。

- [ ] **Step 2：运行并确认失败**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_legacy_state_transition.py -q
```

- [ ] **Step 3：实现最小纯编译器**

`LegacyStateTransitionAdapter` 只维护 `foundation_state_v0` envelope：复制 base snapshot，按 canonical ID 排序合并 `world_mutations`，附加去重后的 `ledger_events`，更新 source candidate hash；它不把 `character.inventory.add` 等 predicate 解释成领域表或嵌套人物模型。Golden fixture 补 genesis state snapshot/hash，使测试完全离线。

- [ ] **Step 4：验证纯度与 determinism**

运行本测试两次并比较序列化 bytes，再运行 Candidate adapter/contract tests。

### Task 13：实现 Atomic Commit Service、双 Head OCC 与数据库幂等 reservation

**文件：**

- Create: `app/canonical/commit_service.py`
- Modify: `app/canonical/repositories.py`
- Create: `tests/unit/canonical/test_commit_service.py`
- Create: `tests/unit/canonical/test_commit_failure_atomicity.py`

- [ ] **Step 1：写失败测试矩阵**

至少覆盖：

- validation incomplete 拒绝且零行写入。
- base revision 不等于 current revision → `RevisionConflict`。
- `PreparedCanonicalCommit.state_transition.base_state_version_id` 不等于 Project State Head → `StateVersionConflict`；即使目标是另一个未修改 subsection 也必须拒绝。
- transition 的 candidate hash、base state 或 state hash 不能复算时拒绝。
- 首次 commit 同时生成 commit/revision/state/ledger/idempotency/outbox manifest，并原子移动 Subsection Revision Head 与 Project State Head。
- 相同 key/hash 重放返回同 commit。
- 相同 key/不同 hash → `IdempotencyConflict`。
- 两个 session 同时首次提交相同 key 时，唯一 reservation 只允许一个事务创建 Canon；另一个等待后读取 completed record。
- 在 revision、state、ledger、idempotency、outbox 任一步 monkeypatch 抛错后，九表行数与两个 Head 均回到事务前，`reserved` row 不得残留。
- Commit transaction 中若触碰 projection port，测试直接失败。

- [ ] **Step 2：实现单一 session transaction**

`CanonicalCommitService.commit(prepared: PreparedCanonicalCommit, idempotency_key: str)` 只接收已经编译好的 Canon 值与 SQL Unit of Work。协议固定为：

1. 在 nested transaction/savepoint 中插入 `IdempotencyRecord(status="reserved")`，数据库 `UNIQUE(tenant_id, project_id, idempotency_key)` 是最终裁判；禁止把 `SELECT then INSERT` 当作安全保证。
2. unique conflict 时只回滚 savepoint，读取首事务已完成 record；candidate hash 相同则返回原 result，不同则抛 `IdempotencyConflict`。若首事务回滚，竞争者的 insert 可成为 reservation owner。
3. reservation owner 以固定顺序 `SELECT ... FOR UPDATE` 锁 Project State Head，再锁 Subsection Revision Head，分别比较 `base_state_version_id` 与 `base_revision_number/current_revision_id`。
4. 只写入 `PreparedCanonicalCommit` 中已确定的 revision snapshot、next state snapshot、ledger events 与固定 projection manifest；Commit Service 不应用 mutation ontology。
5. 原子移动两个 Head，将 idempotency record 更新为 `completed` 并保存 result JSON，最后 commit。

SQLite unit tests 验证逻辑；不要用 SQLite 结果替代并发结论。

- [ ] **Step 3：验证**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\canonical\test_commit_service.py tests\unit\canonical\test_commit_failure_atomicity.py -q
```

### Task 14：验证 Postgres 并发、100 次重放和进程终止

**文件：**

- Create: `tests/integration/canonical/test_commit_concurrency.py`
- Create: `tests/integration/canonical/test_worker_crash_recovery.py`
- Create: `tests/integration/canonical/test_commit_replay.py`

- [ ] **Step 1：写真实并发测试**

- 20 个线程/进程用同 key 同 hash commit，结果只有 1 revision、1 commit、1 幂等记录和一组 ledger/outbox。
- 同 key 不同 hash 并发首次进入时，一个成功、其余全部 `IdempotencyConflict`，没有残留 reservation。
- 20 个相同 base revision、不同 key/hash 并发修改同一 subsection，只有一个成功，其余为明确 `RevisionConflict`。
- 两个不同 subsection Candidate 同时基于同一 State Head：先提交者移动 State Head，后提交者即使自己的 subsection revision 未变化也必须得到 `StateVersionConflict`。
- 同一消息顺序/乱序重放 100 次仍只有一个 revision。

- [ ] **Step 2：写 crash tests**

子进程在三个受控 barrier 停住：事务前、事务中写若干表后、SQL commit 已返回后。父进程终止子进程并用新 service/process 重试：前/中都得到一次新完整 commit；后通过 Repository/CommitService lookup 得到原 commit result。本任务不模拟 LLM；完整“Celery retry 不重新生成”在 Task 18 的 Coordinator integration test 验证。

不要在 production service 中保留可由环境任意触发的 crash flag；测试用 dependency hook 或 SQLAlchemy test event 注入。

- [ ] **Step 3：运行 Postgres tests**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical\test_commit_concurrency.py tests\integration\canonical\test_worker_crash_recovery.py tests\integration\canonical\test_commit_replay.py -q
```

### Task 15：实现 Transactional Outbox 与 Critical Projection Barrier

**文件：**

- Create: `app/canonical/outbox.py`
- Create: `app/canonical/projection_ports.py`
- Create: `app/canonical/projection_barrier.py`
- Create: `tests/unit/canonical/test_outbox_dispatcher.py`
- Create: `tests/unit/canonical/test_projection_barrier.py`
- Create: `tests/integration/canonical/test_outbox_retry.py`

- [ ] **Step 1：写失败测试**

- Commit 成功后固定 critical/non-blocking projection manifest 全部以独立 pending outbox rows 持久化，unique commit + projection name。
- projector 成功后 published；失败后 Canon 不变、attempts/last_error 更新且可 retry。
- 重启 dispatcher 后仍能从 DB 找到 pending/failed event。
- duplicate commit 不新增 outbox。
- dispatcher 永不把 candidate/rejected 数据投影。
- 任一 critical event pending/failed 时 `ProjectionBarrier.ensure_ready(commit_id)` 不得返回 ready；所有 critical published 后才 ready。
- non-blocking event 全部失败也不影响 Barrier ready。

- [ ] **Step 2：实现最小 dispatcher**

提供 `dispatch_critical(commit_id)`、`dispatch_non_blocking(commit_id)`、`dispatch_pending(limit)` 与 `ProjectionBarrier.ensure_ready(commit_id)`。P2 允许单进程同步触发 + 启动时补偿扫描；完整租约、并发 worker、死信、全量 rebuild 与跨 projector orchestration 留到 P3A。

- [ ] **Step 3：明确 P2 的投影保证**

- 保证 Canon durable、event durable、失败可见可重试。
- Golden projector 使用 deterministic commit/chunk identifiers 或 replace semantics，避免普通 retry 重复。
- 保证 read-after-write：critical barrier 未 ready 时不得生成下一 subsection。
- 不宣称所有 Legacy 外部系统具备 crash-window exactly-once；这项 Gate 属于 P3A。

### Task 16：先把 Writer 单小节生成提取成无副作用 Candidate seam

**文件：**

- Modify: `app/agents/writer.py`
- Create: `app/writing/subsection_generator.py`
- Create: `tests/unit/test_writer_candidate_generation.py`
- Modify: `tests/unit/test_writer.py`
- Modify: `tests/unit/test_writing_pipeline.py`

- [ ] **Step 1：写 characterization tests**

固定 mock LLM、outline/context、从 Coordinator 注入的 `CanonicalStateSnapshot`，记录当前一个 subsection 的 prompt hash、draft hash、validation、handover observation 和返回字段；同时把 WorldState、EventGraph、VectorStore、ContextManager、Blackboard/stream 设为副作用 spies。WorldState/EventGraph 的只读查询可以执行，任何 mutation method 调用立即失败。

新 `generate_subsection_candidate()` 在返回 Candidate 前不得触发任何 spy，且 Candidate 的 `base_state_version_id` 必须等于实际输入 snapshot version；禁止构建 Candidate 后再查询最新 Head 回填。

- [ ] **Step 2：先做行为保持型提取**

从 `Writer.run` 中提取“准备输入 → assemble prompt → generate → handover extraction → post validation → build Candidate”的单小节逻辑到 `SubsectionGenerator`。先保持 prompt 与 draft byte-equivalent，再删除旧的 pre-validation `commit_handover_effects` 调用。

- [ ] **Step 3：保留旧 facade contract**

`Writer.run` 的已冻结参数/返回 shape 暂不破坏；legacy mode 可通过 compatibility orchestrator 使用旧投影，canonical mode 由 Coordinator 调用 Candidate seam。不要为注入 commit service 再改 `Writer.run` 参数。

- [ ] **Step 4：验证纯度与行为**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\unit\test_writer_candidate_generation.py tests\unit\test_writer.py tests\unit\test_writing_pipeline.py -q
```

### Task 17：把现有副作用封装成 post-commit Golden projector

**文件：**

- Create: `app/writing/legacy_subsection_projection.py`
- Modify: `app/writing/state_committer.py`
- Modify: `app/vector_store.py`
- Modify: `app/writing/subsection_handover_persistence.py`
- Create: `tests/unit/test_legacy_subsection_projection.py`

- [ ] **Step 1：写失败测试**

- 没有 `CanonicalCommitResult` 的 candidate 不能投影。
- critical projector 固定拆为 `legacy_world_event`、`handover_context`、`chroma_story_chunks`；non-blocking projector 为 stream/preview/export/analytics。每个 projector 只消费自己的 outbox row，不以一个笼统的“projection done”掩盖局部失败。
- 每个 sink 收到 `commit_id/revision_id/content_hash` provenance。
- Chroma chunk id 由 `commit_id + chunk ordinal + content hash` 确定；重复投影不新增 chunk。
- 任一 sink 失败抛出可分类错误给 dispatcher，但不调用 Canon commit/rollback。

- [ ] **Step 2：移动副作用**

将 `StateCommitter.commit_handover_effects` 和 `commit_subsection` 中的外部写入迁入 `LegacySubsectionProjection`；旧 `StateCommitter` 只作为 legacy facade/compatibility adapter，`_committed` 字典不得参与 canonical 幂等。

- [ ] **Step 3：验证**

运行投影 tests、vector store tests、handover persistence tests。

### Task 18：接入 Coordinator、retry preflight 与 Projection Barrier

**文件：**

- Create: `app/writing/canonical_subsection_runtime.py`
- Modify: `app/coordinator.py`
- Modify: `app/task_store.py`
- Create: `app/task_store_migrations.py`
- Modify: `app/routers/tasks.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Create: `tests/unit/test_canonical_subsection_runtime.py`
- Create: `tests/integration/test_coordinator_canonical_canary.py`
- Create: `tests/integration/test_coordinator_canonical_retry.py`
- Modify/Create: task history/route contract tests

- [ ] **Step 1：写运行时状态机测试**

覆盖两条固定顺序：

1. 新提交：load both Heads/state snapshot → generate Candidate → validate → compile StateTransition → commit Canon → checkpoint committed ids → dispatch critical projections → Barrier ready → 才允许 next subsection → 异步 non-blocking projections。
2. Celery retry：derive stable idempotency key → lookup completed result；存在则跳过 LLM/compile/commit，直接恢复 checkpoint 并追 critical Barrier；不存在才进入生成。

Commit 失败时零 projection；critical projection 失败时 task phase 为 `awaiting_critical_projection`，document/state committed 状态不倒退，且下一 subsection 的 LLM mock 零调用。Non-blocking projection 失败只显示 lag/failure，不暂停下一 subsection。

- [ ] **Step 2：实现 mode router**

- `canonical_subsection_runtime` 组合 Repository、Generator、StateTransitionCompiler、CommitService、Dispatcher 与 ProjectionBarrier，Writer 不持有数据库 session。
- canary 精确到 task/subsection，非 allowlist 保持 legacy；同一 subsection 不能双写。
- `internal_required` 模式缺少 scope/document binding、两个 base Heads、数据库或 critical barrier 时 fail closed；该模式只允许内部 dogfood。
- checkpoint 保存 `generation_attempt_id/idempotency_key/document_id/current_revision_id/current_state_version_id/last_commit_id/critical_projection_status`，不再把完整正文作为新路径的恢复权威。
- 恢复路径先查 Idempotency Repository；commit-after-crash 的 retry 必须返回原 result，不能因为再次 LLM 生成了不同 Candidate 而制造 `same key/different hash` 冲突。

- [ ] **Step 3：迁移 TaskStore 语义**

用版本化 migration 为 `task_history` 增加 `document_id/current_revision_id/last_commit_id` 引用；保留 `draft_preview` 供旧页面兼容，但新 API/result 的全文从 Canonical Repository 读取。不要在本阶段把旧 TaskStore 整体迁入 SQLAlchemy。

- [ ] **Step 4：保持 API additive compatibility**

旧 response 字段继续存在，新增 `document_ref`、`commit_status`、`state_version_id`、`critical_projection_status`、`non_blocking_projection_status`；contract snapshot 允许 additive change，禁止旧字段消失。

### Task 19：Golden Slice 真实全链与故障演练

**文件：**

- Create: `tests/integration/canonical/test_golden_vertical_slice.py`
- Create: `tests/integration/canonical/test_golden_projection_failures.py`
- Create: `scripts/foundation/run_golden_slice.py`
- Create: `scripts/foundation/verify_foundation_gate.py`
- Create: `reports/foundation/p2-golden-slice-evidence.json`
- Create: `reports/foundation/p2-recovery-drill.md`

- [ ] **Step 1：先写端到端失败测试**

用固定 Golden Candidate（不调用真实 LLM）跑 Postgres：创建 scope/genesis State Head → compile transition → commit/move two Heads → materialize document/state → dispatch critical projection → Barrier ready → dispatch non-blocking projection → 查询 task result。断言正文 hash、Project State Head/state hash、ledger count、每个 outbox status、API document ref。

- [ ] **Step 2：故障矩阵**

至少执行：

| 注入点 | 预期 |
|---|---|
| revision/state/ledger/idempotency/outbox SQL step | 零部分 Canon 写入 |
| SQL commit 后 critical Legacy/Handover down | Canon 成立，Barrier not ready，下一 subsection 零 LLM 调用 |
| SQL commit 后 critical Chroma down | 同上；恢复后 deterministic projection，Barrier 才 ready |
| non-blocking Redis Stream/Markdown down | Canon/Barrier ready，允许下一 subsection，失败可补偿 |
| critical projection 中途进程终止 | Canon 不变，新 dispatcher 继续；未 ready 前不生成下一小节 |
| 同消息 100 次 | 1 revision、1 commit、1 ledger set、1 固定 outbox manifest |
| 两个 subsection 基于同一 State Head 并发 | 1 success、1 `StateVersionConflict` |
| commit 成功但 worker 未收到结果 | retry preflight 返回原 result，LLM 零调用 |
| 删除临时 Redis/Chroma/Markdown | 从 Canon 恢复正文 hash 100% |

- [ ] **Step 3：运行真实脚本并生成证据**

报告只保存 ID/hash/count/duration/error type，不保存 API key、完整 prompt 或不必要的正文。

```powershell
& .\.venv\Scripts\python.exe scripts\foundation\run_golden_slice.py --fixture tests\fixtures\foundation_golden_slice_v1.json
& .\.venv\Scripts\python.exe scripts\foundation\verify_foundation_gate.py --evidence reports\foundation\p2-golden-slice-evidence.json
```

### Task 20：全量回归、internal_required dogfood 决策与回滚演练

**文件：**

- Modify: `docs/architecture/foundation-data-ownership-v0.md`
- Create: `docs/runbooks/foundation-canonical-cutover.md`
- Create: `reports/foundation/p0-p2-gate-summary.md`
- Modify: `docs/PROGRESS.md` only after all evidence passes

- [ ] **Step 1：运行完整验证**

```powershell
uv sync --locked --extra dev
& .\.venv\Scripts\python.exe -m pytest tests\unit tests\integration tests\contract -q
& .\.venv\Scripts\python.exe -m pytest tests\integration\canonical -q
& .\.venv\Scripts\python.exe scripts\foundation\verify_foundation_gate.py --evidence reports\foundation\p2-golden-slice-evidence.json
git diff --check
git status --short
```

必须保存每条命令的新鲜退出码、passed/failed/skipped 数；Postgres Gate 测试被 skip 视为未完成。

- [ ] **Step 2：回滚演练**

- canary → legacy 只影响下一次未开始的 subsection；已 canonical commit 的正文仍由 Canon 读取，不允许回写旧 checkpoint 覆盖。
- `internal_required` dogfood 切换失败时暂停内部新任务，保留 Canon DB/outbox，再切 legacy 处理 pre-Foundation resume。
- 演练一次“commit 已成功但 critical projection 未完成”的恢复，证明回滚 mode 不会否认已成立 Canon，也不会越过 Barrier 继续生成。

- [ ] **Step 3：P2 最终 Gate**

只有以下全部成立才批准内部 dogfood 新任务使用 `internal_required`；这不是外部 production readiness：

- 单元、集成、contract 全绿且零未解释失败。
- 一个真实 Golden subsection 完成 Candidate → StateTransition → Commit → 双 Head 移动 → Outbox → Critical Barrier ready。
- 事务每一步失败均零部分 Canon 写入。
- 100 次重复消息只产生一个 canonical result。
- 事务前/中/后进程终止恢复结果一致。
- 同 subsection revision 冲突与跨 subsection state-head 冲突都被明确拒绝。
- 并发首次相同 idempotency key 由 DB unique reservation 协议收敛，而不是依赖先查后写。
- critical projection 未追平时下一 subsection 不会开始；non-blocking failure 不错误阻塞。
- Redis/Chroma/Markdown 不可用时 Canon 正文与 state 仍完整。
- Writer canonical path 无 pre-commit Legacy/Redis/Chroma 写入。
- checkpoint/fixture/report/log 无明文 API key。
- 旧外部 contract 仍通过，新增字段均为 additive。

- [ ] **Step 4：提交与评审**

按 P0、P1、P2 小 Gate 分提交，避免一个超大 commit。完成后使用 `superpowers:requesting-code-review`，修复评审问题并重新执行本任务的全部验证；再使用 `superpowers:finishing-a-development-branch` 决定合并方式。

---

## 5. 建议提交序列

1. `chore: freeze and sanitize foundation baseline`
2. `test: establish deterministic pre-foundation contracts`
3. `feat: add canonical schema v0 and migrations`
4. `feat: add scoped canonical repositories and snapshot recovery`
5. `feat: add subsection candidate and legacy state transition`
6. `feat: add dual-head atomic commit and idempotency reservation`
7. `feat: add transactional outbox and projection barrier`
8. `refactor: make writer subsection generation side-effect free`
9. `feat: project canonical subsection commits to legacy stores`
10. `feat: route canary writing through canonical runtime`
11. `test: prove foundation golden slice fault recovery`

每个提交前运行本任务定向测试；P0/P1/P2 Gate 提交前运行对应全量集合。

## 6. 完成定义与下一阶段入口

Foundation 完成后，系统首次具备“作品已发生一次正式变化”的确定定义：一份通过验证且基于未过期 Revision/State Heads 的 Candidate，经纯 StateTransition 编译后，在单一关系型事务中形成不可变 Revision、状态版本、Ledger、幂等结果与 Outbox，并原子移动两套 Head。Redis、Chroma、Handover、WorldState/EventGraph legacy store 和 Markdown 即使暂时失败，也不能改变这次提交是否成立；但 critical projection 未追平时系统必须暂停后续生成，避免 read-after-write 叙事不一致。

完成后只批准两个后续入口：

1. P3A：把 Minimal Projection 扩展成具备 lease、并发 worker、dead-letter、全量 rebuild、hash/ledger reconciliation 的生产投影与恢复 Gate。
2. P3B：并行建设鉴权、租户隔离、credential reference、CI、可观测性、备份恢复与外部试用安全门槛。

P4 World Runtime 只能把更好的事实解释 Candidate 接到既有 Commit Runtime，不能重新定义提交边界。
