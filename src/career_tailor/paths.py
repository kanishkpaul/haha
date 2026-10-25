"""Filesystem locations shared by every stage.

Code lives in this repository; personal data never does. Everything derived
from the career record, and every job and application, lives in the data
directory, which defaults to a `haha-data/` folder next to the repository.

Override with environment variables:
- HAHA_DATA_DIR: the data directory.
- HAHA_CAREER_RECORD: the master career record (Markdown).
- HAHA_PROOF_DIR: the proof-card folder whose entries.json maps claims to sources.
- HAHA_OUTPUTS_DIR: the folder holding the dated opportunity-database outputs.
"""

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RM_CLI = REPO / "bin" / "rm"
TEMPLATES_DIR = REPO / "templates"

_DEFAULT_DATA = REPO.parent / "haha-data"
_DEFAULT_RECORD = REPO.parent / "outputs" / "KANISHK_PAUL_MASTER_CAREER_RECORD.md"
_DEFAULT_PROOF = REPO.parent / "evidence"
_DEFAULT_OUTPUTS = REPO.parent / "outputs"


WORK_DIR = "work"  # inside an application folder: prompt packs, check logs, raw fetches, render scratch


def prepared(path: Path) -> Path:
    """`path`, with its parent folder created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Paths:
    data: Path
    career_record: Path
    proof_dir: Path
    outputs: Path = _DEFAULT_OUTPUTS

    @property
    def bank(self) -> Path:
        return self.data / "bank"

    @property
    def annotations(self) -> Path:
        return self.data / "annotations"

    @property
    def record_updates(self) -> Path:
        return self.data / "record_updates.yaml"

    @property
    def jobs(self) -> Path:
        return self.data / "jobs"

    @property
    def applications(self) -> Path:
        return self.data / "applications"

    @property
    def work(self) -> Path:
        """Scratch space: prompt packs and argument files for bin/rm."""
        return self.data / "work"

    @property
    def candidate(self) -> Path:
        return self.data / "candidate.yaml"

    @property
    def proof_entries(self) -> Path:
        return self.proof_dir / "entries.json"


def load() -> Paths:
    """Resolve paths from the environment, falling back to the sibling-folder defaults."""
    return Paths(
        data=_env_path("HAHA_DATA_DIR", _DEFAULT_DATA),
        career_record=_env_path("HAHA_CAREER_RECORD", _DEFAULT_RECORD),
        proof_dir=_env_path("HAHA_PROOF_DIR", _DEFAULT_PROOF),
        outputs=_env_path("HAHA_OUTPUTS_DIR", _DEFAULT_OUTPUTS),
    )
