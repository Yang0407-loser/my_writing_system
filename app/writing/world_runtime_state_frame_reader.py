"""WR3.10 shadow state-frame reader: WR shadow checkpoint -> legacy StateFrame V1.

Read-side switch for the Event Ledger / StateFrame After consumer.  When a task
checkpoint carries a valid ``world_runtime_shadow_v1`` payload, the reader
returns the WR-built legacy StateFrame V1 after-view (deterministic, verified by
recomputing the frame hash).  Otherwise it fails open to the legacy
``build_state_frame_artifacts`` path, so old checkpoints stay readable.

No production wiring is changed: this is the opt-in reader for the migration.
"""

from __future__ import annotations

from typing import Any, Mapping

from .state_frame_service import build_state_frame_artifacts
from .world_runtime_checkpoint_shadow import (
    CHECKPOINT_SHADOW_KEY,
    verify_shadow_payload,
)
from .world_runtime_legacy_projection import project_state_frame


READER_VERSION = "world-runtime-state-frame-reader-wr3.10-v1"


def read_state_frame_after(
    *,
    task_id: str,
    section: int,
    subsection: int,
    task_data: Mapping[str, Any] | None = None,
    checkpoint: Mapping[str, Any] | None = None,
    committed=None,
) -> dict[str, Any]:
    """Return the StateFrame V1 after-view, WR-shadow first, legacy fallback."""
    checkpoint = dict(checkpoint or {})
    shadow = checkpoint.get(CHECKPOINT_SHADOW_KEY)
    if shadow is not None and committed is not None:
        verified, issues = verify_shadow_payload(shadow)
        if verified:
            frame = project_state_frame(
                committed,
                task_id=task_id,
                section=section,
                subsection=subsection,
            )
            stored_hash = shadow.get("legacy_frame_hash")
            if frame.frame_hash == stored_hash:
                return {
                    "schema_version": READER_VERSION,
                    "source": "world_runtime_shadow",
                    "verified": True,
                    "frame": frame.model_dump(mode="json"),
                }
            issues = [f"legacy_frame_hash_mismatch:{stored_hash}"]
        fallback_reason = "shadow_invalid:" + "|".join(issues)
    else:
        fallback_reason = "shadow_missing" if shadow is None else "committed_missing"
    artifacts = build_state_frame_artifacts(
        task_id=task_id,
        section=section,
        subsection=subsection,
        task_data=task_data,
        checkpoint=checkpoint,
    )
    return {
        "schema_version": READER_VERSION,
        "source": "legacy",
        "verified": False,
        "fallback_reason": fallback_reason,
        "frame": artifacts["after"],
    }
