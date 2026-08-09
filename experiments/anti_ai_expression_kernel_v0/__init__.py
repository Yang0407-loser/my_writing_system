"""Single-scene, expression-only Anti-AI Kernel experiment."""

from .kernel import ANTI_AI_EXPRESSION_KERNEL_V0, render_expression_kernel
from .metrics import evaluate_expression_signals

__all__ = [
    "ANTI_AI_EXPRESSION_KERNEL_V0",
    "render_expression_kernel",
    "evaluate_expression_signals",
]
