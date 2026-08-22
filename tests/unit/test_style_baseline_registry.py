import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_style_baseline_registry_reclassifies_old_gold_and_pins_hash():
    registry = json.loads(
        (ROOT / "tests/quality/style_baseline_registry.v1.json").read_text(
            encoding="utf-8"
        )
    )
    baseline = registry["baselines"][0]
    artifact = ROOT / baseline["artifact_path"]
    assert baseline["baseline_type"] == "regression"
    assert "human_gold" in baseline["prohibited_claims"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == baseline[
        "artifact_sha256"
    ]


def test_registry_defines_authorization_and_text_evidence_contracts():
    schemas = json.loads(
        (ROOT / "tests/quality/style_baseline_registry.v1.json").read_text(
            encoding="utf-8"
        )
    )["baseline_schemas"]
    assert "license_or_authorization" in schemas["human_reference"]["required_fields"]
    assert schemas["human_reference"]["text_requirements"] == {
        "min_characters": 800,
        "max_characters": 1500,
        "authorization_evidence_required": True,
    }
    assert "evidence_spans" in schemas["machine_failure"]["required_fields"]
