import json
from pathlib import Path


pytest_plugins = ["pytester"]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _entry(node_id: str) -> dict[str, str]:
    return {
        "node_id": node_id,
        "category": "missing_generated_artifact",
        "reason": "missing-output",
    }


def _configure_project(pytester, manifest_node_ids: list[str]) -> None:
    pytester.syspathinsert(REPOSITORY_ROOT)
    pytester.makepyprojecttoml(
        """
[tool.pytest.ini_options]
addopts = '-m "not historical_artifact"'
asyncio_default_fixture_loop_scope = "function"
markers = [
    "historical_artifact: explicit historical verification",
]
"""
    )
    pytester.makeconftest(
        'pytest_plugins = ["tests.support.pytest_suite_plugin"]\n'
    )
    manifest_dir = pytester.path / "tests"
    manifest_dir.mkdir()
    (manifest_dir / "test_suite_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [_entry(node_id) for node_id in manifest_node_ids],
            }
        ),
        encoding="utf-8",
    )
    pytester.makepyfile(
        test_sample="""
def test_historical():
    assert True


def test_safe():
    assert True
"""
    )


def test_default_selection_deselects_only_manifest_item(pytester):
    _configure_project(
        pytester,
        ["test_sample.py::test_historical"],
    )

    result = pytester.runpytest("-q")

    result.assert_outcomes(passed=1, deselected=1)


def test_explicit_historical_selection_runs_manifest_item(pytester):
    _configure_project(
        pytester,
        ["test_sample.py::test_historical"],
    )

    result = pytester.runpytest(
        "-o",
        "addopts=",
        "-m",
        "historical_artifact",
        "-q",
    )

    result.assert_outcomes(passed=1, deselected=1)


def test_full_collection_fails_when_manifest_node_is_missing(pytester):
    _configure_project(
        pytester,
        [
            "test_sample.py::test_historical",
            "test_removed.py::test_removed",
        ],
    )

    result = pytester.runpytest("-q")

    combined = result.stdout.str() + result.stderr.str()
    assert result.ret != 0
    assert "historical manifest drift" in combined
    assert "test_removed.py::test_removed" in combined


def test_targeted_selection_does_not_require_unselected_manifest_nodes(pytester):
    _configure_project(
        pytester,
        [
            "test_sample.py::test_historical",
            "test_removed.py::test_removed",
        ],
    )

    result = pytester.runpytest("test_sample.py::test_safe", "-q")

    result.assert_outcomes(passed=1)
