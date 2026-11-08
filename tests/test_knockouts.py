import csv
import json

import pytest
import yaml

from career_tailor import cli, ingest, jobspec, knockouts, opportunities, paths
from career_tailor.models import Candidate, JobSpec, Posting, Requirement

ADA = Candidate(location="Springfield, Example State", country="Exampleland", authorized_to_work_in=["India"],
                degree_level="bachelor", degree_status="in progress", degree_expected="2031-06",
                earliest_full_time_start="2031-07")  # fmt: skip


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We sponsor visas in both the UK and US.", "offered"),
        ("Visa sponsorship & relocation support", "offered"),
        ("We are unable to sponsor visas for this role.", "not offered"),
        ("Must be authorized to work in the US without sponsorship.", "not offered"),
        ("Hybrid in London.", "not stated"),
        ("(The posting says nothing about visa sponsorship or remote work.)", "not stated"),
    ],
)
def test_sponsorship(text, expected):
    assert knockouts.sponsorship(text) == expected


@pytest.mark.parametrize(
    ("quote", "category", "visa", "status"),
    [
        ("authorized to work in India", "authorization", "not stated", "pass"),
        ("must be authorized to work in the US", "authorization", "offered", "condition"),
        ("must be authorized to work in the US", "authorization", "not offered", "blocker"),
        ("must be authorized to work in the UK", "authorization", "not stated", "check"),
        ("This is an in-person role in London or San Francisco", "location", "offered", "condition"),
        ("This is an in-person role in London", "location", "not offered", "blocker"),
        ("Remote (anywhere)", "location", "not stated", "pass"),
        ("Must be based in Springfield", "location", "not stated", "pass"),
        ("PhD in machine learning", "education", "offered", "blocker"),
        ("PhD or equivalent research experience", "education", "offered", "condition"),
        ("MS in Computer Science", "education", "offered", "blocker"),
        ("Bachelor's degree in CS", "education", "offered", "condition"),
        ("Final year of a Bachelor's, Master's, or Ph.D. degree in CS", "education", "offered", "condition"),
        ("2–5 years of backend experience", "experience", "offered", "blocker"),
        ("5+ years of industry experience", "experience", "offered", "blocker"),
        ("3+ years of research experience or equivalent", "experience", "offered", "condition"),
        ("experience shipping production code", "experience", "offered", "check"),
        ("Able to start in January", "availability", "offered", "condition"),
    ],
)
def test_judge_rules(quote, category, visa, status):
    assert knockouts.judge(quote, category, ADA, visa)[0] == status


def test_relocation_refusal_blocks_onsite_roles():
    stay = ADA.model_copy(update={"open_to_relocation": False})
    assert knockouts.judge("in-person in London", "location", stay, "offered")[0] == "blocker"


def write_outputs(root):
    db = root / "opportunity_refresh_2030-01-01"
    db.mkdir(parents=True)
    db.joinpath("opportunities.json").write_text(json.dumps([
        {"id": "AIO-1", "fields": {"Canonical opportunity name": "Acme — Research Engineer", "Organization": "Acme Labs",
                                   "Application URL": "https://jobs.lever.co/acme/aaaa/apply", "Deadline": "Rolling"}},
        {"id": "AIO-2", "fields": {"Organization": "Other", "Official URL": "https://other.example"}},
    ]))  # fmt: skip
    apply_now = root / "apply_now_2030-01-02"
    apply_now.mkdir()
    with (apply_now / "gate_decisions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "organization", "review_status", "eligibility", "deadline", "deadline_tz", "gate",
                    "cls", "reason", "blocking_field", "condition"])  # fmt: skip
        w.writerow(["AIO-1", "", "", "", "", "", "", "KEPT", "APPLY_NOW", "", "", ""])
    with (apply_now / "apply_now_ranked.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "id", "class", "score", "condition", "top_risk", "mobility_category", "lead_items"])
        w.writerow(
            ["3", "AIO-1", "APPLY_NOW", "66", "", "Very competitive", "confirmed (UK sponsorship)", "MCW; ChromeClaw"]
        )


def test_canonical_url():
    assert opportunities.canonical_url("https://www.Jobs.lever.co/acme/aaaa/apply?src=x") == "jobs.lever.co/acme/aaaa"


def test_find_joins_database_gate_and_ranking(tmp_path):
    write_outputs(tmp_path)
    match = opportunities.find(tmp_path, ["https://jobs.lever.co/acme/aaaa"])
    assert (match.id, match.organization, match.gate, match.cls) == ("AIO-1", "Acme Labs", "KEPT", "APPLY_NOW")
    assert match.score == "66/100 (rank 3)" and match.lead_items == "MCW; ChromeClaw"
    assert opportunities.find(tmp_path, ["https://nowhere.example"]) is None
    assert opportunities.find(tmp_path / "missing", ["https://jobs.lever.co/acme/aaaa"]) is None


TEXT = "## Requirements\n\n- Strong Python skills\n\n## Logistics\n\n- In-person in London. We sponsor visas.\n"


def test_cli_check_writes_knockout_report(data_dir, tmp_path, monkeypatch, capsys):
    write_outputs(tmp_path / "outputs")
    monkeypatch.setenv("HAHA_OUTPUTS_DIR", str(tmp_path / "outputs"))
    p = paths.load()
    posting = Posting(key="lever:acme:aaaa", ats="lever", org="acme", company="acme", title="Research Engineer",
                      url="https://jobs.lever.co/acme/aaaa", fetched_at="2030-01-01T00:00:00+00:00", text=TEXT)  # fmt: skip
    folder = ingest.save_posting(p.applications, posting, {})
    spec = JobSpec(key=posting.key, company="Acme Labs", title="Research Engineer", requirements=[
        Requirement(id="R1", text="Python", quote="Strong Python skills", kind="basic"),
        Requirement(id="R2", text="London", quote="In-person in London", kind="knockout", category="location"),
    ])  # fmt: skip
    (folder / jobspec.SPEC_FILE).write_text(spec.model_dump_json())
    p.data.mkdir(parents=True, exist_ok=True)
    p.candidate.write_text(yaml.safe_dump(ADA.model_dump() | {"assumptions": ["authorisation inferred"]}))
    with pytest.raises(SystemExit) as done:
        cli.main(["job", "--check", folder.name])
    assert done.value.code == 0
    assert "knockouts: 0 blocker, 1 condition" in capsys.readouterr().out
    report = (folder / knockouts.KNOCKOUT_FILE).read_text()
    assert "**Possible, with conditions.**" in report and "Visa sponsorship in the posting: **offered**" in report
    assert "AIO-1 · Acme — Research Engineer · gate **KEPT**, class **APPLY_NOW**, score 66/100 (rank 3)" in report
    assert "- authorisation inferred" in report


@pytest.mark.local
def test_real_database_knows_the_apollo_posting():
    match = opportunities.find(paths.load().outputs,
                               ["https://jobs.lever.co/apolloresearch/4a65c6e1-785a-4f88-8998-a97574afb7ee"])  # fmt: skip
    assert match.id == "AIO-00526" and match.organization == "Apollo Research" and match.cls == "APPLY_NOW"


def test_year_ranges_use_the_lower_bound():
    from career_tailor.knockouts import _YEARS

    for text in ("2-5 years", "2–5 years", "2 — 5 years", "2 to 5 yrs", "2+ years"):
        assert _YEARS.search(text).group(1) == "2", text



def test_a_move_within_the_candidates_country_is_not_a_visa_blocker():
    kolkata = Candidate(location="Kolkata, West Bengal, India", country="India", authorized_to_work_in=["India"],
                        open_to_relocation=True)
    status, note = knockouts.judge("Office in Bengaluru, 5 days a week", "location", kolkata, "not offered")
    assert status == "condition" and "no visa" in note


@pytest.mark.parametrize(
    ("quote", "status"),
    [
        ("Currently has or is in the process of obtaining a PhD degree in the field of Natural Language Processing, "
         "Speech Processing, Computer Vision, Machine Learning, Artificial Intelligence, or equivalent", "blocker"),
        ("PhD or equivalent", "condition"),
        ("PhD degree or equivalent practical experience", "condition"),
        ("Master's degree in Statistics, Mathematics, or a related field", "blocker"),
        ("Master's degree or equivalent", "condition"),
    ],
)
def test_or_equivalent_after_a_field_list_names_a_field_not_a_degree(quote, status):
    assert knockouts.judge(quote, "education", ADA, "offered")[0] == status
