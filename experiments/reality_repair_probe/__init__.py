"""One-pass, non-production narrative reality repair experiment."""

from .runner import (
    REALITY_REPAIR_PROMPT_VERSION,
    build_repair_instruction,
    evaluate_sections,
    run_repair_probe,
)

__all__ = [
    "REALITY_REPAIR_PROMPT_VERSION",
    "build_repair_instruction",
    "evaluate_sections",
    "run_repair_probe",
]
