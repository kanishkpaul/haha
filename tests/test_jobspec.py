import json

import pytest

from career_tailor import cli, ingest, jobspec
from career_tailor.models import JobSpec, Posting, Requirement

TEXT = """\
## About the role

You'll run evals on frontier models.

## You may be a good fit if you

- Have strong Python skills and are comfortable with production systems
- Care deeply about measurement quality, thinking twice before trusting a number

NICE TO HAVE

- Experience with RLHF or other post-training methods

LOGISTICS

- Location: in-person in London
"""
POSTING = Posting(key="lever:acme:1", ats="lever", org="acme", company="acme", title="Research Engineer, Evals",
                  fetched_at="2030-01-01T00:00:00+00:00", text=TEXT)  # fmt: skip


def req(id_, quote, kind="basic", category="skill", **kw):
    return Requirement(id=id_, text=quote[:20], quote=quote, kind=kind, category=category, **kw)


def good_spec(**changes):
    spec = JobSpec(
        key="lever:acme:1", company="Acme", title="Research Engineer, Evals",
        requirements=[
            req("R1", "strong Python skills", weight=3),
            req("R2", "thinking twice before trusting a number", category="trait"),
            req("R3", "Experience with RLHF or other post-training methods", kind="preferred", weight=1),
            req("R4", "in-person in London", kind="knockout", category="location"),
        ],
        keywords={"Python": [], "RLHF": ["reinforcement learning from human feedback"]},
    )  # fmt: skip
    return spec.model_copy(update=changes)


def test_norm_unifies_quotes_dashes_bullets_and_case():
    assert jobspec.norm("- **We’re** looking — for  “you”") == 'we\'re looking - for "you"'


def test_requirement_bullets_follow_requirement_headings():
    assert jobspec.requirement_bullets(TEXT) == [
        "Have strong Python skills and are comfortable with production systems",
        "Care deeply about measurement quality, thinking twice before trusting a number",
        "Experience with RLHF or other post-training methods",
    ]


def test_a_grounded_spec_passes():
    report = jobspec.validate(good_spec(), POSTING)
    assert report.ok, report.findings
    assert report.findings == []


def test_errors_for_title_paraphrase_duplicates_and_missing_basics():
    spec = good_spec(title="Research Engineer (Evals)", requirements=[
        req("R1", "excellent Python programming ability"),
        req("R1", "Experience with RLHF or other post-training methods", kind="preferred"),
    ])  # fmt: skip
    found = {(f.check, f.severity) for f in jobspec.validate(spec, POSTING).findings}
    assert {("title", "error"), ("quote", "error"), ("ids", "error")} <= found
    only_preferred = good_spec(
        requirements=[req("R1", "Experience with RLHF or other post-training", kind="preferred")]
    )
    assert ("coverage", "error") in {(f.check, f.severity) for f in jobspec.validate(only_preferred, POSTING).findings}


def test_warnings_for_recall_keywords_short_quotes_and_odd_knockouts():
    spec = good_spec(
        requirements=[req("R1", "strong Python skills"), req("R2", "evals", kind="knockout", category="skill")],
        keywords={"Kubernetes": ["k8s"]},
    )
    messages = [f.message for f in jobspec.validate(spec, POSTING).findings if f.severity == "warning"]
    assert any("not covered" in m and "measurement quality" in m for m in messages)
    assert any("'Kubernetes'" in m for m in messages)
    assert any("very short" in m for m in messages)
    assert any("knockout but its category is skill" in m for m in messages)


def make_folder(tmp_path):
    folder = ingest.save_posting(tmp_path / "applications", POSTING, {})
    jobspec.write_pack(folder)
    return folder


def test_pack_carries_key_title_posting_and_schema(tmp_path):
    folder = make_folder(tmp_path)
    pack = (folder / jobspec.PACK_FILE).read_text()
    assert "`lever:acme:1`" in pack and "exactly `Research Engineer, Evals`" in pack
    assert "You'll run evals on frontier models." in pack and '"quote"' in pack


def test_check_folder_states(tmp_path):
    folder = make_folder(tmp_path)
    report, spec = jobspec.check_folder(folder)
    assert not report.ok and spec is None and "no jobspec.json" in report.findings[0].message
    (folder / jobspec.SPEC_FILE).write_text(json.dumps({"key": "x"}))
    report, _ = jobspec.check_folder(folder)
    assert report.findings[0].check == "schema"
    (folder / jobspec.SPEC_FILE).write_text(good_spec().model_dump_json())
    report, spec = jobspec.check_folder(folder)
    assert report.ok and spec.company == "Acme"
    assert "**OK**" in (folder / jobspec.CHECK_FILE).read_text()


def test_cli_job_writes_pack_and_checks(data_dir, tmp_path, capsys):
    jd = tmp_path / "acme-research.md"
    jd.write_text("# Research Engineer, Evals\n\n" + TEXT)
    with pytest.raises(SystemExit):
        cli.main(["job", str(jd)])
    assert "next: fill" in capsys.readouterr().out
    folder = next((data_dir / "applications").glob("acme-research-engineer-evals-*"))
    assert (folder / jobspec.PACK_FILE).is_file()
    with pytest.raises(SystemExit) as done:
        cli.main(["job", "--check", "acme-research"])
    assert done.value.code == 1
    spec = good_spec(key="file:acme-research")
    (folder / jobspec.SPEC_FILE).write_text(spec.model_dump_json())
    with pytest.raises(SystemExit) as done:
        cli.main(["job", "--check", folder.name])
    assert done.value.code == 0
    with pytest.raises(SystemExit) as done:
        cli.main(["job", "--check", "nothing-matches"])
    assert done.value.code == 1
