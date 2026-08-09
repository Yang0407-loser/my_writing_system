from __future__ import annotations

import json
from typing import Any

from .models import HISTORICAL_STYLE_FIELDS, StyleContract


SYSTEM_PROMPT = """你是一位职业小说作者。只输出小说正文，不要输出标题、解释、分析、提纲或元数据。
必须完成场景任务，保持人物边界和事实一致。不要机械清点动作，不要用时间、序号或数字反复起句，
不要用同构短句堆出虚假的节奏，不要照抄任何参考材料。"""


BASE_USER_PROMPT = """请完成以下独立小说场景。

## 场景任务
{scene_prompt}

## 人物资料
{characters}

## 世界与连续性事实
{world_facts}

## 必须发生
{mandatory_events}

## 禁止发生
{forbidden_events}

## 风格输入
{style_input}

正文目标长度：{target_chars} 个汉字左右，允许上下浮动 15%。
只输出正文。"""


def render_contract(contract: StyleContract, scene_modulation: str) -> str:
    positive = "\n".join(f"- {item}" for item in contract.positive_principles)
    prohibitions = "\n".join(f"- {item}" for item in contract.prohibitions)
    examples = "\n\n".join(
        f"正例 {index}：{text}" for index, text in enumerate(contract.positive_examples, 1)
    )
    negatives = "\n\n".join(
        f"反例 {index}：{item.text}\n错误原因：{item.reason}"
        for index, item in enumerate(contract.negative_examples, 1)
    )
    adaptations = "\n".join(f"- {key}：{value}" for key, value in contract.scene_adaptation.items())
    return f"""### 稳定风格契约
正向原则：
{positive}

明确禁忌：
{prohibitions}

叙事距离与视角：{contract.narrative_distance_and_viewpoint}
句式与段落节奏：{contract.sentence_and_paragraph_rhythm}
措辞、意象和感官来源：{contract.diction_imagery_and_sensory_sources}
情绪表达：{contract.emotional_expression}

场景适配：
{adaptations}

{examples}

{negatives}

### 本场景调制
{scene_modulation}"""


def build_style_input(
    arm: str,
    prepared: dict[str, Any],
    scene_modulation: str,
) -> str:
    if arm == "A":
        return "无额外风格控制。"
    if arm == "B":
        profile = prepared["four_dimensional"]
        values = {
            key: profile.get(key)
            for key in ("emotion_intensity", "dialogue_ratio", "sentence_preference", "sensory_density")
        }
        return (
            "### 当前四维风格方案\n"
            f"控制量：{json.dumps(values, ensure_ascii=False)}\n"
            f"自然语言简报：{profile.get('style_brief', '')}"
        )
    if arm == "C":
        return (
            "### 历史“50维”分析转写简报\n"
            f"{prepared.get('historical_brief', '')}\n"
            "注意：只执行自然语言简报，不要在正文中复述参数。"
        )
    if arm == "D":
        contract = StyleContract.model_validate(prepared["style_contract"])
        return render_contract(contract, scene_modulation)
    raise ValueError(f"unknown arm: {arm}")


def build_generation_messages(
    *,
    arm: str,
    prepared: dict[str, Any],
    scene: dict[str, Any],
    shared_context: dict[str, Any],
    target_chars: int,
) -> list[dict[str, str]]:
    style_input = build_style_input(arm, prepared, scene["style_modulation"])
    prompt = BASE_USER_PROMPT.format(
        scene_prompt=scene["prompt"],
        characters="\n".join(f"- {item}" for item in shared_context["characters"]),
        world_facts="\n".join(f"- {item}" for item in shared_context["world_facts"]),
        mandatory_events="\n".join(f"- {item}" for item in scene["mandatory_events"]),
        forbidden_events="\n".join(f"- {item}" for item in scene["forbidden_events"]),
        style_input=style_input,
        target_chars=target_chars,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def build_control_response_messages(
    *,
    dimension: str,
    level: str,
    instruction: str,
    scene: dict[str, Any],
    shared_context: dict[str, Any],
    target_chars: int,
) -> list[dict[str, str]]:
    style_input = f"""### 单变量控制响应实验
本次只测试 `{dimension}` 的 `{level}` 档。
执行指令：{instruction}
除这一项外，不主动追求其他风格特征。不要在正文中复述实验名、维度名或档位。"""
    prompt = BASE_USER_PROMPT.format(
        scene_prompt=scene["prompt"],
        characters="\n".join(f"- {item}" for item in shared_context["characters"]),
        world_facts="\n".join(f"- {item}" for item in shared_context["world_facts"]),
        mandatory_events="\n".join(f"- {item}" for item in scene["mandatory_events"]),
        forbidden_events="\n".join(f"- {item}" for item in scene["forbidden_events"]),
        style_input=style_input,
        target_chars=target_chars,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def historical_analysis_messages(reference_text: str) -> list[dict[str, str]]:
    field_list = "\n".join(f"- {field}" for field in HISTORICAL_STYLE_FIELDS)
    prompt = f"""从参考文本中分析历史“50维”风格合同。历史代码实际列出了 49 个非元数据字段；
保持这个历史事实，不要补造第 50 个字段。只输出 JSON 对象。

字段：
{field_list}

无法从文本可靠判断的字段请填 null，不要使用默认值假装分析结果。

参考文本：
{reference_text[:6000]}"""
    return [
        {"role": "system", "content": "你是文学风格分析员，只输出合法 JSON。"},
        {"role": "user", "content": prompt},
    ]


def historical_brief_messages(profile: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "你是文学编辑。把结构化风格分析转成可执行的自然语言简报，不得补写未知字段。",
        },
        {
            "role": "user",
            "content": (
                "将以下历史风格分析改写成 200—500 字编辑简报。覆盖情感表达、叙事距离、"
                "句段节奏、对话、措辞、修辞和感官来源。不要罗列数字，不要添加标题。\n\n"
                + json.dumps(profile, ensure_ascii=False, indent=2)
            ),
        },
    ]


def contract_analysis_messages(reference_text: str) -> list[dict[str, str]]:
    schema = StyleContract.model_json_schema()
    return [
        {
            "role": "system",
            "content": "你是文学编辑。只输出合法 JSON，并严格依据参考文本证据。",
        },
        {
            "role": "user",
            "content": f"""从参考文本蒸馏一份稳定的作品级风格契约。

要求：
- 3—5 条正向原则和 3—5 条禁忌；
- 明确叙事距离、视角、句段节奏、措辞意象、感官来源和情绪表达；
- 分别给出 dialogue、action、introspection 三类场景适配规则；
- 提取 2—3 个短正例；
- 写 1—2 个短反例并说明错误原因；反例必须是你根据风险自行改写的错误示范，不得冒充原文；
- evidence 中保存原则与参考文本短引文的对应关系；
- 不得大段复制参考文本。

JSON Schema：
{json.dumps(schema, ensure_ascii=False)}

参考文本：
{reference_text[:6000]}""",
        },
    ]
