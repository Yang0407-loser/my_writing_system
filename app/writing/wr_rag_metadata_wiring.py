"""WR3.5 write-side wiring: real WR commit -> flat RAG metadata provider.

Default-off seam between the Writer subsection commit and the WR runtime:
``build_rag_metadata_provider`` loads the frozen WR commit payload for a
(task, section, subsection), projects it with ``project_rag_metadata`` and
returns the flat retrieval metadata dict (characters/time/weekday/locations/
world_revision/source) that ``StateCommitter.commit_subsection(rag_metadata=...)``
merges into chunk metadata.  Nothing here changes production behavior unless a
provider is explicitly passed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from .world_runtime_metadata_projection import project_rag_metadata
from .world_runtime_state_committer import CommittedWorldState

logger = logging.getLogger(__name__)


def _candidate_paths(commits_dir: Path, section: int, subsection: int) -> list[Path]:
    paths = [
        commits_dir / f"S{section}_{subsection}.json",
        commits_dir / f"S{section}.{subsection}.json",
    ]
    if section == 1:
        # Legacy canary payloads name section-1 subsection commits S1/S2/S3.
        paths.append(commits_dir / f"S{subsection}.json")
    return paths


def load_wr_committed(
    commits_dir: Path | str,
    task_id: str,
    section: int,
    subsection: int,
) -> CommittedWorldState | None:
    """Load and validate the WR commit payload for one subsection, if present."""
    root = Path(commits_dir)
    for path in _candidate_paths(root, int(section), int(subsection)):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            committed = CommittedWorldState.model_validate(payload)
            if committed.after.revision is None:
                logger.warning("WR commit payload missing after revision: %s", path)
                return None
            return committed
        except Exception as exc:
            logger.warning("WR commit payload invalid: %s (%s)", path, type(exc).__name__)
            return None
    return None


def flat_rag_metadata(
    committed: CommittedWorldState,
    *,
    section: int = 1,
    subsection: int = 1,
) -> dict[str, Any]:
    """Project one real WR commit into the flat chunk-metadata shape."""
    projection = project_rag_metadata(committed, section=section, subsection=subsection)
    flat = dict(projection["metadata"])
    flat["metadata_source"] = projection["schema_version"]
    return flat


def build_rag_metadata_provider(
    commits_dir: Path | str,
    task_id: str,
) -> Callable[[int, int], dict[str, Any] | None]:
    """Return a (section, subsection) -> flat rag_metadata | None provider."""

    def provider(section: int, subsection: int) -> dict[str, Any] | None:
        committed = load_wr_committed(commits_dir, task_id, section, subsection)
        if committed is None:
            return None
        return flat_rag_metadata(committed, section=section, subsection=subsection)

    return provider
