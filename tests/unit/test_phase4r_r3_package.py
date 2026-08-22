import ast
import json
from pathlib import Path

import pytest

from app.context_ab_shadow import messages_hash
from tests.benchmarks.phase4r_r3_package import (
    _context_manifest,
    attach_scene_spec,
    evaluate,
    import_results,
    run_all,
)


def test_scene_spec_attachment_changes_only_last_user_message():
    messages = [
        {"role": "system", "content": "fixed"},
        {"role": "user", "content": "write"},
    ]
    original_hash = messages_hash(messages)
    result = attach_scene_spec(messages, "[UNKNOWN] shop status")
    assert messages_hash(messages) == original_hash
    assert result[0] == messages[0]
    assert result[1]["content"].startswith("write")
    assert "SceneSpec" in result[1]["content"]


def test_run_requires_explicit_private_input_confirmation(tmp_path):
    with pytest.raises(RuntimeError, match="not confirmed"):
        run_all(tmp_path, confirmed=False)


def test_public_context_manifest_excludes_text():
    manifest = _context_manifest({"items": [{
        "item_id": "i", "source_id": "s", "source_type": "rag",
        "text": "private prose", "text_hash": "hash", "keep": True,
        "priority": "P2", "requirement": "evidence_required",
    }]})
    assert manifest[0]["text_hash"] == "hash"
    assert "text" not in manifest[0]


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_import_and_evaluate_validate_hashes_without_exposing_arm_order(tmp_path):
    source = tmp_path / "external"
    runtime = tmp_path / "runtime"
    for query_index in (4, 6, 7, 8):
        candidates = []
        mapping = {}
        arms = ("legacy_full", "budgeted_broker", "broker_scene_spec")
        prepare_arms = {}
        for position, arm in enumerate(arms, 1):
            candidate_id = f"candidate_{position:02d}"
            text = f"q{query_index}-{candidate_id}"
            digest = __import__("hashlib").sha256(text.encode()).hexdigest()
            candidates.append({
                "candidate_id": candidate_id, "output_sha256": digest,
                "characters": len(text), "estimated_output_tokens": 2,
                "paragraph_count": 1, "duplicate_paragraph_count": 0, "elapsed_ms": 1.0,
            })
            mapping[candidate_id] = {"arm": arm, "messages_hash": f"hash-{arm}"}
            prepare_arms[arm] = {"messages_hash": f"hash-{arm}"}
            (source / f"q{query_index:02d}").mkdir(parents=True, exist_ok=True)
            (source / f"q{query_index:02d}" / f"{candidate_id}.txt").write_text(text, encoding="utf-8")
        _write(runtime / f"q{query_index:02d}" / "prepare.json", {"arms": prepare_arms})
        _write(source / f"q{query_index:02d}" / "blind.json", {
            "query_index": query_index, "candidates": candidates,
        })
        _write(source / f"q{query_index:02d}" / "private_mapping.json", {
            "query_index": query_index, "mapping": mapping,
        })
    imported = import_results(source, runtime)
    assert imported["validated_candidates"] == 12
    result = evaluate(runtime)
    assert result["status"] == "awaiting_blind_review"
    assert all("arm" not in candidate for sample in result["samples"] for candidate in sample["candidates"])
    template = json.loads((runtime / "blind_review.template.json").read_text())
    assert "mapping" not in json.dumps(template)


def test_r3_runtime_is_ignored_and_runtime_module_has_no_evaluation_answers():
    assert ".phase4r_r3_runtime/" in Path(".gitignore").read_text(encoding="utf-8")
    path = Path("tests/benchmarks/phase4r_r3_package.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert tree
    for forbidden in ("must_recall_facts", "gold_sections", "human_relevant", "supports_which_fact"):
        assert forbidden not in source
