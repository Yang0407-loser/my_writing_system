import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_config_from(tmp_path: Path, *, testing: bool) -> dict[str, str]:
    (tmp_path / ".env").write_text(
        "WRITER_HANDOVER_CONTRACT_VERSION=v2.3\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("WRITER_HANDOVER_CONTRACT_VERSION", None)
    env.pop("WRITER_TESTING", None)
    if testing:
        env["WRITER_TESTING"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(PROJECT_ROOT), env.get("PYTHONPATH", ""))
        if part
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from app.config import settings; "
                "print(json.dumps({"
                "'handover': settings.WRITER_HANDOVER_CONTRACT_VERSION, "
                "'raw': settings.WRITER_HANDOVER_CONTRACT_VERSION_RAW"
                "}))"
            ),
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_testing_mode_does_not_load_dotenv_from_cwd(tmp_path: Path) -> None:
    assert _load_config_from(tmp_path, testing=True) == {
        "handover": "v1",
        "raw": "v1",
    }


def test_production_mode_still_loads_dotenv_from_cwd(tmp_path: Path) -> None:
    assert _load_config_from(tmp_path, testing=False) == {
        "handover": "v2.3",
        "raw": "v2.3",
    }


def test_pytest_session_removes_runtime_after_global_store_import(
    tmp_path: Path,
) -> None:
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    test_file = tmp_path / "test_global_store.py"
    test_file.write_text(
        (
            "from app.dependencies import char_store\n\n"
            "def test_global_store_uses_test_database():\n"
            "    path = char_store._conn.execute("
            "'PRAGMA database_list').fetchone()[2]\n"
            "    assert 'writer-tests-' in path\n"
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["TEMP"] = str(system_temp)
    env["TMP"] = str(system_temp)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(PROJECT_ROOT), env.get("PYTHONPATH", ""))
        if part
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "tests.conftest",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert list(system_temp.glob("writer-tests-*")) == []
