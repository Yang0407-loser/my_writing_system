# 前端 API 对接与交互升级完整计划

> 日期：2026-08-12  
> 范围：`/write-ui-v2` 当前前端、FastAPI 现有路由、P3A 状态与当前已存在的 World Runtime/StateFrame 能力  
> 目标：先修复任务链路和接口错位，再将单页“功能弹窗集合”升级为可持续扩展的长篇写作工作台。

## 1. 执行摘要

当前前端已经覆盖了大纲、正文生成、角色、规则、伏笔、地图、势力、关系、支线、物品、时间线、AI 痕迹检测和审阅等大量功能，但存在三个结构性问题：

1. **任务主链路仍有断点。** 交互模式切换新 `task_id`、草稿任务锚点、世界观卡片持久化和大纲评估接口存在明确错位。
2. **后端能力没有形成页面工作流。** StateFrame、连续性审查、叙事事件图谱、地图路线、物品流转、支线热力图、关系阶段和 Canon/Projection 状态尚未进入主要界面。
3. **前端信息架构已经超过“顶部按钮 + 20 个模态框”的承载上限。** 用户可以找到功能，但难以理解当前任务、当前章节、当前世界状态和下一步行动之间的关系。

升级策略采用两段式：

- **第一段：稳定现有 Vue 3 ES Modules 页面。** 修复 P0 接口、任务会话、错误处理、轮询和持久化问题，不等待框架迁移。
- **第二段：按领域模块重构工作台。** 将高频能力放入常驻工作区，将低频能力放入抽屉或命令面板，并逐步迁移到 Vue 3 + Vite + TypeScript。

最终产品形态不是“增加更多弹窗”，而是由以下五个稳定工作区组成：

| 工作区 | 用户目标 | 核心数据 |
|---|---|---|
| 写作 | 生成、编辑、修订、保存和恢复正文 | task、stream、draft、revision、review |
| 大纲 | 组织章节、事件预算、角色弧和写作队列 | outline、event contract、arc projection |
| 世界 | 管理人物、关系、势力、地点、物品和伏笔 | characters、relations、factions、map、items、foreshadowings |
| 分析 | 查看承续、事件图、StateFrame、支线热力和质量变化 | continuity、events、state-frame、subplot heat map |
| 项目 | 切换历史任务、查看运行状态、导出和恢复 | history、result、document ref、projection status |

---

## 2. 当前前端文件审计

### 2.1 实际在生产页面加载的文件

| 文件 | 当前职责 | 主要问题 | 升级处理 |
|---|---|---|---|
| `app/static/index.html` | 全部页面结构和约 20 个模态框 | 结构超过 1100 行；业务区、弹窗和表单全部耦合；只有一个页面入口 | 拆为工作台壳、领域页面、抽屉和对话框组件 |
| `app/static/js/main.js` | 全部响应式状态、API 调用、轮询、领域逻辑 | 约 1300 行；任务状态和 UI 状态混杂；重复刷新；错误大量静默 | 先拆 composable/store，再迁移 TypeScript |
| `app/static/js/api.js` | API 请求封装 | 无超时、取消、错误码、重试、响应校验；存在前端调用但未导出的函数 | 替换为统一 API Client 和领域 service |
| `app/static/js/outline-tree.js` | 大纲树基础操作 | 可继续使用，但缺少命令历史和批量操作抽象 | 保留纯函数，增加单测和 command/undo 层 |
| `app/static/js/utils.js` | 树转换、字数等工具 | 与后端 outline shape 强耦合 | 加输入输出契约和测试 |
| `app/static/styles/base.css` | 当前全部有效样式 | 超过 1100 行；大量内联样式；没有响应式断点 | 拆 token、layout、component、state 样式 |

### 2.2 当前未被页面加载或已形成重复实现的文件

| 文件 | 现状 | 风险 | 建议 |
|---|---|---|---|
| `app/static/js/app.js` | 未被 `index.html` 引用 | 与 `main.js` 存在另一套启动和轮询逻辑 | 完成功能对照后删除或归档，不再双线维护 |
| `app/static/js/store.js` | 未被当前入口引用 | 状态模型与 `main.js` 内部 ref 不一致 | 作为新 store 设计参考，不直接继续扩展 |
| `app/static/js/stream.js` | 未被当前入口引用 | 与 `main.js` 的轮询重复，修复容易只改到一份 | 提取唯一 TaskStreamController 后删除重复实现 |
| `app/static/js/persistence.js` | 未被当前入口引用 | 过期时间与 `main.js` 内联持久化策略不一致 | 合并为唯一 workspace persistence 模块 |
| `app/static/js/components/*` | 未被当前入口引用 | 组件代码可能与真实页面行为逐渐漂移 | 迁移时逐项核对，不能假设可直接复用 |
| `app/static/styles/cards.css`、`panels.css` | 未在 `index.html` 引用 | 样式存在但用户页面不生效 | 合并有效规则后移除死样式 |

### 2.3 架构结论

- 当前所谓“ES Modules 多文件前端”，实际运行核心仍是 `index.html + main.js + base.css` 三个大文件。
- Vue 通过 `https://unpkg.com/vue@3/...` 加载，版本未固定且本地离线启动依赖外网；应至少固定版本并本地化，后续由 Vite 打包。
- 页面没有路由层，所有管理功能都通过模态框叠加；随着功能增加，状态恢复、焦点管理、深链接和浏览器返回行为都无法自然实现。
- 前端已经出现“旧模块存在但不加载、当前逻辑又重新内联”的双实现现象，应先建立唯一所有权，再继续开发。

---

## 3. 必须优先修复的接口与任务链路问题

### P0-1：交互模式没有切换后端返回的新任务 ID

**现状**

- `POST /tasks/{task_id}/decide` 在继续执行时返回 `new_task_id`。
- `sendDecisionFn()` 只关闭确认状态，没有把 `taskId` 切换为 `new_task_id`，也没有重启针对新任务的轮询。

**用户影响**

- 用户批准大纲或章节后，前端继续轮询旧任务。
- 新任务已经在后端运行，但页面看起来停住，交互模式实际不可用。

**升级动作**

- `sendDecision()` 返回统一的 `TaskTransition`。
- 若包含 `new_task_id`，原子执行：停止旧轮询、记录任务链、切换 ID、从旧 stream cursor 断开、启动新状态和新 stream 轮询。
- UI 显示“已从任务 A 继续到任务 B”，但项目级内容保持同一个 workspace。

### P0-2：开始写作时可能丢失草稿任务锚点

**现状**

- 大纲和素材先保存到 draft `task_id`。
- `startWriting()` 中多次调用 `resetWriting()`，该函数会清空 `taskId`。
- 随后 `/write` 可能创建新的 Celery task，造成大纲、势力、地图、物品等仍挂在旧 draft task 下。

**用户影响**

- 写作前创建的世界元素与写作中的任务状态分裂。
- 历史恢复时不同资源可能来自不同 task。

**升级动作**

- 分离 `resetRunState()` 与 `resetWorkspace()`；开始写作只能清理生成态，不得清理 workspace/task 锚点。
- 引入稳定 `workspace_id/project_id` 概念；短期继续使用 draft `task_id` 时，必须从创建到生成全程复用。
- 后端 `/write` 返回并确认 `workspace_task_id`，前端检测响应 ID 是否异常变化。

### P0-3：世界观卡片调用不存在的 `API.saveWorldSetting`

**现状**

- `main.js` 在采纳世界观卡片时调用 `API.saveWorldSetting()`。
- `api.js` 没有该导出，后端也没有允许 draft 状态更新世界观的通用接口。
- 现有 `/tasks/{task_id}/edit-field` 只允许已完成任务，且字段白名单不包含世界观和梗概。

**升级动作**

- 新增 `PATCH /tasks/{task_id}/workspace`，允许保存 `topic`、`world_setting`、`story_synopsis`、`reference_text`、`style_profile`、`target_words_per_section`。
- 前端设置区和卡片采纳统一调用 `workspaceService.patch()`。
- 字段保存采用 500–800ms debounce，并显示“保存中 / 已保存 / 保存失败”。

### P0-4：大纲逻辑评估调用不存在的路由

**现状**

- 前端调用 `POST /api/analysis/evaluate?task_id=...`。
- 后端实际提供的是：
  - `POST /tasks/{task_id}/review/continuity`
  - `GET /tasks/{task_id}/events`
  - `POST /tasks/{task_id}/analyze`
- 当前“大纲逻辑评估”模态框也没有可见入口，是一项半接入死功能。

**升级动作**

- 若目标是写前大纲评估，新增明确接口 `POST /tasks/{task_id}/outline/evaluate`，输入节点范围和评估维度。
- 若目标是写后分析，删除旧调用，拆成“连续性审查”和“写后关系/时间线分析”两个动作。
- 不再用含糊的 `/api/analysis/evaluate` 混合写前与写后语义。

### P0-5：错误与断线被静默吞掉

**现状**

- 多数 `catch` 只显示“失败”，部分直接空处理。
- status/stream 轮询错误后无限静默重试。
- 后端错误文本没有被解析成可操作错误。

**升级动作**

- API Client 统一解析 FastAPI `detail`，输出 `code/status/message/retryable/requestId`。
- 连续 3 次轮询失败进入“正在重连”；10 秒未恢复显示手动重试；30 秒进入离线保护状态。
- 生成、抽卡、分析等长操作支持 AbortController 和防重复提交。
- Vue 根应用增加全局错误边界，单个面板失败不能拖垮整个页面。

### P0-6：`sendBeacon` 草稿保存契约不稳定

**现状**

- `beforeunload` 直接发送字符串，浏览器可能使用 `text/plain`。
- `PUT /tasks/{task_id}/draft` 需要 JSON body，且 `sendBeacon` 固定为 POST，当前调用路径与方法契约不一致。

**升级动作**

- 常规编辑通过 debounce 的 `PUT` 自动保存。
- 离开页面使用专用 `POST /tasks/{task_id}/draft/beacon`，接受 `application/json` 或 Blob。
- 页面关闭保存仅作为兜底，不再承担主要持久化职责。

### P0-7：完成节点显示“导出”，但没有正文导出功能

**现状**

- 流程图包含“导出”节点。
- 完成弹窗只有查看审阅和关闭；前端没有调用 `/result/{task_id}`，也没有安全下载接口。

**升级动作**

- 新增结果中心和 `GET /tasks/{task_id}/exports`、`POST /tasks/{task_id}/exports`、`GET /tasks/{task_id}/exports/{export_id}/download`。
- 支持 Markdown、TXT、JSON；DOCX 作为后续能力。
- 不把服务端本地文件路径直接暴露给浏览器。

---

## 4. 逐功能 API 对接矩阵

状态定义：

- **已接通**：主要 CRUD 或工作流可用。
- **半接通**：只使用了部分后端能力，或交互状态不完整。
- **错位**：前端调用不存在、方法不匹配或任务语义错误。
- **未呈现**：后端已有能力，但页面没有入口。
- **需新增 API**：前端升级需要后端补充契约。

| 页面/功能 | 当前前端 | 后端能力 | 状态 | 升级重点 |
|---|---|---|---|---|
| 创建写作 | `createTask`、`startWriting` | `POST /tasks`、`POST /write` | 半接通 | 保持稳定 workspace/task 锚点，防重复提交 |
| 任务状态 | 1s 轮询 `/status` | Canon 引用、commit、两类 projection 状态已返回 | 半接通 | 展示提交、关键投影阻塞、非阻塞降级和版本 ID |
| 流式正文 | 300ms 轮询 `/stream` | Redis Stream | 已接通但脆弱 | 自适应退避、断线重连、事件去重、批量渲染 |
| 交互审批 | `sendDecision` | 返回 `new_task_id` | 错位 | 切换任务链并恢复轮询 |
| 停止写作 | 通过 decide/stop | Celery revoke + cancelled event | 半接通 | 显示“停止请求中/已停止”，处理 late event |
| 草稿自动保存 | 定时和 beacon | `PUT/GET /tasks/{id}/draft` | 半接通 | 修正 beacon 方法，保存状态可见化 |
| 大纲树 | 保存、恢复、删除撤销、版本 | outline 路由完整 | 已接通 | 批量操作、版本 diff、保存冲突和乐观锁 |
| 篇幅建议 | 已使用 budget advice | 对应 API 已有 | 已接通 | 允许批量采纳、变更预览、撤销 |
| 角色弧确认 | 已使用 preview/confirm | 对应 API 已有 | 已接通 | 将实验弹窗并入大纲检查面板，突出 stale 状态 |
| 世界观/梗概设置 | 只在内存中编辑 | 缺少 draft workspace patch | 错位 | 新增 workspace 保存 API 与自动保存 |
| 大纲逻辑评估 | 调不存在路由 | 有连续性/写后分析，无写前评估 | 错位 | 新增明确写前评估，或移除死功能 |
| 角色库 | CRUD、提取 | stats、batch-save 也已存在 | 半接通 | 批量保存、冲突合并、项目角色/全局角色分层 |
| 规则 | CRUD、导入导出 | context、presets/list 未使用 | 半接通 | 显示实际注入上下文、启停和预设选择 |
| 伏笔 | 列表、新建 | get/update/delete、chapter context 已有 | 半接通 | 编辑、状态推进、按章节提醒和回收操作 |
| 创作讨论 | chat | quick-prompts、summarize 已有 | 半接通 | 快捷提问、会话摘要、将结论应用到项目 |
| 灵感库/抽卡 | draw/redraw/inspirations | cards API | 半接通 | 采纳动作事务化，显示将影响哪些实体 |
| 审阅报告 | 从 `/status` 读取 review | 连续性和写后分析另有 API | 半接通 | 统一质量中心，支持从建议发起定向修订 |
| AI 痕迹检测 | analyze | high-freq、patterns 也有 | 已接通 | 文本定位、批量修订、前后对比 |
| 写后定向修订 | 无页面入口 | `POST /tasks/{id}/revise` | 未呈现 | 选中小节/文本发起修订，保留版本 |
| 任务字段编辑 | 无入口 | `/edit-field` 仅 completed | 未呈现/受限 | 由更清晰的 workspace/draft revision API 替代 |
| 地图 | 列表、创建地点 | nodes、edges、route 已有 | 半接通 | 地图画布、连线、主角路线、章节行程 |
| 地图编辑/删除 | 无 | 后端也缺 update/delete node/edge | 需新增 API | 完整图编辑和撤销 |
| 势力 | CRUD 基本使用 | member、relation、context 已有 | 半接通 | 成员不能继续塞进 FactionBody；改用成员和关系接口 |
| 势力关系 | API 已封装部分 | relations/list、set/delete | 未呈现 | 关系图和冲突变化时间线 |
| 角色关系 | CRUD | advance-stage 已有 | 半接通 | 阶段推进、章节绑定、当前阶段提示 |
| 支线 | CRUD、抽卡 | heat-map、auto-bind 已有 | 半接通 | 热力图、自动绑定预览、与大纲双向定位 |
| 物品 | 列表、新建 | update、inventory、transaction 已有 | 半接通 | 所有权流转、角色背包、来源与去向时间线 |
| 物品删除 | 无 | 后端缺 delete | 需新增 API | 软删除/退役，避免破坏历史事件 |
| 经历时间线 | 只读列表 | context、单事件详情已有 | 半接通 | 章节筛选、人物轨道、跳转正文和 StateFrame |
| 写后关系/时间线分析 | 无 | `POST /tasks/{id}/analyze` | 未呈现 | 分析任务、结果缓存、可选择写入正式实体 |
| 连续性审查 | 无独立入口 | `POST /tasks/{id}/review/continuity` | 未呈现 | 章节过渡评分、问题定位、修复动作 |
| 叙事事件图 | 无 | `GET /tasks/{id}/events` | 未呈现 | 事件图、角色/伏笔/章节过滤和正文跳转 |
| StateFrame | 无 | 指定小节 before/after/delta/quality 已有 | 未呈现 | 小节状态检查器；后端补 list/latest 查询 |
| Canon 提交状态 | 无 | `/status` 已返回 document ref、commit status | 未呈现 | 在任务状态抽屉展示，不使用内部术语轰炸用户 |
| Projection 状态 | 无 | `/status` 已返回 critical/non-blocking status | 未呈现 | 显示“内容已提交/索引同步中/关键同步阻塞” |
| 历史任务 | 列表、恢复、删除 | list、history detail、delete 均有 | 半接通 | 使用详情 API；区分活跃、暂停、完成、失败 |
| 导出 | 无正文导出 | result 有 output_file，但无下载 API | 需新增 API | 导出中心和安全下载 |

---

## 5. 目标页面与信息架构

### 5.1 全局工作台壳

页面从上到下分为四层：

1. **项目栏**：项目切换、标题、保存状态、运行状态、最近版本、更多菜单。
2. **工作区标签**：写作、大纲、世界、分析、项目。
3. **主工作区**：根据当前任务显示两栏或三栏，而不是始终固定三栏。
4. **任务运行条**：只在生成、分析、导出等后台任务存在时显示，支持展开任务时间线。

顶部不再平铺 15 个彩色文字按钮。高频命令保留在当前工作区工具栏，低频命令进入“添加素材”菜单、命令面板或右侧检查器。

### 5.2 写作工作区

**布局**

- 左栏：章节/小节导航、写作队列、完成状态、字数偏差。
- 中栏：正文编辑器，支持生成中只读和完成后编辑两种状态。
- 右栏：当前小节上下文检查器，包含人物、地点、物品、伏笔、规则、StateFrame 和审阅建议。

**新增交互**

- 选中文本后出现紧凑浮动工具：改写、扩写、压缩、增强对话、检查一致性。
- 小节顶部显示生成状态、自动保存状态、revision、质量分和 projection readiness。
- 生成流采用分段缓冲更新，避免每个 token 都触发大范围 Vue 渲染。
- 允许“重试本小节”“从此处重新生成”“采用修订”“对比原稿”。
- 完成后自动打开结果摘要侧栏，不用阻塞式庆祝弹窗打断用户。

### 5.3 大纲工作区

**布局**

- 左侧树：卷/章/小节层级和写作队列。
- 中部详情：标题、梗概、要点、事件契约、目标字数、注入素材。
- 右侧检查器：篇幅建议、角色弧候选、支线覆盖、伏笔计划、问题列表。

**新增交互**

- 批量选择节点、批量拆分、批量设置字数、批量入队。
- 拖拽排序替代大量上下移动按钮，并保留键盘操作。
- 保存版本时生成可读摘要；恢复前显示树级 diff。
- 评估问题可直接定位到节点，并提供“应用建议”而不是只展示报告。
- 角色弧、支线、地点和伏笔不再通过右键小弹层硬塞，统一进入节点检查器。

### 5.4 世界工作区

使用二级标签组织领域：人物、关系、势力、地图、物品、伏笔。

**统一交互模型**

- 左侧可搜索列表。
- 中部详情编辑。
- 右侧关联关系与章节时间线。
- 每个实体都能跳转到首次出现章节、相关事件、当前 StateFrame 和引用它的大纲节点。

**新增功能**

- 人物关系图与阶段推进。
- 势力成员和势力关系图。
- 地图节点、边和主角路线编辑。
- 物品所有权流水和角色背包。
- 伏笔按“待埋设、已埋设、已暗示、待回收、已回收、逾期”分组。

### 5.5 分析工作区

包含五个子视图：质量总览、连续性、事件图、状态变化、支线节奏。

**质量总览**

- 章节评分、维度评分、成本、生成耗时、问题数量。
- 点击任一问题定位正文并启动修订。

**连续性**

- 使用 `/review/continuity`，显示章节过渡、交接信息和冲突点。
- 支持只分析新写章节，避免每次全量调用。

**事件图**

- 使用 `/events` 展示叙事事件、人物和章节关系。
- 支持按人物、事件类型、状态和章节筛选。

**状态变化**

- 使用 StateFrame 展示 before/after/delta/quality。
- 页面优先说“本小节前后发生了什么变化”，技术来源和 schema 放在高级信息中。

**支线节奏**

- 使用 `/subplots/heat-map` 形成章节热力图。
- 自动绑定先预览 diff，再调用 `/auto-bind` 提交。

### 5.6 项目工作区

- 历史项目筛选、排序和状态分组。
- 项目详情包含主题、最近编辑、字数、章节、运行状态、错误和导出记录。
- 支持继续写作、复制项目、归档、删除、导出。
- 显示 Canon 内容版本和同步状态，但将内部字段翻译成用户语言：
  - `committed` → 内容已保存
  - critical projection lagging → 关键索引同步中，暂不可继续生成
  - non-blocking lagging → 辅助数据同步中，不影响编辑

---

## 6. 通用交互升级规范

### 6.1 状态必须完整

每个远程数据区都必须实现：

- 初次加载骨架屏。
- 空状态和明确的下一步动作。
- 错误状态、原因和重试按钮。
- 刷新中但保留旧数据的 stale 状态。
- 成功保存反馈。

禁止继续使用空 `catch` 让区域悄悄保持旧数据。

### 6.2 保存与冲突

- 表单字段采用 debounce 自动保存，破坏性或跨实体动作显式确认。
- 保存状态固定显示在项目栏，不用频繁 toast。
- 大纲、正文和关键世界实体增加 `revision/version`，提交时携带期望版本。
- 发生冲突时显示“你的版本/服务器版本/合并结果”，不直接覆盖。

### 6.3 后台任务

- 统一 Task Activity Center 管理写作、分析、抽卡、导出、批量绑定等操作。
- 每项任务显示排队、运行、等待确认、重连、失败、完成。
- 用户可在不关闭当前页面的情况下查看任务详情和日志摘要。

### 6.4 危险操作

- 用应用内确认对话框替换 `window.confirm()`。
- 删除项目、恢复版本、覆盖角色等动作展示影响范围。
- 大纲删除继续支持撤销；世界实体优先软删除/归档。

### 6.5 键盘与焦点

- `Ctrl/Cmd+S`：立即保存当前编辑。
- `Ctrl/Cmd+K`：命令面板。
- `Alt+1..5`：切换工作区。
- 模态框/抽屉必须锁定焦点，Esc 关闭，关闭后焦点回到触发控件。
- 快捷键只在相关工作区生效，不能像当前卡片快捷键一样全局劫持输入。

### 6.6 桌面与窄屏

- 主要创作目标为桌面端，1280px 以上提供完整三栏。
- 900–1279px 使用主区 + 可切换检查器。
- 小于 900px 提供项目查看、审批、轻编辑；复杂地图和大纲批量编辑提示转到桌面。
- 所有固定工具条和按钮需有稳定尺寸，避免状态文本变化导致布局跳动。

---

## 7. 前端目标架构

### 7.1 迁移策略

**阶段 A：不改构建方式先修主链路。**

- 继续使用当前静态入口。
- 先创建唯一的 `api-client`、`task-session`、`polling-controller` 和 `workspace-persistence`。
- 删除 `main.js` 内重复实现后再拆 UI。

**阶段 B：迁移 Vue 3 + Vite + TypeScript。**

- 新建 `frontend/`，FastAPI 生产环境挂载构建后的静态目录。
- `/write-ui-v2` URL 保持不变。
- 迁移期间按工作区逐个替换，设置功能等价门禁，不进行一次性重写。

### 7.2 建议目录

```text
frontend/
  src/
    api/
      client.ts
      contracts.ts
      task-service.ts
      outline-service.ts
      world-service.ts
      analysis-service.ts
    stores/
      workspace.ts
      task-session.ts
      activity.ts
      notifications.ts
    composables/
      useTaskStream.ts
      useAutosave.ts
      useAsyncState.ts
      useVersionConflict.ts
    layouts/
      WorkspaceShell.vue
    views/
      WritingView.vue
      OutlineView.vue
      WorldView.vue
      AnalysisView.vue
      ProjectView.vue
    modules/
      outline/
      draft/
      characters/
      relations/
      factions/
      map/
      items/
      foreshadowings/
      subplots/
      state-frame/
    components/
      AsyncPanel.vue
      ActivityCenter.vue
      SaveIndicator.vue
      ConfirmDialog.vue
      EmptyState.vue
    styles/
      tokens.css
      layout.css
      states.css
```

### 7.3 API Client 规范

- 所有 API 都通过领域 service 调用，页面组件不得手写 URL。
- `request()` 支持 JSON、Blob、204、超时、AbortSignal 和 request ID。
- 对 401/403、404、409、422、429、5xx 提供统一解析。
- 对任务状态、StateFrame、outline、entity 等关键响应进行运行时 schema 校验。
- OpenAPI 作为契约来源，可生成 TypeScript 类型，但人工维护用户友好的领域模型。

### 7.4 状态所有权

- `workspace store`：项目级主题、设定、当前工作区。
- `task session store`：当前运行 task、任务链、stream cursor、连接状态。
- 各领域 store：只管理自身实体和缓存。
- 临时表单状态留在组件内，不进入全局 store。
- 服务器事实优先，localStorage 只保存 UI 偏好和未提交草稿，不再冒充完整数据库。

---

## 8. 需要新增或调整的后端 API

### 8.1 P0 契约

| API | 目的 | 备注 |
|---|---|---|
| `GET /tasks/{task_id}/workspace` | 聚合加载项目基本信息 | 返回主题、设定、当前 task、outline 摘要和版本 |
| `PATCH /tasks/{task_id}/workspace` | 保存主题、世界观、梗概、风格和字数设置 | 支持 draft/running/paused，字段白名单 |
| `POST /tasks/{task_id}/draft/beacon` | 页面关闭时兜底保存 | 幂等、体积限制、接受 JSON Blob |
| `POST /tasks/{task_id}/outline/evaluate` | 写前大纲逻辑评估 | 与写后 continuity 分开 |
| `GET /tasks/{task_id}/exports` | 导出记录 | 不暴露本地文件路径 |
| `POST /tasks/{task_id}/exports` | 创建导出 | format、range、include_metadata |
| `GET /tasks/{task_id}/exports/{export_id}/download` | 下载导出 | 使用文件响应 |

### 8.2 P1/P2 契约

| API | 目的 |
|---|---|
| `GET /tasks/{task_id}/state-frames?from=&to=&limit=` | StateFrame 时间线列表 |
| `GET /tasks/{task_id}/state-frame/latest` | 当前最新状态摘要 |
| `PATCH /tasks/{task_id}/draft/sections/{section}/{subsection}` | 保存人工编辑的小节正文 |
| `GET /tasks/{task_id}/draft/versions` | 正文版本列表 |
| `POST /tasks/{task_id}/draft/versions/{version_id}/restore` | 恢复正文版本 |
| `PUT/DELETE /api/map/nodes/{node_id}` | 地图节点完整编辑 |
| `PUT/DELETE /api/map/edges/{edge_id}` | 地图连线完整编辑 |
| `DELETE /api/items/{item_id}` | 物品退役或软删除 |
| `POST /tasks/{task_id}/analysis/jobs` | 统一启动长分析任务并返回 job ID |
| `GET /tasks/{task_id}/activity` | 用户可理解的任务活动时间线 |

### 8.3 API 统一要求

- 统一错误结构：`{error, detail, retryable, fields?, request_id?}`。
- 写操作返回最新 `version`、`updated_at` 和受影响实体。
- 409 专门表示版本冲突、候选 stale 或任务状态不允许。
- 分页列表统一使用 `items/total/next_cursor`。
- 聚合端点只用于减少首屏瀑布请求，领域写操作仍保持独立。
- P3A 运维 CLI 不直接暴露给普通前端；页面只消费只读的任务级提交与同步状态。管理员操作必须等鉴权和审计边界完成后单独建设。

---

## 9. 分阶段实施计划

## Phase 0：主链路稳定化

**目标**：在不改视觉架构的前提下，让现有页面的创建、保存、生成、审批、恢复和失败处理可靠。

**前端任务**

- 修复 `new_task_id` 切换。
- 拆分 `resetRunState` 与 `resetWorkspace`。
- 移除不存在的 `saveWorldSetting` 和 `/api/analysis/evaluate` 调用。
- 建立统一 API Client、错误解析和轮询控制器。
- 修复 beacon 保存。
- 显示 `commit_status`、`critical_projection_status`、`non_blocking_projection_status`。
- 给所有长操作增加 loading、disabled、retry 和取消状态。

**后端任务**

- 增加 workspace patch、outline evaluate、beacon 和 export 契约。
- 为关键接口补充结构化错误。

**验收**

- 自动和交互两种模式都能从创建任务走到完成。
- 审批后页面自动跟随新任务继续流式显示。
- 写作前创建的角色、势力、地图、物品和大纲与写作任务保持同一作用域。
- 网络中断、API Key 错误、429 和 Redis 短暂不可达都有用户可理解的反馈。

## Phase 1：工作台壳与写作/大纲升级

**目标**：替换顶部功能按钮和模态框中心化结构，建立主要工作区。

**任务**

- 建立项目栏、工作区标签、动态检查器、任务运行条。
- 迁移写作工作区和大纲工作区。
- 加入正文编辑、自动保存、选中修订、版本对比。
- 大纲加入批量选择、拖拽、版本 diff、问题定位和批量应用建议。
- 将篇幅建议和角色弧确认整合进大纲检查器。

**验收**

- 用户完成核心写作流程不需要打开超过两个模态框。
- 当前章节、当前任务、保存状态和下一步动作在首屏可见。
- 大纲恢复前可预览变更，正文修订可回退。

## Phase 2：世界工作区

**目标**：把分散实体管理升级为可关联、可追踪的故事世界模型。

**任务**

- 迁移人物、关系、势力、地图、物品、伏笔。
- 接入势力成员/关系、角色关系阶段、地图边/路线、物品 transaction、伏笔 update/delete/chapter context。
- 统一实体详情、关联章节、关联事件和大纲引用。
- 卡片采纳改成“变更预览 + 一次提交 + 失败回滚”。

**验收**

- 任一实体可查看其相关人物、章节、事件和状态变化。
- 地图路线、物品流转和关系阶段可被编辑并回显。
- 卡片采纳不会出现部分实体成功、部分失败但 UI 仍显示全部成功。

## Phase 3：分析与 StateFrame

**目标**：将后端运行时和质量能力转化为作者可以行动的诊断工具。

**任务**

- 接入连续性、叙事事件图、StateFrame、支线热力、写后分析。
- 建立质量总览和问题定位。
- 从审阅/检测/连续性问题直接创建修订任务。
- 提供 before/after/delta 可视化和来源说明。

**验收**

- 分析结果可以定位到章节/小节/正文片段。
- 每条问题至少有“查看上下文”或“发起修订”动作。
- StateFrame 能按小节浏览，且区分持久化记录与 live reconstruction。

## Phase 4：项目、恢复与导出

**目标**：完成从“单次生成页面”到“长期写作项目”的闭环。

**任务**

- 历史任务升级为项目列表和详情页。
- 接入安全导出、导出历史和下载。
- 展示 task chain、错误恢复、继续写作和版本状态。
- 增加复制、归档和软删除策略。

**验收**

- 浏览器刷新、服务重启后能从服务器恢复项目，而不是只依赖 localStorage。
- 用户能明确区分活跃、等待确认、暂停、失败、完成和归档项目。
- Markdown/TXT/JSON 导出可下载且内容与当前 revision 一致。

## Phase 5：工程化、性能与可访问性

**目标**：完成 Vite/TypeScript 迁移和质量门禁。

**任务**

- 完成组件化和类型化，删除旧入口与未加载资源。
- 固定依赖版本并实现离线可启动。
- 优化流式渲染、列表虚拟化、聚合请求和缓存失效。
- 完成键盘、焦点、对比度、窄屏和错误边界。

**验收**

- `main.js` 不再承担领域业务；无第二套未加载轮询/持久化实现。
- 首屏不依赖公共 CDN。
- 关键 E2E、API contract、可访问性和性能检查进入 CI。

---

## 10. 测试计划

### 10.1 API 契约测试

- 使用 FastAPI TestClient 覆盖 workspace、task transition、outline evaluate、StateFrame list 和 export。
- 对 `/status` 保留旧字段兼容性，同时断言 Canon/Projection 字段。
- 409、422、429、500 的前端错误映射必须有固定测试样例。

### 10.2 前端单元测试

- Task session：旧任务到新任务的切换、cursor 重置、任务链保留。
- Outline commands：拖拽、批量字数、删除撤销、版本 diff。
- API Client：超时、取消、Blob、错误 detail、空响应。
- Autosave：debounce、失败重试、版本冲突和页面离开兜底。

### 10.3 Playwright 关键流程

1. 创建草稿 → 编辑大纲和世界实体 → 开始写作 → 验证同一 workspace。
2. 交互模式 → 批准大纲 → 收到新 task ID → 继续流式生成。
3. 断开后端 → 页面进入重连 → 恢复后不重复正文 token。
4. 写作完成 → 查看审阅 → 发起小节修订 → 对比并采用版本。
5. 查看 StateFrame → 从 delta 跳转正文和相关实体。
6. 历史恢复 → 导出 Markdown → 验证 revision 和正文一致。

### 10.4 视觉与可访问性

- 桌面 1440×900、1280×720、窄屏 1024×768、移动 390×844 截图基线。
- 检查按钮文字、工具栏、模态框、长实体名和错误信息不重叠。
- 键盘完成项目切换、工作区切换、保存、确认和关闭抽屉。
- axe 检查无严重级可访问性错误。

### 10.5 性能门槛

- 流式 token 以 50–100ms 批次更新 DOM，不按单 token 全量渲染。
- 后台轮询空闲时退避，页面隐藏时降低频率。
- 不再每 5 秒无条件刷新全部世界实体；按事件失效或按面板激活加载。
- 大纲或实体超过 500 项时使用虚拟列表或分段渲染。

---

## 11. 发布与回滚策略

- 每个 Phase 使用功能开关，保留旧入口直到对应工作区通过功能等价测试。
- 先在 `/write-ui-v3` 验证新壳，稳定后将 `/write-ui-v2` 重定向到新版本；避免原地一次性替换。
- API 先加后弃，至少跨一个发布周期保留旧调用。
- 任务主链路修复必须单独发布，不与大规模视觉重构绑定。
- 数据写入接口都保留幂等键或版本号，避免双击和重试造成重复实体。
- 回滚前端时不得回滚 Canon 数据；旧页面无法识别的新字段应安全忽略。

---

## 12. 优先级与最终完成定义

### 必须先做

1. 修复 task ID 和 workspace 锚点。
2. 修复不存在/错误的方法和路由。
3. 建立统一错误、轮询和自动保存。
4. 接入提交与关键投影状态。
5. 提供真实正文导出。

### 高价值新增

1. 正文编辑、定向修订和版本对比。
2. StateFrame 状态变化检查器。
3. 连续性审查与叙事事件图。
4. 地图路线、物品流转、关系阶段、支线热力图。
5. 项目级恢复和导出中心。

### 完成定义

本轮前端升级只有在以下条件同时满足时才算完成：

- 核心写作流程在自动和交互模式下均通过 E2E。
- 前端不再调用不存在的 API，后端已有高价值能力均有明确入口或被明确延期。
- 用户可以看到保存、生成、等待确认、重连、失败、提交和同步状态。
- 页面从模态框集合升级为五个可导航工作区。
- 正文、大纲和世界实体具备可靠恢复路径。
- 审阅、连续性、StateFrame 和事件分析都能导向具体修订动作。
- 当前未加载的旧前端模块和重复实现已删除，不再形成维护歧义。
- API contract、Playwright、视觉和可访问性检查进入持续验证流程。
