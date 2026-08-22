from __future__ import annotations

from .boundary_ticket import canonical_hash
from .models import BoundaryTicket, CompiledSummary

TEMPLATES = {
    "raised_mesh_rack": "今晚的取舍已经确定：唯一的防水箱留给顾客暂存的手写日记，书店校样本只移到高处通风网架临时避水。场景止于两人完成这项临时处置；长期干燥、修复、窗体处理和两人的关系都不在此刻发生变化。",
    "single_absorbent_wrap": "今晚的取舍已经确定：唯一的防水箱留给顾客暂存的手写日记，书店校样本只用一层吸水材料临时包覆。场景止于两人完成这项临时处置；长期干燥、修复、窗体处理和两人的关系都不在此刻发生变化。",
}


def compile_summary(ticket: BoundaryTicket) -> CompiledSummary:
    text = TEMPLATES[ticket.locked_boundaries.store_item_temporary_handling]
    body = {
        "compiler_version": "boundary-summary-1.0",
        "source_ticket_hash": ticket.ticket_hash,
        "compiled_summary": text,
        "model_calls": 0,
    }
    return CompiledSummary.model_validate({**body, "summary_hash": canonical_hash(body)})


def validate_summary(summary: CompiledSummary, ticket: BoundaryTicket) -> CompiledSummary:
    if summary.source_ticket_hash != ticket.ticket_hash:
        raise ValueError("summary source ticket mismatch")
    if summary.summary_hash != canonical_hash(summary.model_dump(exclude={"summary_hash"})):
        raise ValueError("summary hash mismatch")
    if summary.compiled_summary != TEMPLATES[ticket.locked_boundaries.store_item_temporary_handling]:
        raise ValueError("summary is not deterministic compiler output")
    return summary

