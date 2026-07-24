import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_state_frame_production_modules_do_not_import_tests_or_llm():
    paths = [
        ROOT / "app" / "writing" / "state_frame_v1.py",
        ROOT / "app" / "writing" / "state_frame_builder.py",
        ROOT / "app" / "writing" / "state_frame_quality.py",
        ROOT / "app" / "writing" / "state_frame_service.py",
        ROOT / "app" / "routers" / "state_frames.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = "\n".join(
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        assert "tests" not in imports
        assert "llm" not in imports.lower()
        assert "writer" not in imports.lower()


def test_state_frame_does_not_modify_writer_pipeline_or_create_database():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "app" / "writing" / "state_frame_v1.py",
            ROOT / "app" / "writing" / "state_frame_builder.py",
            ROOT / "app" / "writing" / "state_frame_quality.py",
            ROOT / "app" / "writing" / "state_frame_service.py",
        )
    )
    for forbidden in (
        "sqlite3.connect", "CREATE TABLE", "chat_completion",
        "save_checkpoint(", "commit_subsection(", "messages.append(",
    ):
        assert forbidden not in source
