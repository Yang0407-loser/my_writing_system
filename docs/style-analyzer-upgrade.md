# 风格分析器升级规划

## 核心思路

50 维风格模型作为完整内部表示。AI 从参考文本一键补全所有字段，用户可选展开精细调节。两套界面共存：快速层（textarea）+ 精细层（可折叠滑块）。

```
参考文本 → AI 分析 → 50 维全量填充
                        │
            ┌───────────┼───────────┐
            ▼           ▼           ▼
        预设模板     快速编辑     精细调节
       (一键覆盖)   (textarea)   (展开面板)
            │           │           │
            └───────────┴───────────┘
                        │
                        ▼
              build_style_brief()
              50 维 → 自然语言简报
                        │
                        ▼
              注入 Writer prompt
```

## 50 维模型 (3 组)

```
A. 情感基调 (12)           B. 句式节奏 (16)          C. 修辞用词 (22)
──────────────────────     ─────────────────────     ─────────────────────

[情感核心]                  [句长分布]                  [修辞格]
primary_emotion            short_sentence_ratio       metaphor_frequency
  主情感(温暖/冷峻/压抑/     短句<15字占比(0-1)         比喻频率(极少/适度/密集)
  激昂/悲凉/恐惧/好奇/
  怀旧/荒诞/宁静)          medium_sentence_ratio      simile_metaphor_ratio
                              中句15-30字占比(0-1)      明喻vs暗喻(明喻为主/
emotion_intensity                                       暗喻为主/平衡)
  情感强度 0-100            long_sentence_ratio
  (克制30←→浓烈90)           长句>30字占比(0-1)       personification
                                                      拟人频率(极少/适度/密集)
emotion_subtlety           sentence_length_variance
  表达方式(直白/含蓄/隐晦)    句长波动性               synesthesia
                              (稳定/适度波动/剧烈波动)   通感频率(极少/适度/密集)
[情感光谱]
emotion_blend              [句式类型]                 rhetorical_devices
  情感配比                  sentence_pattern            常用修辞标签
  {"悲凉":0.6,"温暖":0.3}    句式偏好(松散句/紧凑句/     (排比/反问/反复/对比/
                             排比句/长短交替/倒装句/     夸张/双关/反语/省略/
[情感节奏]                   短句群)                    设问/互文)
emotion_curve              
  情感曲线                  sentence_opening_style     rhetorical_density
  (平稳/渐强/渐弱/           句首多样性(变化丰富/        修辞密度(0-1)
   波浪/突转)                重复开头/主语开头/连词开头)
                                                     [词汇层]
emotional_peaks            complex_sentence_ratio     vocabulary_register
  高潮频率                   复合句占比(简单句为主/      用词层级(口语化/文学化/
  (每节1次/每节2-3次/        复合句为主/平衡)            学术化/古风化/新闻体)
   集中在结尾/均匀分布)      
                            [段落层]                  vocabulary_richness
catharsis_style            paragraph_rhythm             词汇丰富度(基础/中等/
  释放方式                   段落节奏(长→短交替/         丰富/专业领域)
  (爆发式/内敛式/渐进式)      短→长交替/渐进式/
                             均匀块状/跳跃式)         chengyu_frequency
[情感距离]                                            成语频率(极少/适度/密集)
narrative_empathy          paragraph_length_avg
  叙述共情度                  平均段落字数             dialect_flavor
  (冷漠旁观/适度共情/                                 方言色彩(无/轻微/浓重)
   深度代入)               paragraph_opening_style
                              段落开头偏好             foreign_loanwords
inner_monologue_ratio        (场景描写/对话起头/        外来词(无/偶尔/频繁)
  内心独白占比(0-1)           动作起头/独白起头/混合)
                                                     [修饰层]
show_vs_tell               [对话层]                   adjective_density
  展示vs讲述                dialogue_ratio               形容词密度(0-1)
  (动作驱动/心理驱动/         对话占比(0-1)
   平衡)                                            adverb_policy
                            dialogue_mixing              副词策略(克制/适度/丰富)
[情感质感]                    对话与叙述的交替方式
emotional_registry            (独立成段/嵌入叙述/混合)  modifier_position
  情感语域(日常口语/                                    修饰位置偏好(前置为主/
  文学抒情/冷峻克制/         dialogue_tag_style           后置为主/平衡)
  诗化/新闻报道)              对话标记风格
                              ("他说"密集/稀疏标记/     [感官与意象]
sensory_anchoring              零标记/动作替代)        sensory_density
  感官锚定(是/否)                                       感官描写密度
                            [节奏控制]                    (极少/适度/丰富)
emotional_contrast          pacing
  情感对比度                   整体节奏                 sensory_spectrum
  (高频切换/稳定持续/          (舒缓/中等/紧凑/急促/     感官侧重(视觉为主/
   渐进演变)                   变速)                    听觉为主/多感官平衡/
                                                        触觉突出/嗅觉突出)
                            scene_transition
                              场景过渡                 color_use
                              (直接切/过渡铺垫/         色彩使用(黑白灰/暖色调/
                              蒙太奇/时间跳跃/           冷色调/高饱和/低饱和/
                              倒叙插入)                 金属色)

                            time_dilation             imagery_domain
                              时间拉伸                  意象领域(自然/城市/
                              (实时/加速/减速/          身体/机械/宗教/战争/
                              静止/非线性)              家庭)

                            tension_curve
                              张力曲线
                              (持续上升/波浪起伏/
                              突然爆发/缓慢释放)
```

## 数据模型

```python
class StyleProfile(BaseModel):
    # 完整 50 维结构（AI 可全量填充）
    primary_emotion: str = "中性"
    emotion_intensity: int = 50
    emotion_subtlety: str = "含蓄"
    emotion_blend: dict = {}
    emotion_curve: str = "平稳"
    emotional_peaks: str = "均匀分布"
    catharsis_style: str = "渐进式"
    narrative_empathy: str = "适度共情"
    inner_monologue_ratio: float = 0.2
    show_vs_tell: str = "平衡"
    emotional_registry: str = "文学抒情"
    sensory_anchoring: bool = True
    emotional_contrast: str = "渐进演变"
    short_sentence_ratio: float = 0.3
    medium_sentence_ratio: float = 0.5
    long_sentence_ratio: float = 0.2
    sentence_length_variance: str = "适度波动"
    sentence_pattern: str = "长短交替"
    sentence_opening_style: str = "变化丰富"
    complex_sentence_ratio: str = "平衡"
    paragraph_rhythm: str = "均匀块状"
    paragraph_length_avg: int = 200
    paragraph_opening_style: str = "混合"
    dialogue_ratio: float = 0.3
    dialogue_mixing: str = "混合"
    dialogue_tag_style: str = "稀疏标记"
    pacing: str = "中等"
    scene_transition: str = "过渡铺垫"
    time_dilation: str = "实时"
    tension_curve: str = "波浪起伏"
    metaphor_frequency: str = "适度"
    simile_metaphor_ratio: str = "平衡"
    personification: str = "适度"
    synesthesia: str = "极少"
    rhetorical_devices: list[str] = []
    rhetorical_density: float = 0.1
    vocabulary_register: str = "文学化"
    vocabulary_richness: str = "中等"
    chengyu_frequency: str = "适度"
    dialect_flavor: str = "无"
    foreign_loanwords: str = "偶尔"
    adjective_density: float = 0.15
    adverb_policy: str = "适度"
    modifier_position: str = "平衡"
    sensory_density: str = "适度"
    sensory_spectrum: str = "视觉为主"
    color_use: str = "暖色调"
    imagery_domain: str = "自然"
    
    # 元数据
    style_brief: str = ""       # AI 自动生成的风格简报文本
    reference_text: str = ""    # 参考文本
    preset_name: str = ""       # 预设名称
```

## API（3 个端点）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/style/analyze` | `{reference_text}` → AI 分析 → 返回完整 50 维 + style_brief |
| POST | `/api/style/preset` | `{preset_name}` → 返回完整 50 维预设 + style_brief |
| POST | `/api/style/brief` | `{style_profile}` → 将 50 维转为自然语言 style_brief（用于用户手动调参后重新生成简报） |

## AI 补全策略

`/api/style/analyze` 的 prompt 要求 LLM 以完整 JSON 格式返回全部 50 个字段。LLM 一次调用填充全部。解析后 Pydantic 校验，缺失字段用默认值补齐。

`/api/style/preset` 预设存储在代码中作为 dict 常量。预设提供完整 50 维。用户选择预设后也可以再调 AI 从参考文本微调。

## UI

右侧面板"风格控制台"——双层结构：

**快速层（始终可见）：**
```
┌─ 风格控制台 ──────────────────┐
│ [🔥热血] [❄️冷峻] [🌸治愈]     │
│ [🖤压抑] [⚡紧迫] [🎭荒诞]     │
│                                │
│ 风格简报 (可编辑):              │
│ ┌────────────────────────────┐ │
│ │ 情感基调为冷峻压抑...        │ │
│ │ (AI自动生成，可直接修改)     │ │
│ └────────────────────────────┘ │
│ [🤖 从参考文本提取]  [🔧 精细调节] │
└────────────────────────────────┘
```

**精细层（点击"🔧 精细调节"后展开）：**
```
┌─ 精细调节 ────────────────────┐
│ ▶ A. 情感基调 (12项)           │
│   primary_emotion: [冷峻 ▾]    │
│   emotion_intensity: [===○] 35 │
│   emotion_subtlety: [含蓄 ▾]   │
│   ...折叠项...                 │
│                                │
│ ▶ B. 句式节奏 (16项)           │
│ ▶ C. 修辞用词 (22项)           │
│                                │
│ [重新生成简报] [恢复默认]       │
└────────────────────────────────┘
```

- 精细层修改任何字段后，点"重新生成简报"→ 调 `/api/style/brief` 刷新 textarea
- 预设按钮覆盖全部 50 维 + 重新生成简报
- 用户可以直接在 textarea 里改简报（快速模式），也可以在精细面板里逐项调（专家模式）

## Writer 集成

coordinator 传给 Writer 两个东西：
1. `style_brief` — 自然语言简报，直接注入 prompt
2. `style_profile` — 完整 50 维 dict，供 Reviewer 后续审计时对比

当前 `WRITING_PROMPT` 的零散 6 个数字被替换为：
```
## 风格要求
{style_brief}

请严格遵循以上风格描述。特别注意句式节奏、用词密度和情感表达方式。
```

## 6 个预设

每个预设是完整 50 维 dict，存储在 `style_analyzer.py` 中作为常量。

## 影响文件

| 文件 | 改动 |
|------|------|
| `app/models.py` | `StyleProfile` 50 字段完整模型 |
| `app/utils/prompt_templates.py` | `STYLE_ANALYSIS_PROMPT` 输出 50 维 JSON |
| `app/agents/style_analyzer.py` | `analyze()` 返回 StyleProfile；`get_preset()`；`build_brief()` |
| `app/main.py` | 3 个端点 |
| `app/coordinator.py` | Writer prompt 注入 style_brief |
| `writing_ui.html` | 预设按钮 + textarea + 可折叠精细面板 |

## 验证

1. `POST /api/style/analyze` → 50 维完整 JSON + style_brief
2. `POST /api/style/preset` → 6 个预设各含完整 50 维
3. `POST /api/style/brief` → 手动改参数后重新生成简报
4. UI 预设切换 → textarea 更新 + 精细面板字段同步
5. UI 精细面板改参数 → 点"重新生成" → textarea 更新
6. 写作全流程 → style_brief 注入 Writer prompt
