"""Run the offline Phase 4R R5 deterministic post-generation validator.

The benchmark owns only frozen fixture adaptation. Validator behavior lives in
``app.writing.boundary_validator`` so production code never imports tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.writing.boundary_validator import (
    VALIDATOR_VERSION,
    BoundaryValidator,
    ValidationContract,
)
from tests.benchmarks.benchmark_phase4r_r2_scene_spec import SCENES, compile_scene


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".phase4r_r3_runtime"
R3_PUBLIC = ROOT / "reports" / "phase4r-batch-r3-package-manifest.json"
DEFAULT_OUTPUT = RUNTIME / "r5" / "predictions.json"
TASK_ID = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"
Contract = ValidationContract


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _query_manifest(public: dict[str, Any], query_index: int) -> dict[str, Any]:
    return next(item for item in public["queries"] if int(item["query_index"]) == query_index)


def _context_source(query: dict[str, Any]) -> dict[str, str]:
    item = next(
        item for item in query["arms"]["legacy_full"]["context_items"]
        if item["item_id"] == "current:mandatory_events"
    )
    return {"source_id": item["source_id"], "text_hash": item["text_hash"], "role": "current_writing_requirement"}


def build_contract(public: dict[str, Any], query_index: int) -> ValidationContract:
    query = _query_manifest(public, query_index)
    spec, _ = compile_scene(query_index, SCENES[query_index], TASK_ID)
    expected = query["arms"]["broker_scene_spec"]
    if spec.spec_hash != expected["scene_spec_hash"]:
        raise AssertionError(f"q{query_index}: frozen SceneSpec hash changed")
    scene_source = expected["scene_spec_source_manifest"][0]
    refs = (
        {"source_id": scene_source["source_id"], "text_hash": scene_source["text_hash"], "role": "scene_spec_constraint"},
        _context_source(query),
    )
    return ValidationContract(
        query_index=query_index,
        section=int(query["section"]),
        subsection=int(query["subsection"]),
        intent=SCENES[query_index]["intent"],
        spec_hash=spec.spec_hash,
        source_refs=refs,
    )


def build_predictions() -> dict[str, Any]:
    public = _read_json(R3_PUBLIC)
    run_manifest = _read_json(RUNTIME / "run_manifest.json")
    if int(run_manifest.get("generation_calls", 0)) != 12:
        raise AssertionError("R5 requires all 12 frozen R3 candidates")
    validator = BoundaryValidator()
    predictions = []
    for query in run_manifest["queries"]:
        query_index = int(query["query_index"])
        contract = build_contract(public, query_index)
        for candidate in query["candidates"]:
            candidate_id = candidate["candidate_id"]
            output_hash = candidate["output_sha256"]
            text = (RUNTIME / f"q{query_index:02d}" / f"{candidate_id}.txt").read_text(encoding="utf-8")
            predictions.append(validator.validate(contract, candidate_id, text, output_hash))
    if len(predictions) != 12:
        raise AssertionError("expected 12 predictions")
    return {
        "schema_version": "phase4r-r5-predictions-v1",
        "mode": "offline_prediction_only",
        "validator_version": VALIDATOR_VERSION,
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "production_behavior_changed": False,
        "production_messages_hash_unchanged": public["production_messages_hash_unchanged"],
        "runtime_answer_fields_used": [],
        "candidate_count": len(predictions),
        "predictions": predictions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_predictions()
    _write_json(args.output, payload)
    print(json.dumps({
        "candidate_count": payload["candidate_count"],
        "prediction_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
