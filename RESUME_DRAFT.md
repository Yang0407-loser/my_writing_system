# Resume - 技术栈 & 项目经历

> 针对 AI 应用开发 / 大模型应用 / Agent 开发 岗位

---

## 技术栈

**编程语言**：Python, JavaScript, SQL

**后端框架**：FastAPI, LangChain, Celery

**数据库 & 存储**：ChromaDB（向量检索）, SQLite, Redis（消息队列 & 状态黑板）

**AI / LLM**：Prompt Engineering, Agent 编排, Tool Calling, RAG 检索增强生成, BGE-M3 Embedding

**语音处理**：whisperX（ASR + VAD + 说话人分离 + 情感标注）, ffmpeg

**前端**：Streamlit, Vue 3 (CDN), Chrome Extension Manifest V3

**工程工具**：Docker, Git, pytest, uv

---

## 项目经历

### 多智能体协作写作系统 — WriterAgent

*2026.03 ~ 2026.06 | 独立开发*

解决 LLM 长文本生成中上下文遗忘、角色一致性漂移和伏笔回收三大难题。

- **Agent 编排**：设计并实现 10 Agent 串行流水线，Redis 黑板解耦通信 + Coordinator 统一调度，串行设计天然规避黑板并发竞态，Agent 间低耦合可独立替换
- **Prompt Engineering**：设计 50 维风格分析→自然语言简报的二阶段 prompt 链路，Writer 融合交接笔记、风格简报、RAG 检索、角色上下文、世界状态及规则约束六大信息源生成正文
- **继承制写作**：Writer 输出纯正文后独立 LLM 调用提取结构化交接数据（伏笔/人物状态/开放线索），章节间通过交接笔记链传递关键信息，实现跨章节连贯性
- **上下文管理**：ChromaDB 语义检索从已写段落召回相关内容，ContextManager 维护运行摘要（旧内容压缩 + 最近原文保留），缓解长文本窗口溢出
- **世界状态**：规则层正则预过滤（零 LLM 成本）+ LLM 交叉验证双层事实验证，实体交集 → LLM 精判矛盾检测，不一致时 warning 注入而非硬阻断
- **Agent 角色设计**：下游 Agent 设为"笔记员"而非"裁判"——只记录事实、标注提醒，不做强制裁决，最终决策权保留给用户，避免 Agent 越权替代创作者判断
- **统一事件模型**：将角色弧线、伏笔线索、世界事实统一建模为 NarrativeEvent，按重要性×紧迫度全局排序填充上下文窗口——伏笔密集回收的章节自动获得更多空间，角色弧线无推进的章节自动让位，替代按类型固定预算的僵化分配
- **容错与恢复**：Redis Stream 流式推送 + 流式失败自动 fallback 非流式；每节检查点保存 Redis，支持断点恢复与人工审批
- **技术栈**：FastAPI + Celery + Redis + ChromaDB + SQLite + DeepSeek V4 Pro，部署于阿里云 ECS

### AI 会议信息管理系统 — MeetingAgent

*2025.06 ~ 至今 | 独立开发*

端到端的智能会议处理系统，支持音频/视频自动转写、Agent 编排分析、跨会议知识关联、一键导出 Obsidian。

- **Agent 编排架构**：设计 Coordinator Agent，LLM 自主决策调用 8 个 Tool（转录摘要、实录生成、待办提取、历史检索、说话人画像、概念关联分析等），替代传统线性 pipeline
- **语音处理 & 情绪感知**：集成 whisperX 实现 VAD 切分 + 说话人分离 + 情感标注（HAPPY/SAD/ANGRY/NEUTRAL），输出带说话人和情绪标签的结构化转录
- **双层检索 + 知识关联**：ChromaDB 向量检索历史会议 + LLM 语义推理，自动生成横向拓展（概念关联）、纵向深入（前置/进阶知识）、批判视角（边界条件质疑）三个维度的分析
- **SQLite 待办追踪**：自动提取待办项并持久化，新会议自动带出上次未完成的待办，支持按负责人筛选
- **Chrome 浏览器插件**：开发 Manifest V3 扩展，B站/YouTube 视频页一键触发，自动下载音频 → 转写 → 生成结构化笔记 → 导出 Obsidian
- **Obsidian 知识库集成**：自动生成 YAML frontmatter + 情感时间线（Mermaid）+ 概念关联段落 + [[双链语法]]，无缝对接 Obsidian
- **技术栈**：FastAPI + LangChain + Streamlit + ChromaDB + SQLite + Chrome Extension
