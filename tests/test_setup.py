"""Checks that the local setup is wired correctly. No pipeline logic is tested yet."""

import importlib
import json
import subprocess

from career_tailor import paths

STAGES = ["bank", "jobspec", "match", "selection", "masters", "write", "verify", "render", "ats", "cli"]


def test_stage_modules_import():
    for name in STAGES:
        importlib.import_module(f"career_tailor.{name}")


def test_career_record_present():
    assert paths.CAREER_RECORD.is_file()


def test_resume_matcher_cli_responds():
    out = subprocess.run([str(paths.RM_CLI), "get_status"], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout)["max_master_resumes"] == 5
