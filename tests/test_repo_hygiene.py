"""The repository must not track local runtime state.

Running the app creates a business SQLite database and a LangGraph checkpoint file
(plus their ``-wal``/``-shm`` sidecars). Those leaked into commits twice during this
work and turned the deliverable patch into a binary-patch failure, so the guard is a
test rather than a convention.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_SUFFIXES = (".db", ".sqlite", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm")


def _tracked_data_files():
    try:
        output = subprocess.run(["git", "-C", str(ROOT), "ls-files", "data"],
                                capture_output=True, text=True, timeout=15, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available in this environment")
    return [line.strip() for line in output.splitlines() if line.strip()]


def test_no_runtime_database_is_tracked():
    tracked = _tracked_data_files()
    offenders = [name for name in tracked if name.endswith(RUNTIME_SUFFIXES)]
    assert not offenders, f"runtime state must not be committed: {offenders}"


def test_no_malformed_database_path_is_tracked():
    """A bad path join once created a directory literally named "sqlite:" in the repo."""
    try:
        output = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                                capture_output=True, text=True, timeout=20, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available in this environment")
    offenders = [line for line in output.splitlines()
                 if line.startswith("sqlite:") or line.endswith((".sqlite-shm", ".sqlite-wal"))]
    assert not offenders, f"stray database paths must not be committed: {offenders}"


def test_expected_data_files_are_tracked():
    """data/ stays in the image (Dockerfile copies it), so it must not vanish entirely."""
    tracked = set(_tracked_data_files())
    assert "data/.gitkeep" in tracked or "data/README.md" in tracked