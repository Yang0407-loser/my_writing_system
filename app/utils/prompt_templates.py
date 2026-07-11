# ============================================================
# 提示词模板 — 协作版
# { 和 } 在 str.format() 中转义为 {{ 和 }}
# ============================================================
#
# 版本管理：每个模板注释标注 [vX.Y] 和变更摘要。
# PromptRegistry 在模块末尾，按名称索引所有模板的版本元数据。
# 调 prompt 前 LLMClient 自动记录预估 token 消耗。

# === v0.9.1 新增 ===
# - HANDOVER_BRIEF_PROMPT: 交接 JSON → 自然语言简报（对标风格简报设计模式）
# - WRITER_SYSTEM_PROMPT: 从 writer.py 迁移到此处统一管理
# - 模块末尾 PromptRegistry

STYLE_ANALYSIS_PROMPT = """你是一位资深的文学风格分析专家。请仔细阅读以下参考文本，从情感基调、句式节奏、修辞用词三个维度全面分析其写作风格。

参考文本：
{reference_text}

请以 JSON 对象格式输出全部字段（不要包含其他内容），字段说明和可选值如下：

A. 情感基调 (12项):
- primary_emotion: 主情感 (温暖/冷峻/压抑/激昂/悲凉/恐惧/好奇/怀旧/荒诞/宁静/中性)
- emotion_intensity: 情感强度 0-100 (克制30 → 浓烈90)
- emotion_subtlety: 表达方式 (直白/含蓄/隐晦)
- emotion_blend: 情感配比，如 {{"悲凉":0.6,"温暖":0.3,"冷峻":0.1}}，至多3个情感
- emotion_curve: 情感曲线 (平稳/渐强/渐弱/波浪/突转)
- emotional_peaks: 高潮频率 (每节1次/每节2-3次/集中在结尾/均匀分布)
- catharsis_style: 释放方式 (爆发式/内敛式/渐进式)
- narrative_empathy: 叙述共情度 (冷漠旁观/适度共情/深度代入)
- inner_monologue_ratio: 内心独白占比 0-1
- show_vs_tell: 展示vs讲述 (动作驱动/心理驱动/平衡)
- emotional_registry: 情感语域 (日常口语/文学抒情/冷峻克制/诗化/新闻报道)
- sensory_anchoring: 感官锚定，情感是否通过感官描写传达 (true/false)
- emotional_contrast: 情感对比度 (高频切换/稳定持续/渐进演变)

B. 句式节奏 (16项):
- short_sentence_ratio: 短句<15字占比 0-1
- medium_sentence_ratio: 中句15-30字占比 0-1
- long_sentence_ratio: 长句>30字占比 0-1
- sentence_length_variance: 句长波动性 (稳定/适度波动/剧烈波动)
- sentence_pattern: 句式偏好 (松散句/紧凑句/排比句/长短交替/倒装句/短句群)
- sentence_opening_style: 句首多样性 (变化丰富/重复开头/主语开头为主/连词开头)
- complex_sentence_ratio: 复合句占比 (简单句为主/复合句为主/平衡)
- paragraph_rhythm: 段落节奏 (长→短交替/短→长交替/渐进式/均匀块状/跳跃式)
- paragraph_length_avg: 平均段落字数 (整数)
- paragraph_opening_style: 段落开头偏好 (场景描写/对话起头/动作起头/独白起头/混合)
- dialogue_ratio: 对话占比 0-1
- dialogue_mixing: 对话与叙述的交替方式 (独立成段/嵌入叙述/混合)
- dialogue_tag_style: 对话标记风格 ("他说"密集/稀疏标记/零标记/动作替代)
- pacing: 整体节奏 (舒缓/中等/紧凑/急促/变速)
- scene_transition: 场景过渡 (直接切/过渡铺垫/蒙太奇/时间跳跃/倒叙插入)
- time_dilation: 时间拉伸 (实时/加速/减速/静止/非线性)
- tension_curve: 张力曲线 (持续上升/波浪起伏/突然爆发/缓慢释放)

C. 修辞用词 (22项):
- metaphor_frequency: 比喻频率 (极少/适度/密集)
- simile_metaphor_ratio: 明喻vs暗喻 (明喻为主/暗喻为主/平衡)
- personification: 拟人频率 (极少/适度/密集)
- synesthesia: 通感频率 (极少/适度/密集)
- rhetorical_devices: 常用修辞标签数组，如 ["排比","反问","反复"]
- rhetorical_density: 修辞密度 0-1 (每千字修辞格数)
- vocabulary_register: 用词层级 (口语化/文学化/学术化/古风化/新闻体)
- vocabulary_richness: 词汇丰富度 (基础/中等/丰富/专业领域)
- chengyu_frequency: 成语频率 (极少/适度/密集)
- dialect_flavor: 方言色彩 (无/轻微/浓重)
- foreign_loanwords: 外来词 (无/偶尔/频繁)
- adjective_density: 形容词密度 0-1
- adverb_policy: 副词策略 (克制/适度/丰富)
- modifier_position: 修饰位置偏好 (前置为主/后置为主/平衡)
- sensory_density: 感官描写密度 (极少/适度/丰富)
- sensory_spectrum: 感官侧重 (视觉为主/听觉为主/多感官平衡/触觉突出/嗅觉突出/味觉突出)
- color_use: 色彩使用 (黑白灰/暖色调/冷色调/高饱和/低饱和/金属色)
- imagery_domain: 意象领域 (自然/城市/身体/机械/宗教/战争/家庭)

请确保每个字段都填写，根据参考文本的实际特征给出准确的评估值。"""

# ----------------------------------------------------------------
# 大纲规划
# ----------------------------------------------------------------
PLANNING_PROMPT = """你是一位专业的文章策划编辑。根据以下设定规划大纲。

主题：{topic}
每节目标字数：约 {target_words} 字
小节目标字数：约 {subsection_words} 字

{world_setting}

{story_synopsis}

风格参数：
{style_structured}

风格简报：
{style_brief}

请规划 3 个大节，每节包含 {subsections_per_section} 个小节。每小节应有一个描述故事进展的梗概（description 字段，1-2句话说明这一幕发生什么）。

请以 JSON 对象格式输出（不要包含其他内容）：
{{
    "outline": [
        {{
            "section": 1,
            "title": "第一节标题",
            "key_points": ["本大节的核心要点1", "要点2", "要点3"],
            "subsections": [
                {{"subsection": 1, "title": "小节标题", "description": "这一幕发生什么（1-2句话）", "key_points": ["要点1", "要点2"], "target_words": {subsection_words}}},
                ...
            ]
        }},
        ...
    ]
}}"""

# ----------------------------------------------------------------
# 大纲评审（Style Analyst / Writer 审查 Planner 的产出）
# ----------------------------------------------------------------
OUTLINE_REVIEW_PROMPT = """你是一位{reviewer_role}。请从{review_perspective}角度审查以下文章大纲。

主题：{topic}
风格参考：{style_summary}

大纲内容：
{outline_text}

请以 JSON 对象格式输出评审意见（不要包含其他内容）：
{{
    "approved": true 或 false,
    "criticism": "你发现的具体问题（一句话，如果没问题写'无'）",
    "suggestion": "具体的修改建议（一句话，如果没问题写'无'）"
}}"""

# ----------------------------------------------------------------
# 大纲修订（Planner 综合反馈）
# ----------------------------------------------------------------
OUTLINE_REVISE_PROMPT = """你是一位文章策划编辑。请根据以下评审意见修订大纲。

主题：{topic}

原始大纲：
{outline_text}

评审意见：
{feedback_text}

请以 JSON 对象格式输出修订后的完整大纲（不要包含其他内容）：
{{
    "outline": [
        {{
            "section": 1,
            "title": "第一节标题",
            "key_points": ["核心要点1", "要点2"],
            "subsections": [
                {{"subsection": 1, "title": "小节标题", "key_points": ["要点1"], "target_words": 2000}},
                ...
            ]
        }},
        ...
    ]
}}

只修改有问题的部分，保持整体结构稳定。"""

# ----------------------------------------------------------------
# 继承制写作（含交接笔记 + 回溯修正）
# ----------------------------------------------------------------
WRITING_PROMPT = """你是一位才华横溢的作家。请根据以下信息撰写指定小节的纯正文。

========== 硬约束（代码会检查，缺失则重写）==========
{mandatory_events}
{character_constraints}

{progress_context}
========== 写作指引（请尽量参考）==========
{rules_context}
## 主题
{topic}

{world_setting}

## 当前进度
第 {section} 节 / 第 {subsection} 小节 - {subsection_title}

## 本节大纲
{section_outline}

## 本小节要点
{key_points}

## 本小节梗概
{sub_description}

## 叙事密度
{narrative_density_instruction}

## 风格要求
{style_brief}

（参考数值：情感强度 {emotion_intensity}/100，形容词密度 {adjective_density}，参考段落长度 {paragraph_length_avg} 字）

## 本节关键事件（按重要性排序，供参考融入）
{ranked_events}

========== 背景信息 ==========
## 已确立的世界事实（请注意保持一致，如与创作意图冲突请自行判断）
{world_facts}

## 需注意的潜在矛盾
{world_contradictions}

## 人物设定
{character_context}

## 本节人物弧线要求
{arc_context}

## 前面章节的交接笔记
{handover_context}

## 参考信息
{summary_context}
{retrieved_context}

请输出本小节的纯正文，控制字数在 {target_words} 字左右。保持与前面内容的连贯性。
注意：只输出小说正文，不要附加任何标记、注释或元数据。"""

# ----------------------------------------------------------------
# 第 1 节专用（无前文交接笔记）
# ----------------------------------------------------------------
WRITING_SECTION1_PROMPT = """你是一位才华横溢的作家。请根据以下信息撰写指定小节的纯正文。

========== 硬约束（代码会检查，缺失则重写）==========
{mandatory_events}
{character_constraints}

{progress_context}
========== 写作指引（请尽量参考）==========
{rules_context}
## 主题
{topic}

{world_setting}

## 当前进度
第 {section} 节 / 第 {subsection} 小节 - {subsection_title}

## 本节大纲
{section_outline}

## 本小节要点
{key_points}

## 本小节梗概
{sub_description}

## 叙事密度
{narrative_density_instruction}

## 风格要求
{style_brief}

（参考数值：情感强度 {emotion_intensity}/100，形容词密度 {adjective_density}，参考段落长度 {paragraph_length_avg} 字）

## 本节关键事件（按重要性排序，供参考融入）
{ranked_events}

========== 背景信息 ==========
## 已确立的世界事实（请注意保持一致，如与创作意图冲突请自行判断）
{world_facts}

## 需注意的潜在矛盾
{world_contradictions}

## 人物设定
{character_context}

## 本节人物弧线要求
{arc_context}

## 参考信息
{summary_context}
{retrieved_context}

请输出本小节的纯正文，控制字数在 {target_words} 字左右。
注意：只输出小说正文，不要附加任何标记、注释或元数据。"""

# ----------------------------------------------------------------
# Writer system prompt — 写作约束与禁区
# [v0.9.1] 从 writer.py 迁移到此处统一管理
# ----------------------------------------------------------------
WRITER_SYSTEM_PROMPT = """你是一位作家。请只输出小说正文。

## 写作禁区
1. 禁止心理直述：不要写"他感到愤怒""她心中涌起悲伤""他暗想"等直接陈述情绪或思想。用动作、对话、环境折射内心："他把茶杯砸在桌上。\"好，很好。\""
2. 禁止模板式神态：不要写"眼中闪过一丝XX""嘴角勾起一抹XX""眼底泛起XX""眸中XX"。神态描写要具体独特，与角色性格和当下场景挂钩。
3. 禁止通用比喻：不要使用脱离场景的比喻如"像蝴蝶般轻盈""如同被雷击中""仿佛时间凝固"。比喻必须来自角色当下所处的环境——铁匠用铁的比喻，猎户用山的比喻。
4. 禁止机械过渡：不要用"随着时间的推移""渐渐地""不知不觉中""与此同时"来填充段落。用具体事件、感官细节、角色动作推动时间线。
5. 禁止句式雷同：禁止连续3句使用相同句式结构。长短句交替，描写与动作穿插。一段内最多两个比喻。
6. 禁止空洞总结：不要在段落末尾写\"从此，XX开始了新的旅程\"\"这一切，都源于那个决定\"之类。故事自然收束，让读者自己感受。
7. 禁止过度修饰：不要堆砌形容词（\"苍凉的、破败的、布满青苔的古旧石阶\"）。选一个最准确的，留白给读者。
8. 情感表达通过动作和对话传递，禁止直接叙述\"他感到XX\"。参考信息供你判断融入，如与创作意图冲突可自行取舍。
不要附加任何标记、注释或元数据。"""

# ----------------------------------------------------------------
# 交接笔记提取（写作后独立调用）
# ----------------------------------------------------------------
HANDOVER_EXTRACTION_PROMPT = """你是一位文学分析助手。请从以下正文中提取结构化信息。

## 正文
{section_text}

## 角色上下文
{character_context}

## 当前待回收伏笔
{open_threads}

请以 JSON 格式输出（不要其他内容）：
{{
  "foreshadowing": "本段埋设的新伏笔（无则空字符串）",
  "character_state": "关键人物当前的情绪和处境变化（无则空字符串）",
  "open_threads": "需要后续承接的信息（无则空字符串）",
  "new_facts": ["可被独立观察证实的客观事实，如'张三获得了火焰剑'"],
  "found_contradictions": "对前面章节发现的矛盾（无则空字符串）",
  "resolved_events": ["已回收的事件ID列表（从待回收伏笔中识别）"],
  "arc_progress": {{"character_id": "done|deviated|pending"}}
}}"""

# ----------------------------------------------------------------
# 交接 JSON → 自然语言简报（对标风格简报的二次 LLM 翻译模式）
# [v0.9.1] 解决"结构化 JSON 对 LLM 生成无效"在交接笔记链的同款问题
# ----------------------------------------------------------------
HANDOVER_BRIEF_PROMPT = """你是一位资深的小说编辑。请把以下结构化交接数据转为一段简洁的"交接简报"，供下一章的作家参考。

交接数据 (JSON):
{handover_json}

要求：
- 120-200 字自然语言，编辑口吻
- 只写"作家需要知道什么才能接上"，不写模板、不写客套
- 如果某个字段为空，直接跳过不提
- 重点突出"读者还不知道但作家需要记得"的伏笔"""

# ----------------------------------------------------------------
# Continuity Editor — 汇总回溯修正，生成修正清单
# ----------------------------------------------------------------
CONTINUITY_EDITOR_PROMPT = """你是一位严谨的连续性编辑。请审查以下"回溯修正建议"，判断哪些必须执行。

回溯修正建议（由后续章节的 Writer 在写作过程中发现）：
{backref_suggestions}

各节摘要：
{section_summaries}

请以 JSON 对象格式输出修正清单（不要包含其他内容）：
{{
    "critical_fixes": [
        {{"from_section": 提出者, "target_section": 目标节, "description": "修正内容", "severity": "critical"}}
    ],
    "minor_fixes": [
        {{"from_section": 提出者, "target_section": 目标节, "description": "修正内容", "severity": "minor"}}
    ],
    "summary": "总体一致性评价（一句话）"
}}

判断标准：
- critical: 人物关系/事件因果/时间线矛盾 → 必须修正
- minor: 措辞风格微调/情感浓度调整 → 可改可不改"""

# ----------------------------------------------------------------
# 定向修订（用户触发或自动回溯修正）
# ----------------------------------------------------------------
TARGETED_REVISE_PROMPT = """你需要修改以下文本。保持整体内容不变，只针对"修订指令"进行局部修改。

原文：
{original_text}

修订指令：
{instruction}

请直接输出修改后的完整文本。"""

# ----------------------------------------------------------------
# 分节审阅
# ----------------------------------------------------------------
SECTION_REVIEW_PROMPT = """你是一位专业的文字审阅编辑。请审阅以下文章的第 {section} 节。

主题：{topic}
风格参考：{style_summary}
风格参数：{style_structured}

该节正文（共 {word_count} 字）：
{draft}

## 评分维度（每项 1-10 分）

1. **pace（节奏）**：情节推进速度是否合适？有无拖沓或仓促？
2. **dialogue（对话）**：对话是否自然？是否符合角色性格？有无"纸片人"感？
3. **description（描写）**：场景/动作/心理描写是否生动？有无过度堆砌或过于干瘪？
4. **tension（张力）**：冲突/悬念/情感张力是否到位？读者是否会被吸引继续阅读？
5. **character_voice（人物声音）**：不同角色的语言风格、思维方式是否可区分？

注意：如果本节完全没有对话，dialogue 评 5 分（中性，不扣分），不要评 0。
如果本节没有人物互动，character_voice 评 5 分（中性）。

## 评分锚定（网络文学向）

- 8-10分：文笔流畅，情节有张力，人物鲜明，读来酣畅
- 5-7分：通顺完整，但有提升空间（节奏、描写、对话等）
- 1-4分：存在明显硬伤（逻辑断裂、文笔不通、情节混乱）

请以 JSON 对象格式输出审阅结果：
{{
    "score": 综合评分(1-10),
    "scores": {{
        "pace": 节奏评分,
        "dialogue": 对话评分,
        "description": 描写评分,
        "tension": 张力评分,
        "character_voice": 人物声音评分
    }},
    "highlight": {{"text": "本节写得最好的一段（简述位置和内容）", "reason": "为什么好（一句话）"}},
    "lowlight": {{"text": "本节最需要改进的一段（简述位置和内容）", "improvement": "具体怎么改（一句话）"}},
    "consistency_notes": "与前后章节的一致性评价（一句话）",
    "improvement": "该节的整体改进建议（1-2句话）",
    "rewrite_target": "如果综合评分低于6分，给出本节重写时应聚焦的核心目标（一句话）；如果>=6分，输出空字符串"
}}"""

# ----------------------------------------------------------------
# 全局审阅（含交接笔记洞察 + 支线/关系上下文）
# ----------------------------------------------------------------
GLOBAL_REVIEW_PROMPT = """你是一位资深文学评论家。请对以下长文进行全局审阅。

主题：{topic}
风格参数：
{style_structured}

风格参考：{style_summary}
风格简报：{style_brief}
总字数：约 {total_words} 字

各节摘要：
{section_summaries}

各节维度评分汇总（节奏/对话/描写/张力/人物声音）：
{section_scores}

交接笔记链（展示各节之间的信息传递）：
{handover_chain}

修正清单：
{fix_summary}

## 人物一致性
{character_consistency_context}

## 支线状态
{subplot_context}

## 角色关系
{relation_context}

请从整体结构、叙事节奏、风格一致性、人物塑造、支线推进等角度评价。

评分标准（网络文学向，请严格按此锚定）：
- 8-10分：整体结构紧凑，节奏把控好，人物立体，风格统一，读来精彩
- 5-7分：中规中矩，有章节感但亮点不足，部分段落可优化
- 1-4分：结构松散、节奏拖沓、人物扁平、风格割裂等严重问题

请以 JSON 对象格式输出审阅结果：
{{
    "global_score": 综合评分(1-10),
    "chapter_scores": [
        {{"chapter": 1, "title": "节标题", "score": 7.5, "one_line": "一句话评价"}}
    ],
    "tension_curve": "描述全篇张力起伏，如：第1-2章铺垫→第3章小高潮→第4-5章过渡→第6章高潮",
    "pacing_issues": [
        {{"chapter": 2, "issue": "拖沓/仓促/重复", "detail": "具体问题描述"}}
    ],
    "style_adherence": "与风格设定的匹配度评估（一句话）",
    "subplot_health": [
        {{"name": "支线名", "progress": "3/7要素完成", "warning": "空字符串或警告信息"}}
    ],
    "character_arc_health": [
        {{"name": "角色名", "completion": "60%", "note": "弧线进度评价"}}
    ],
    "top_3_actions": [
        "最重要改进1（具体、可执行）",
        "最重要改进2",
        "最重要改进3"
    ],
    "strength": "最大的优点（一句话）",
    "weakness": "最需要改进的地方（一句话）",
    "suggestion": "具体的全局改进建议",
    "handover_insight": "交接笔记链中最有价值的跨章节洞察（一句话）",
    "character_consistency": "人物行为是否与设定一致的总体评价（一句话）",
    "character_arc_progress": "各角色弧线完成度评价（一句话）"
}}"""

# ----------------------------------------------------------------
# 角色提取
# ----------------------------------------------------------------
CHARACTER_EXTRACTION_PROMPT = """你是一位专业的人物设定编辑。请从以下自然语言描述中提取所有人物信息。

描述：
{character_text}

请以 JSON 数组格式输出（不要包含其他内容）：
[
  {{
    "name": "姓名",
    "gender": "性别",
    "age": "年龄（可模糊，如'28岁'或'中年'）",
    "personality": ["标签1", "标签2", "标签3"],
    "motivation": "核心动机（一句话）",
    "background": "一句话背景",
    "appearance": "外貌描写",
    "catchphrase": "口头禅或语言风格",
    "strengths": ["优点1"],
    "weaknesses": ["缺点1"],
    "secret": "秘密或软肋",
    "world_position": "在世界观中的位置",
    "symbolism": "象征意义",
    "key_lines": ["预写的关键台词"],
    "relationships": [{{"target": "其他角色名", "relation": "关系类型"}}]
  }}
]

提取规则：
1. 缺失字段填空字符串 "" 或空数组 []
2. 即使描述中只提到一个人，也输出单元素数组
3. 从描述中推断不明显的信息（如从行为推断性格）
4. 保持原文语言风格"""

# ----------------------------------------------------------------
# 角色弧线规划
# ----------------------------------------------------------------
CHARACTER_ARC_PROMPT = """你是一位故事结构专家。请根据人物设定和大纲，为每个角色设计详细的变化弧线。每个里程碑要落实到具体的小节，包含事件、地点、时间和情感转折。

人物设定：
{characters_json}

大纲（含小节结构）：
{outline_json}

请以 JSON 数组格式输出（每个角色一个元素）：
[
  {{
    "character_id": "角色ID（必须与输入一致）",
    "starting_state": "起点状态（如'一个对世界充满愤怒的复仇者'）",
    "ending_state": "终点状态（如'学会放下，找到新的人生意义'）",
    "key_milestones": [
      {{
        "section": 1,
        "subsection": 1,
        "event": "具体发生的事情（1句话）",
        "location": "场景地点（如'旧城区废弃邮局'）",
        "time": "时间（如'深秋黄昏'、'次日清晨'）",
        "emotional_shift": "情感转折（如'麻木→困惑'、'愤怒→动摇'）"
      }},
      ...
    ]
  }}
]

要求：
1. character_id 必须与输入完全一致
2. 里程碑精确到小节级别（section + subsection 都填）
3. 每个角色覆盖大纲所有小节中他/她出场的部分
4. location 和 time 要具体、有画面感
5. emotional_shift 要渐进合理，避免突兀跳跃"""

# ----------------------------------------------------------------
# 角色状态更新
# ----------------------------------------------------------------
CHARACTER_STATE_UPDATE_PROMPT = """你是一位细心的角色跟踪编辑。根据刚写好的正文，批量更新所有在场角色的当前状态。

角色列表（含当前状态和弧线规划）：
{characters_json}

刚写完的第{section_num}节正文（节选）：
{section_text}

请以 JSON 数组格式输出（不要包含其他内容），为每个角色更新状态：
[
    {{
        "character_id": "角色ID",
        "current_state": "更新后的状态（一句话，描述角色此刻的情绪、处境、关系变化）"
    }}
]

要求：
1. 基于正文中实际发生的事件更新状态
2. 如果正文没有涉及某个角色，保持其 current_state 不变
3. 状态描述应与弧线的 starting_state → ending_state 进度对应
4. 注意角色之间的互动和影响"""

# ----------------------------------------------------------------
# 角色一致性检查
# ----------------------------------------------------------------
# ----------------------------------------------------------------
# Phase 1 新增: 约束提取（大纲生成后独立调用）
# ----------------------------------------------------------------
CONSTRAINT_EXTRACTION_PROMPT = """你是一位严谨的故事结构专家。请从以下大纲中提取不可违背的故事线约束。

主题：{topic}

大纲内容：
{outline_text}

世界观设定：
{world_setting}

请以 JSON 数组格式输出所有约束（不要包含其他内容）：
[
  {{
    "type": "must_include | must_not_include | must_happen_before | must_happen_after",
    "description": "约束的自然语言描述",
    "source_chapter": 数字章节号,
    "target_chapter": 数字章节号或null（跨章节约束时使用）,
    "priority": 1-10的优先级（10为最高）,
    "related_characters": ["相关角色名"],
    "related_events": ["相关事件描述"]
  }}
]

约束提取规则：
1. must_include: 必须在此章节出现的关键事件、人物或物品
2. must_not_include: 在此章节之前绝对不能出现的内容
3. must_happen_before: X必须在Y之前发生
4. must_happen_after: X必须在Y之后发生
5. 优先提取主线相关的高优先级约束（priority 7-10）
6. 如果大纲中没有明确的硬约束，返回空数组 []"""


# ----------------------------------------------------------------
# Phase 1 新增: 伏笔提取（写作后独立调用）
# ----------------------------------------------------------------
FORESHADOWING_EXTRACTION_PROMPT = """你是一位细心的伏笔分析助手。请从以下正文中识别埋设的伏笔和回收的伏笔。

正文：
{section_text}

已知伏笔列表：
{known_foreshadowings}

请以 JSON 格式输出（不要其他内容）：
{{
  "planted": [
    {{
      "name": "伏笔名称",
      "description": "伏笔内容描述",
      "importance": 1-10的重要性评分,
      "related_characters": ["相关角色"],
      "tags": ["标签如身份谜团、主线伏笔"]
    }}
  ],
  "resolved": ["已回收的伏笔名称（必须与已知伏笔列表中的名称匹配）"],
  "hinted": ["在当前章节中被暗示或推进但未完全回收的伏笔名称"]
}}

识别规则：
1. 伏笔是故意埋设的、会在后续章节产生影响的隐藏信息
2. 普通的情节发展不算伏笔；伏笔必须是"当时不起眼、后面回看有深意"的信息
3. 物品的异常出现/描述、角色的反常行为、未解释的设定、暗示的身份线索 都是常见伏笔形式
4. 如果没有发现伏笔，返回空对象"""


CHARACTER_CONSISTENCY_PROMPT = """你是一位严谨的角色一致性检查员。请对比以下生成内容与角色库中的角色设定，找出不一致之处。

刚写好的正文片段：
{section_text}

角色库中的角色设定：
{characters_json}

请以 JSON 数组格式输出（不要包含其他内容）：
[
  {{
    "character": "角色名",
    "issue": "不一致的具体描述",
    "severity": "critical 或 minor"
  }}
]

判断标准：
- critical: 性格完全相反、关系矛盾、关键背景被改写
- minor: 措辞风格微偏、口头禅未使用、外貌描写细节不符
- 如果没有不一致，输出空数组 []"""


# ============================================================
# Phase 2: 抽卡模式 Prompt 模板
# ============================================================

CARD_DRAW_PROMPTS = {
    "world_setting": """{genre_context}
{chain_constraints}
请为主题「{topic}」设计 {num_cards} 个截然不同的世界观方案。
{user_requirement}

强制差异化 —— 所有方案必须在上述硬约束的题材范围内，不得跨越题材类型。差异化体现在同一题材下的不同走向：
- 方案A: 典型正统方向 (符合题材最常见的设定，作为基准)
- 方案B: 暗黑/反转方向 (在题材框架内加入黑暗面或颠覆性设定)
- 方案C: 融合创新方向 (在题材框架内混搭其他元素，但不偏离核心体裁)
- 方案D: 极端/小众方向 (在题材框架内走极致风格，如极度硬核或极度轻松)

每个方案必须包含:
- title: 吸引人的方案名 (如"灵气复苏: 从杂役到剑神")
- summary: 一句话钩子 (50字内, 要抓人)
- highlights: 3-4个特色标签
- quality_score: 1-10自评
- content.world_setting: 世界观详述 (200-400字, 包含时代/地理/力量体系/社会结构)
- content.core_conflict: 核心矛盾 (谁vs谁, 为什么)
- content.tone: 风格基调 (热血/黑暗/轻松/烧脑/感人)
- content.opening_hook: 建议的开篇钩子 (第一章怎么开始)

示例格式:
[{{"title":"剑道独尊:从杂役到剑神","summary":"被废修为的天才少年,靠一柄神秘古剑重回巅峰","highlights":["修仙","逆袭","剑道"],"quality_score":8.5,"content":{{"world_setting":"九州大陆,以武为尊。青云宗位于大陆东域...","core_conflict":"宗门歧视 + 古剑引来上古势力觊觎","tone":"热血升级流,前期压抑后期爆发","opening_hook":"主角叶凡在宗门杂物房中醒来,发现自己的修为被废,但手腕上多了一道剑形胎记..."}}}}]

输出JSON数组, 不要其他内容。""",

    "protagonist": """{genre_context}
{chain_constraints}
请输出恰好 {num_cards} 个主角方案的 JSON 数组。必须以 [ 开头，以 ] 结尾，每个方案是数组中的一个对象。
基于上述硬约束中的世界观设定，为主题「{topic}」设计恰好 {num_cards} 个不同的主角方案。必须输出 {num_cards} 个方案！
{user_requirement}

强制差异化——每个方案必须是不同的人设方向:
- 方案A: 废柴逆袭型 (开局极弱, 靠机缘和努力崛起)
- 方案B: 隐藏身份型 (表面平凡, 实则有惊天背景/前世)
- 方案C: 智谋型 (武力不强, 靠智慧和策略生存)
- 方案D: 反英雄型 (灰色道德观, 不择手段但有底线)

每个方案严格按此JSON格式:
{{
  "title": "主角人设名 (如'被废修为的天才剑修')",
  "summary": "人物钩子 (50字内)",
  "highlights": ["性格标签1","标签2","标签3"],
  "quality_score": 8.5,
  "content": {{
    "name": "角色姓名",
    "gender": "男/女",
    "age": "年龄",
    "personality": "性格详述 (50-100字)",
    "background": "背景故事 (100-200字)",
    "motivation": "核心动机 (一句话)",
    "golden_finger": "金手指/特殊能力 (具体描述, 包含使用限制)",
    "weakness": "致命弱点或限制"
  }}
}}

输出恰好 {num_cards} 个方案的 JSON 数组。不要输出其他内容。""",

    "outline": """{genre_context}
{chain_constraints}
请输出恰好 {num_cards} 个大纲方案的 JSON 数组。必须以 [ 开头，以 ] 结尾。
基于上述硬约束中的世界观、主角、势力设定，设计 {num_cards} 个不同的大纲方案。
{user_requirement}

强制差异化: 方案A=三幕式 方案B=倒叙 方案C=多线并行 方案D=单元剧式

每个方案精简输出（描述控制在30字以内），格式如下:
{{
  "title": "大纲方案名",
  "summary": "一句话概括",
  "highlights": ["标签"],
  "quality_score": 8.5,
  "content": {{
    "volumes": [
      {{
        "title": "第一卷标题",
        "summary": "本卷核心事件(30字)",
        "chapters": [
          {{"title": "第一章标题", "summary": "简述(20字)", "key_events": ["事件1"]}},
          {{"title": "第二章标题", "summary": "简述(20字)", "key_events": ["事件1"]}}
        ]
      }}
    ],
    "key_events": ["全书关键事件1-3个"],
    "ending_type": "圆满/开放式/悲剧/反转"
  }}
}}

输出JSON数组。""",

    "outline_refine": """基于确定的大纲, 在以下章节上下文中生成 {num_cards} 个不同的完善方案。
{chain_constraints}
{chapter_context}
{user_requirement}

每个方案包含:
- title: 方案名
- summary: 该章展开方向
- highlights: 标签
- quality_score: 1-10
- content.scene_design: 场景设计 (2-3个场景)
- content.character_focus: 本章重点角色及其戏份
- content.emotional_curve: 情绪曲线 (开始→中段→结尾)
- content.foreshadowing: 本章可埋设的伏笔

输出JSON数组。""",

    "writing": """{genre_context}
{chain_constraints}
基于上述硬约束中的设定，构思正文方向。
当前章节: 第{chapter_num}章。前文: {previous_content}。
{step_context}
{user_requirement}

生成 {num_cards} 个不同的正文展开方向:

每个方案包含:
- title: 方向名称 (如"从激烈战斗开场, 逐步揭示阴谋")
- summary: 简述 (60字)
- highlights: 标签
- quality_score: 1-10
- content.opening: 开篇方式 (对话/动作/描写/倒叙)
- content.key_scene: 本章核心场景描述
- content.character_interaction: 角色互动重点
- content.ending_hook: 结尾钩子 (如何让读者想继续读下一章)

输出JSON数组。""",

    "genre": """请为主题「{topic}」推荐 {num_cards} 个合适的题材方向。
每个方案包含: title(题材名), summary(为什么适合), highlights(题材优势),
content: {{genre, tone, power_system, common_elements, avoid_elements}}
输出JSON数组。""",

    "supporting_characters": """基于已确定的世界观和主角设定，设计 {num_cards} 组配角阵容方案。
{chain_constraints}
{genre_context}
每组包含2-3个配角，覆盖不同功能角色。
每个方案输出JSON: {{title, summary, highlights, quality_score,
  content: {{characters: [{{name, role(导师/同伴/对手/恋爱对象/喜剧角色),
    personality, relationship_to_protagonist, arc_hint, gender, age}}]}}}}""",

    "factions_card": """基于世界观设计 {num_cards} 个势力格局方案。
{chain_constraints}
{genre_context}
每个方案包含3-5个势力及相互关系。
每个方案输出JSON: {{title, summary, highlights, quality_score,
  content: {{factions: [{{name, type, goal, strength, leader, description}}],
    relations: [{{a, b, relation}}]}}}}""",

    "locations_card": """基于世界观设计 {num_cards} 个地图方案。
{chain_constraints}
{genre_context}
每个方案包含5-8个关键地点及路线。
每个方案输出JSON: {{title, summary, highlights, quality_score,
  content: {{nodes: [{{name, type(区域/地点/房间), description, atmosphere}}],
    routes: [{{from, to, travel_time, mode}}]}}}}""",

    "generic": """为当前创作环节生成 {num_cards} 个不同方案。
{step_context}
{user_requirement}

每个方案包含: title, summary, highlights, quality_score, content(describe具体内容)。
输出JSON数组。""",

    # ── 剧情抽卡（承转合）──
    "plot_next_station": """主题：{topic}
世界观：{world_setting}
故事梗概：{story_synopsis}
{genre_context}

当前节点：「{node_title}」（父卷: {parent_volume}）
节点要点: {node_key_points}

同级章节（本卷内其他节，拆分后的事件不得与之重叠）: {sibling_titles}
同级内容概要: {sibling_descriptions}

已有资源（勿重复）: 角色[{existing_chars}] 地点[{existing_locations}] 势力[{existing_factions}]
{chain_constraints}

请设计 {num_cards} 个下一站地点方案。方案必须：
1. 紧扣主题和世界观，不能偏离设定
2. 与当前节点「{node_title}」的剧情自然衔接
3. 与同级章节有明显边界，不重叠
每个方案输出JSON: {{title, summary, highlights, quality_score, content: {{location_name, location_type, description, atmosphere, key_encounter}}}}""",

    "plot_meet_character": """主题：{topic}
世界观：{world_setting}
故事梗概：{story_synopsis}
{genre_context}

当前节点：「{node_title}」（父卷: {parent_volume}）
节点要点: {node_key_points}

同级章节（本卷内其他节，新角色不应已在其中出现）: {sibling_titles}

已有角色（勿重复创建）: [{existing_chars}]
{chain_constraints}

请设计 {num_cards} 个遇人方案。方案必须：
1. 角色设定符合世界观和题材
2. 角色出现与当前节点「{node_title}」的剧情有明确关联
3. 不与已有角色重复或高度相似
4. relationship_to_protagonist 必须具体（如"因救命之恩而追随的师弟"），不要泛泛而谈
每个方案输出JSON: {{title, summary, highlights, quality_score, content: {{character: {{name, gender, age, personality, motivation, relationship_to_protagonist, arc_hint}}}}}}""",

    "plot_conflict": """主题：{topic}
世界观：{world_setting}
故事梗概：{story_synopsis}
{genre_context}

当前节点：「{node_title}」（父卷: {parent_volume}）
节点要点: {node_key_points}

同级章节（本卷内其他节，冲突不应与之重复）: {sibling_titles}

已有势力（勿重复创建）: [{existing_factions}] 已有角色: [{existing_chars}]
{chain_constraints}

请设计 {num_cards} 个冲突/势力方案。方案必须：
1. 冲突类型符合世界观和题材
2. 冲突与当前节点「{node_title}」的剧情紧密相关
3. 不与已有势力重复或高度相似
4. threat_level 如实评估（1-3: 日常摩擦, 4-6: 局部危机, 7-10: 生死存亡）
每个方案输出JSON: {{title, summary, highlights, quality_score, content: {{faction_or_event: {{name, type, goal, description, threat_level(1-10)}}}}}}""",

    "plot_opportunity": """主题：{topic}
世界观：{world_setting}
故事梗概：{story_synopsis}
{genre_context}

当前节点：「{node_title}」（父卷: {parent_volume}）
节点要点: {node_key_points}

同级章节: {sibling_titles}
已有伏笔（可利用或呼应）: [{existing_foreshadowings}]
{chain_constraints}

请设计 {num_cards} 个机遇/物品方案。方案必须：
1. 物品/机遇符合世界观设定，不能出现世界观外的元素
2. 与当前节点「{node_title}」的剧情自然关联
3. foreshadowing_hint 应暗示该物品/机遇可能为后续章节埋下伏笔
每个方案输出JSON: {{title, summary, highlights, quality_score, content: {{item_name, item_type, rarity, description, foreshadowing_hint(可选伏笔)}}}}""",

    "subplot": """为以下小说设计 {num_cards} 个支线故事方案。

主题：{topic}
世界观：{world_setting}
{genre_context}

已有角色及其性格：
{characters}

已有势力：
{factions}

已有支线（勿重复设计）：
{existing_subplots}

大纲结构（卷/章）：
{outline_summary}

{chain_constraints}

每个支线需包含：名称、类型(character_arc/romance/mystery/revenge/political/exploration)、一句话描述、起始卷、结束卷、优先级(1-10)、视角(pov: protagonist/other/omniscient)，以及七要素(欲望/阻碍/行动/结果/意外/转折/结局)。为七要素分别绑定合适的章节号(chapter_binding)，确保支线在各卷中均匀展开。

每个方案输出JSON: {{title, summary, highlights, quality_score, content: {{name, type, description, volume_start, volume_end, priority, pov: "protagonist", elements: [{{element_type: "desire", name, description, chapter_binding: [章节号]}}]}}}}""",
}


# ============================================================
# Prompt Registry -- version management & metadata (v0.9.1)
# ============================================================
# Each prompt template registers its version, consumer agents, and changelog.
# LLM calls can query this registry to log which prompt version was used.

PROMPT_REGISTRY = {
    "style_analysis":   {"version":"0.9.1","used_by":["StyleAnalyzer"],"changelog":"v0.9.0 initial 50-dim"},
    "style_brief":      {"version":"0.9.0","used_by":["StyleAnalyzer"],"changelog":"v0.9.0 initial JSON->NL 2nd LLM"},
    "planning":         {"version":"0.9.0","used_by":["Planner"],"changelog":"v0.9.0 cross-agent review"},
    "writing":          {"version":"0.9.1","used_by":["Writer"],"changelog":"v0.9.0 6-source fusion / v0.9.1 sys prompt migrated"},
    "writer_system":    {"version":"0.9.1","used_by":["Writer"],"changelog":"v0.9.1 migrated from writer.py"},
    "handover_extraction":{"version":"0.9.1","used_by":["Writer"],"changelog":"v0.8.0 initial / v0.9.1 brief added"},
    "handover_brief":   {"version":"0.9.1","used_by":["Writer"],"changelog":"v0.9.1 JSON->NL mirrors style_brief"},
    "character_extraction":{"version":"0.9.0","used_by":["CharacterManager"],"changelog":"v0.9.0 NL->16-field card"},
    "character_arc":    {"version":"0.9.0","used_by":["CharacterManager"],"changelog":"v0.9.0 milestones to subsection"},
    "character_consistency":{"version":"0.8.0","used_by":["ConsistencyChecker"],"changelog":"v0.8.0 text vs card cross-check"},
    "section_review":   {"version":"0.7.0","used_by":["Reviewer"],"changelog":"v0.7.0 per-section scoring"},
    "global_review":    {"version":"0.7.0","used_by":["Reviewer"],"changelog":"v0.7.0 global final review"},
    "continuity_editor":{"version":"0.6.0","used_by":["ContinuityEditor"],"changelog":"v0.6.0 backref classification"},
    "card_draw":        {"version":"0.9.0","used_by":["CardDrawer"],"changelog":"v0.9.0 45+ inspirations"},
}


def get_prompt_info(name: str) -> dict | None:
    """Look up a prompt template's version metadata by name."""
    return PROMPT_REGISTRY.get(name)


def get_prompt_version(name: str) -> str:
    """Get prompt version string; returns 'unversioned' if not registered."""
    info = PROMPT_REGISTRY.get(name)
    return info["version"] if info else "unversioned"
