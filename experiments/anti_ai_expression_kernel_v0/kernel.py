"""Compatibility re-export of the production-frozen expression kernel."""

from app.writing.anti_ai_expression_kernel import (
    ANTI_AI_EXPRESSION_KERNEL_V0,
    KERNEL_VERSION,
    ExpressionKernel,
    expression_kernel_hash,
    render_expression_kernel,
)

__all__ = [
    "ANTI_AI_EXPRESSION_KERNEL_V0",
    "KERNEL_VERSION",
    "ExpressionKernel",
    "expression_kernel_hash",
    "render_expression_kernel",
]
