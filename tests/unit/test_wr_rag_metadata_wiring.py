"""WR3.5 write-side rag_metadata wiring: real WR commit -> flat metadata."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from app.writing.wr_rag_metadata_wiring import (
    build_rag_metadata_provider,
    flat_rag_metadata,
    load_wr_committed,
)
from experiments.world_runtime_writer_canary import wr3_shadow_audit as audit


ROOT = Path(__file__).resolve().parents[2]
C21R10_COMMITS = ROOT / ".world_runtime_state_commit_canary_runtime" / "c21r10" / "private" / "commits"


def _gold_committed():
    gold = build_saturday_bakery_gold_fixture()
    delta, validation = audit._gold_committable(gold)
    return WorldRuntimeStateCommitter().commit(
        idempotency_key="wr3:gold",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )


def test_flat_rag_metadata_projects_real_commit():
    committed = _gold_committed()
    flat = flat_rag_metadata(committed, section=1, subsection=2)
    assert set(flat["characters"]) == {"林晚", "周野", "季晴", "老吴"}
    assert flat["time"] == "04:20"
    assert flat["weekday"] == "saturday"
    assert "bakery:wild-bread:workshop" in flat["locations"]
    assert flat["world_revision"] == 8
    assert flat["metadata_source"] == "world-runtime-metadata-projection-wr3.5-v1"
    assert flat["section"] == 1
    assert flat["subsection"] == 2


def test_flat_rag_metadata_is_deterministic():
    committed = _gold_committed()
    assert flat_rag_metadata(committed, section=1, subsection=1) == flat_rag_metadata(
        committed, section=1, subsection=1
    )


def test_load_wr_committed_from_real_canary_payload():
    path = C21R10_COMMITS / "S1.json"
    if not path.exists():
        pytest.skip("c21r10 canary commits not present")
    committed = load_wr_committed(C21R10_COMMITS, "saturday-bakery", 1, 1)
    assert committed is not None
    assert committed.after.revision == 8


def test_provider_missing_subsection_returns_none():
    if not C21R10_COMMITS.exists():
        pytest.skip("c21r10 canary commits not present")
    provider = build_rag_metadata_provider(C21R10_COMMITS, "saturday-bakery")
    assert provider(1, 1) is not None
    assert provider(9, 1) is None
