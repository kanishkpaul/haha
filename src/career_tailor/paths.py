"""Filesystem locations shared by every stage."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CAREER_RECORD = REPO.parent / "outputs" / "KANISHK_PAUL_MASTER_CAREER_RECORD.md"
EVIDENCE_DIR = REPO / "data" / "evidence"
MASTERS_DIR = REPO / "data" / "masters"
JOBS_DIR = REPO / "jobs"
APPLICATIONS_DIR = REPO / "applications"
TEMPLATES_DIR = REPO / "templates"
RM_CLI = REPO / "bin" / "rm"
