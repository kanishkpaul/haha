"""Checks that the local setup is wired correctly."""

import importlib
import json
import subprocess

import pytest

from career_tailor import paths

STAGES = ["bank", "jobspec", "match", "selection", "write", "verify", "render", "ats", "assemble", "cli"]


def test_stage_modules_import():
    for name in STAGES:
        importlib.import_module(f"career_tailor.{name}")


def test_paths_default_to_sibling_folders(monkeypatch):
    for var in ("HAHA_DATA_DIR", "HAHA_CAREER_RECORD", "HAHA_PROOF_DIR"):
        monkeypatch.delenv(var, raising=False)
    p = paths.load()
    assert p.data == paths.REPO.parent / "haha-data"
    assert p.career_record.parent == paths.REPO.parent / "outputs"
    assert p.proof_entries == paths.REPO.parent / "evidence" / "entries.json"


def test_paths_follow_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HAHA_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("HAHA_CAREER_RECORD", str(tmp_path / "r.md"))
    p = paths.load()
    assert p.bank == tmp_path / "d" / "bank"
    assert p.career_record == tmp_path / "r.md"


def test_no_personal_data_dirs_in_repo():
    for name in ("data", "jobs", "applications", "haha-data"):
        assert not (paths.REPO / name).exists(), f"{name}/ belongs in HAHA_DATA_DIR"


@pytest.mark.local
def test_career_record_present():
    assert paths.load().career_record.is_file()


@pytest.mark.local
def test_resume_matcher_cli_responds():
    out = subprocess.run([str(paths.RM_CLI), "get_status"], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout)["max_master_resumes"] == 5
