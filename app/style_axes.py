"""Sparse, independently switchable prose-control axes.

These contracts are intentionally not wired into the production Writer yet.
They exist so the language-surface and limited-POV hypotheses can be tested
without bundling them into one Realization Policy treatment.
"""

from __future__ import annotations

from dataclasses import dataclass


ANTI_AI_SURFACE_VERSION = "anti-ai-surface-v1"
POV_DISCLOSURE_VERSION = "pov-disclosure-v1"


@dataclass(frozen=True)
class StyleAxisPolicy:
    version: str
    guidance: str


def compile_anti_ai_surface() -> StyleAxisPolicy:
    """Return the language-only treatment.

    It must not choose a focal character, knowledge boundary, event order, or
    ending state; those belong to the common content contract or POV axis.
    """

    return StyleAxisPolicy(
        version=ANTI_AI_SURFACE_VERSION,
        guidance=(
            "动作、对白或物件已显明的意义不再抽象复述；"
            "避免连续同构句、整齐排比和功能清单；"
            "具象段落后不追加情绪、关系或主题总结。"
            "重复处优先删并，不用同义改写或刻意残句冒充自然。"
        ),
    )


def compile_pov_disclosure() -> StyleAxisPolicy:
    """Return the information-selection treatment.

    It must not prescribe sentence length, rhetorical devices, or deletion of
    repeated wording; those belong to the language-surface axis.
    """

    return StyleAxisPolicy(
        version=POV_DISCLOSURE_VERSION,
        guidance=(
            "只写视点人物此刻能感知、回忆、推断或误判的内容；"
            "背景关系由当前动作、物件、对白或障碍触发再出现；"
            "先给可接触证据，再给可迟到、错误或未完成的局部判断；"
            "可不解释真正动机，但眼前动作、选择和必要因果必须清楚。"
        ),
    )


def render_style_axis(policy: StyleAxisPolicy, *, heading: str) -> str:
    return f"### {heading}\n{policy.guidance}"
