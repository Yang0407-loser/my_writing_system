import ast
import hashlib
from pathlib import Path

from tests.benchmarks.phase4r_r5_boundary_validator import _write_json, build_predictions


FROZEN_R5_SHA256 = "fb6e21589d362b9e43f8da00ed8f99709c2d90804a2c72be63e691553baa42c0"


def test_r5_prediction_bytes_remain_frozen_after_production_extraction(tmp_path):
    output = tmp_path / "predictions.json"
    _write_json(output, build_predictions())
    assert hashlib.sha256(output.read_bytes()).hexdigest() == FROZEN_R5_SHA256


def test_writer_hook_is_after_commit_and_cannot_mutate_messages():
    source = Path("app/agents/writer.py").read_text(encoding="utf-8")
    commit = source.index("state_committer.commit_subsection(")
    recorded = source.index("subsection_pipeline.record_commit(commit_artifact)", commit)
    shadow = source.index("shadow_boundary_validator.observe_committed(", recorded)
    assert commit < recorded < shadow
    call = source[shadow:source.index("\n                )", shadow) + 18]
    assert "messages=" not in call
    assert "prompt=" not in call


def test_app_never_imports_tests_and_shadow_records_have_no_private_payload_fields():
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        assert all("tests" not in ast.unparse(node) for node in imports), path
    source = Path("app/writing/shadow_validation.py").read_text(encoding="utf-8")
    forbidden_record_keys = ('"text": text', '"messages"', '"prompt"', '"api_key"')
    assert all(value not in source for value in forbidden_record_keys)


def test_feature_flag_default_is_literal_false():
    source = Path("app/config.py").read_text(encoding="utf-8")
    assert '"WRITER_BOUNDARY_VALIDATOR_SHADOW", "false"' in source
