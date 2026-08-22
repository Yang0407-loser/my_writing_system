"""Frozen expression-only constraints for Writer shadow/canary delivery.

This module does not score prose, trigger revisions, or alter story content.
`shadow` records the frozen contract without changing Writer's prompt;
`canary` appends it to the soft style guidance exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib


KERNEL_VERSION = "anti-ai-expression-kernel-v0"
VALID_ANTI_AI_EXPRESSION_MODES = frozenset({"off", "shadow", "canary"})


@dataclass(frozen=True)
class ExpressionKernel:
    version: str
    rules: tuple[str, ...]


ANTI_AI_EXPRESSION_KERNEL_V0 = ExpressionKernel(
    version=KERNEL_VERSION,
    rules=(
        "显式比喻宁缺毋滥：每500字最多一个“像/仿佛/宛如/如同”结构；能直接写动作或物件时不用比喻。",
        "动作、对白或停顿已经显出情绪时，不再补一句抽象解释，不替读者说明人物刚刚领悟了什么。",
        "同一场景中，灯光、低鸣、冷暖、面粉等感官意象各自最多承担一次叙事作用，不换词重复渲染。",
        "段尾停在具体动作、对白、物件状态或未完成反应上，不用人生感悟、主题总结或象征性升华收束。",
        "少用“某种、说不清、好像、忽然觉得”等抽象占位词；避免“一下又一下、展开又收拢、由远及近又由近及远”等对称节拍模板。",
    ),
)


def render_expression_kernel(
    kernel: ExpressionKernel = ANTI_AI_EXPRESSION_KERNEL_V0,
) -> str:
    lines = [f"## 表达实现约束（{kernel.version}）"]
    lines.extend(f"{index}. {rule}" for index, rule in enumerate(kernel.rules, 1))
    lines.append("这些规则只控制表达，不允许改变给定事件、事实、人物关系和结束状态。只输出正文。")
    return "\n".join(lines)


def expression_kernel_hash(
    kernel: ExpressionKernel = ANTI_AI_EXPRESSION_KERNEL_V0,
) -> str:
    return hashlib.sha256(render_expression_kernel(kernel).encode("utf-8")).hexdigest()


def normalize_anti_ai_expression_mode(mode: str) -> str:
    normalized = str(mode or "off").strip().lower()
    return normalized if normalized in VALID_ANTI_AI_EXPRESSION_MODES else "off"


@dataclass(frozen=True)
class AntiAIExpressionController:
    mode: str = "off"

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_anti_ai_expression_mode(self.mode))

    @property
    def rendered_kernel(self) -> str:
        return render_expression_kernel()

    def compose(self, style_context: str) -> str:
        base = str(style_context or "").strip()
        if self.mode != "canary":
            return base
        return "\n\n".join(part for part in (base, self.rendered_kernel) if part)

    def final_prompt_constraints(self) -> str:
        """Return a suffix ready for the final Writer instruction position."""
        if self.mode != "canary":
            return ""
        return self.rendered_kernel + "\n\n"

    def observation(self, *, section: int, subsection: int) -> dict:
        rendered = self.rendered_kernel
        return {
            "section": section,
            "subsection": subsection,
            "mode": self.mode,
            "version": KERNEL_VERSION,
            "kernel_hash": expression_kernel_hash(),
            "characters": len(rendered),
            "injected": self.mode == "canary",
            "revision_enabled": False,
            "production_gate": False,
        }
