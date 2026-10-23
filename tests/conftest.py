"""Shared fixtures. Unit tests run on the synthetic record; `local` tests need the real one."""

import shutil
from pathlib import Path

import pytest

from career_tailor import paths

FIXTURES = Path(__file__).parent / "fixtures"
MINI_RECORD = FIXTURES / "mini_record.md"


def pytest_collection_modifyitems(config, items):
    p = paths.load()
    reasons = []
    if not p.career_record.is_file():
        reasons.append(f"no career record at {p.career_record}")
    if not paths.RM_CLI.exists() or shutil.which("uv") is None:
        reasons.append("Resume-Matcher CLI unavailable")
    if not reasons:
        return
    skip = pytest.mark.skip(reason="; ".join(reasons))
    for item in items:
        if "local" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def mini_record() -> Path:
    return MINI_RECORD


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    """An empty data directory with HAHA_* pointing at it and the synthetic record."""
    monkeypatch.setenv("HAHA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HAHA_CAREER_RECORD", str(MINI_RECORD))
    monkeypatch.setenv("HAHA_PROOF_DIR", str(tmp_path / "proof"))
    return tmp_path / "data"
