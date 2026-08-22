"""4 维风格参数 → 写作示例与行为指令。"""


def build_style_examples(style: dict) -> str:
    """根据 4 维参数选择对应的文本范例。"""
    if not isinstance(style, dict):
        return ""
    parts = []

    sp = style.get("sentence_preference", "balanced")
    if sp == "short":
        parts.append(_EXAMPLE_SENTENCE_SHORT)
    elif sp == "long":
        parts.append(_EXAMPLE_SENTENCE_LONG)
    else:
        parts.append(_EXAMPLE_SENTENCE_BALANCED)

    ei = style.get("emotion_intensity", 50)
    if ei <= 30:
        parts.append(_EXAMPLE_EMOTION_SUBDUED)
    elif ei <= 50:
        parts.append(_EXAMPLE_EMOTION_WARM)
    else:
        parts.append(_EXAMPLE_EMOTION_INTENSE)

    dr = style.get("dialogue_ratio", 0.3)
    parts.append(_EXAMPLE_DIALOGUE)

    sd = style.get("sensory_density", "medium")
    if sd == "rich":
        parts.append(_EXAMPLE_SENSORY_RICH)
    elif sd == "sparse":
        parts.append(_EXAMPLE_SENSORY_SPARSE)

    if parts:
        return (
            "## 风格范例（请模仿以下文本的句法节奏、对话风格和用词习惯）\n\n"
            + "\n\n".join(parts)
        )
    return ""


# ── 示例文本 ──────────────────────────────────────────────────────

_EXAMPLE_SENTENCE_SHORT = (
    "【句法示范 — 短句驱动，节奏明快】\n"
    '"他推开门。灯还亮着。没有人。烤箱在响。那种低沉持续的嗡鸣声，'
    '像冬天炉膛里的风。他没有回头。"\n'
    "（短句用于动作和转折，长句收束节奏，形成急促到舒展的呼吸）"
)

_EXAMPLE_SENTENCE_BALANCED = (
    "【句法示范 — 长短交替，自然呼吸】\n"
    '"凌晨三点半的街灯把槐树叶子照成半透明的琥珀色，周野站在案板前，'
    '掌根推出去，面团在面粉里展开又收拢。烤箱响着。'
    '肩胛骨在灰色长袖下起伏的节奏像一座老钟的摆锤，填满了这个城市沉睡时段的全部寂静。"\n'
    "（长句铺陈场景后接短句推进动作，描写与事件交替）"
)

_EXAMPLE_SENTENCE_LONG = (
    "【句法示范 — 长句铺陈，舒缓绵密】\n"
    '"凌晨三点半的街灯把槐树叶子照成半透明的琥珀色，周野站在案板前，'
    '掌根推出去，面团在面粉里展开又收拢，肩胛骨在灰色长袖下起伏的节奏像一座老钟的摆锤，'
    '而烤箱的低鸣从操作间深处传出来，像某种持续的低音，'
    '填满了这个城市沉睡时段的全部寂静。"\n'
    "（以长句层层推进描写，适合抒情、内心独白和环境铺陈）"
)

_EXAMPLE_EMOTION_SUBDUED = (
    "【情感示范 — 克制，用感官传递氛围】\n"
    '"他转回去，手掌压在案板上，没有回答。指关节泛白。'
    '烤箱还在响。那种低沉持续的嗡鸣声，从操作间深处传出来。"\n'
    "（不命名情绪，用动作和环境的细节折射内心）"
)

_EXAMPLE_EMOTION_WARM = (
    "【情感示范 — 温婉，用感官传递温度】\n"
    '"她捧着杯子站在窗边。水温透过杯壁，一点一点暖到掌心。'
    '窗外的槐树叶子沙沙响，路灯把影子拉得很长。'
    '她想起小时候外婆也是这样，端着搪瓷杯站在院子里，看天慢慢亮起来。"\n'
    "（情绪可提及但不展开，用光线、温度、声音传递温度感）"
)

_EXAMPLE_EMOTION_INTENSE = (
    "【情感示范 — 浓郁，允许直述内心】\n"
    '"她忽然觉得胸口被什么东西堵住了。不是悲伤——比悲伤更锋利。'
    '是愤怒，是委屈，是所有被\'感觉不对\'打发掉的凌晨三点半一起涌上来。'
    '她把辞职信拍在桌上，手心是湿的。"\n'
    "（直接命名情绪，允许内心感受的展开和抒情）"
)

_EXAMPLE_DIALOGUE = (
    "【对话示范 — 用动作替代「说」标签】\n"
    '"周野把水杯推过来。\'晾温了。\'\n'
    '杯壁在她掌心烫了一下。不是水烫——杯子是温的，烫的是手指碰到的位置，那里沾着面粉。\n'
    '她低头看杯里的水。"几点开始揉面的。"\n'
    '他转回去，手掌压在案板上，没有回答。指关节泛白。"\n'
    "（对话独立成段，用身体语言传递情绪，避免「XX说」标签）"
)

_EXAMPLE_SENSORY_RICH = (
    "【感官示范 — 多感官交织】\n"
    '"烤箱的低鸣从操作间深处传出来，像某种持续的低音。'
    '发酵的酸味混着焦糖的甜，从门缝里往外溢。'
    '她摸了一下案板——木头的纹理被面粉填平了，触感像磨过的石头，凉而滑。"\n'
    "（同时调动听觉、嗅觉、触觉）"
)

_EXAMPLE_SENSORY_SPARSE = (
    "【感官示范 — 留白简洁】\n"
    '"案板上只剩一层面粉。很薄。像霜。"\n'
    "（选一个最准确的动作或细节，让读者自己补全画面）"
)
