"""`tailor run`: stops at each prompt pack and gate, resumes after approval, and asks again after a change."""

import os

import pytest

from career_tailor import assemble, cli, jobspec, match, selection, verify, write


@pytest.fixture
def app(data_dir, monkeypatch):
    folder = data_dir / "applications" / "acme-ml-engineer-2031-01-01"
    folder.mkdir(parents=True)
    (folder / "posting.json").write_text("{}")
    calls = []

    def stage(name, effect=None):
        def run(args):
            calls.append(name)
            if effect:
                effect()
            return 0

        return run

    monkeypatch.setattr(jobspec, "write_pack", lambda f: calls.append("spec pack"))
    monkeypatch.setattr(cli, "cmd_job", stage("job"))
    monkeypatch.setattr(
        cli,
        "cmd_plan",
        stage(
            "plan",
            lambda: (folder / match.MATCH_FILE).is_file() and (folder / selection.SELECTION_FILE).write_text("{}"),
        ),
    )
    monkeypatch.setattr(cli, "cmd_write", stage("write"))
    monkeypatch.setattr(
        cli,
        "cmd_check",
        stage(
            "check",
            lambda: (folder / verify.REVIEW_FILE).is_file() and (folder / verify.REPORT_FILE).write_text("report"),
        ),
    )
    return folder, calls


def run(folder, approve=False):
    return cli.main_run(["run", folder.name] + (["--approve"] if approve else []))


def touch_later(path, text):
    path.write_text(text)
    later = os.stat(path.parent / selection.SELECTION_FILE).st_mtime + 10
    os.utime(path, (later, later))


def test_stops_for_each_prompt_pack(app):
    folder, calls = app
    assert run(folder) == 0 and calls == ["spec pack"]
    (folder / jobspec.SPEC_FILE).write_text("{}")
    calls.clear()
    assert run(folder) == 0 and calls == ["job", "plan"]  # no match.json yet: stops after the match pack
    assert not (folder / selection.SELECTION_FILE).is_file()


def test_two_gates_then_ready(app, capsys):
    folder, calls = app
    (folder / jobspec.SPEC_FILE).write_text("{}")
    (folder / match.MATCH_FILE).write_text("{}")
    assert run(folder) == 0 and calls[-1] == "plan" and "gate 1 (plan)" in capsys.readouterr().out
    calls.clear()
    assert run(folder, approve=True) == 0 and calls == ["job", "write"]  # approved; no draft yet
    assert assemble.approved(folder, "plan") and not assemble.approved(folder, "final")
    (folder / write.WRITING_FILE).write_text("{}")
    calls.clear()
    assert run(folder) == 0 and calls == ["job", "write", "check"]  # no review yet
    (folder / verify.REVIEW_FILE).write_text("{}")
    assert run(folder) == 0 and "gate 2 (final)" in capsys.readouterr().out
    assert run(folder, approve=True) == 0 and "final check approved" in capsys.readouterr().out
    assert run(folder) == 0 and "ready:" in capsys.readouterr().out


def test_one_approval_passes_one_gate(app, capsys):
    folder, _ = app
    for name in (jobspec.SPEC_FILE, match.MATCH_FILE, write.WRITING_FILE, verify.REVIEW_FILE):
        (folder / name).write_text("{}")
    assert run(folder, approve=True) == 0
    out = capsys.readouterr().out
    assert "plan approved" in out and "gate 2 (final)" in out and not assemble.approved(folder, "final")


def test_a_changed_plan_needs_approval_again(app, capsys):
    folder, calls = app
    for name in (jobspec.SPEC_FILE, match.MATCH_FILE, write.WRITING_FILE, verify.REVIEW_FILE):
        (folder / name).write_text("{}")
    run(folder, approve=True)
    run(folder, approve=True)
    assert assemble.approved(folder, "final")
    touch_later(folder / selection.EDITS_FILE, "pin: [ENG-01]\n")
    calls.clear()
    assert run(folder) == 0 and calls == ["job", "plan"]  # stale plan: re-planned and stopped at gate 1
    assert "changed since it was approved" in capsys.readouterr().out
    run(folder, approve=True)
    assert "final" not in assemble.approvals(folder)  # a new plan means a new page


def test_a_current_plan_is_not_replanned(app):
    folder, calls = app
    (folder / jobspec.SPEC_FILE).write_text("{}")
    (folder / match.MATCH_FILE).write_text("{}")
    (folder / selection.SELECTION_FILE).write_text("{}")
    later = os.stat(folder / match.MATCH_FILE).st_mtime + 10
    os.utime(folder / selection.SELECTION_FILE, (later, later))
    assert run(folder) == 0 and calls == ["job"]


def test_status_names_the_stage_and_next_step(app):
    folder, _ = app
    assert assemble.status(folder).stage == "job spec"
    for name in (jobspec.SPEC_FILE, match.MATCH_FILE, selection.SELECTION_FILE):
        (folder / name).write_text("{}")
    st = assemble.status(folder, "acme")
    assert st.stage == "gate 1" and st.next.endswith("tailor run acme --approve")
    assemble.approve(folder, "plan")
    assert assemble.status(folder).stage == "write"
    (folder / verify.REPORT_FILE).write_text("Estimated screener grade: **B** (basic 80% Meets).")
    assert assemble.status(folder).grade == "B"


def test_short_names_are_unique_prefixes(tmp_path):
    names = ["acme-ml-engineer-2031", "acme-research-2031", "zeta-agents-2031"]
    folders = [tmp_path / n for n in names]
    assert list(assemble.short_names(folders).values()) == ["acme-ml", "acme-research", "zeta"]


def test_run_finds_a_job_file_in_the_jobs_folder(data_dir, monkeypatch):
    (data_dir / "jobs").mkdir(parents=True)
    (data_dir / "jobs" / "acme.md").write_text("# ML Engineer")
    seen = []
    monkeypatch.setattr(cli, "ingest_posting", lambda p, source, company: seen.append(source))
    cli.main_run(["run", "acme.md"])
    assert seen == [str(data_dir / "jobs" / "acme.md")]
