"""`bin/oppterm`: finds the newest Opportunity Terminal under the outputs folder."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "oppterm"


def run(outputs: Path, *args: str, script: Path = SCRIPT) -> subprocess.CompletedProcess:
    env = {**os.environ, "HAHA_OUTPUTS_DIR": str(outputs)}
    return subprocess.run([str(script), *args], env=env, capture_output=True, text=True)


def page(outputs: Path, folder: str) -> Path:
    path = outputs / folder / "OPPORTUNITY_TERMINAL.html"
    path.parent.mkdir(parents=True)
    path.write_text("<!doctype html>")
    return path


def test_path_is_the_newest_dated_folder(tmp_path):
    page(tmp_path, "apply_now_2031-01-01")
    newest = page(tmp_path, "apply_now_2031-02-01")
    (tmp_path / "apply_now_2031-03-01").mkdir()  # a newer folder without a page is skipped
    done = run(tmp_path, "--path")
    assert done.returncode == 0
    assert done.stdout.strip() == str(newest)


def test_works_through_a_symlink(tmp_path):
    newest = page(tmp_path / "out", "apply_now_2031-01-01")
    link = tmp_path / "oppterm"
    link.symlink_to(SCRIPT)
    done = run(tmp_path / "out", "--path", script=link)
    assert done.returncode == 0
    assert done.stdout.strip() == str(newest)


def test_missing_page_fails_clearly(tmp_path):
    done = run(tmp_path, "--path")
    assert done.returncode == 1
    assert "no OPPORTUNITY_TERMINAL.html" in done.stderr
