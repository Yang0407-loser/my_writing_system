from __future__ import annotations

from pathlib import Path

from scripts.foundation.run_golden_slice import run_golden_slice


def test_golden_slice_moves_heads_materializes_and_reaches_barrier(tmp_path):
    root = Path(__file__).resolve().parents[3]
    url = f"sqlite+pysqlite:///{(tmp_path / 'golden.db').as_posix()}"

    evidence = run_golden_slice(
        root / "tests/fixtures/foundation_golden_slice_v1.json",
        database_url=url,
    )

    assert evidence["hashes"]["fixture_body"] == evidence["hashes"]["revision_content"]
    assert evidence["hashes"]["fixture_body"] == evidence["hashes"]["materialized_document"]
    assert evidence["counts"]["ledger"] >= 1
    assert evidence["counts"]["outbox"] == 7
    assert all(row["status"] == "published" for row in evidence["outbox"].values())
    assert evidence["runtime"]["critical_projection_status"] == "ready"
    assert evidence["api_result"]["document_ref"]["document_id"]
    assert "draft" not in evidence["runtime"]["checkpoint_fields"]
