import datetime as dt

import pytest

from career_tailor import paths, record
from career_tailor.extract import extract, load_updates, parse_dates
from career_tailor.models import RecordUpdate
from career_tailor.textutil import md_plain

UPDATE = RecordUpdate(id="UPD-001", date=dt.date(2031, 1, 5), entry="EDU-01", text="CGPA 6.10/10 (semesters 1–4)",
                      source="Ada, 5 Jan 2031", supersedes_text="CGPA 5.50")  # fmt: skip


@pytest.fixture
def parsed(mini_record):
    return extract(record.read(mini_record), [UPDATE])


@pytest.fixture
def by_id(parsed):
    return {e.id: e for e in parsed[1]}


def test_entries_cover_cards_awards_selections_education(by_id):
    assert list(by_id) == [
        "EDU-01", "EDU-02", "EXP-01", "EXP-02", "RES-01", "RES-02", "RES-03",
        "ENG-01", "ENG-02", "ENG-03", "LEAD-01", "LEAD-02", "AWD-01", "AWD-02", "SEL-01",
    ]  # fmt: skip
    assert "EXP-99" not in by_id  # archived section 17 is ignored
    assert by_id["SEL-01"].kind == "selection" and by_id["AWD-02"].kind == "award"
    assert by_id["SEL-01"].dates.display == "Mar 2030"  # parsed from the wording


def test_card_fields_heading_and_dates(by_id):
    exp1 = by_id["EXP-01"]
    assert (exp1.name, exp1.subtitle) == ("Example University", "Research Collaborator, Speech")
    assert exp1.fields["Location"] == "Springfield."
    assert exp1.dates.display == "Sep 2030 – Present"
    assert exp1.team  # has collaborators
    assert by_id["EXP-02"].dates.display == "Aug 2030"  # the compact public date wins
    assert by_id["ENG-01"].stack == ["TypeScript", "Node", "Playwright"]
    assert by_id["ENG-01"].dates.display == "May 2030 – Oct 2030"


def test_claims_keep_exact_record_text(parsed, mini_record):
    lines = mini_record.read_text().split("\n")
    for entry in parsed[1]:
        for claim in entry.claims:
            if claim.kind == "bullet":
                assert lines[claim.source.line_start - 1] == "- " + claim.text
            elif claim.kind == "row":
                assert claim.text in lines[claim.source.line_start - 1]


def test_claim_kinds_groups_and_notes(by_id):
    exp1 = by_id["EXP-01"]
    assert [c.id for c in exp1.claims] == ["EXP-01.b1", "EXP-01.b2"]
    assert exp1.claims[0].group == "Strong reusable bullets"
    assert exp1.notes["Scope for discussion"].startswith("10,000 utterances")
    res1 = by_id["RES-01"]
    assert [c.kind for c in res1.claims] == ["lead", "row", "row"]
    assert res1.claims[0].group == "Best achievement statement"
    assert res1.claims[1].label == "Corpus" and res1.claims[1].group == "Saved result"
    assert res1.notes["Version note"].startswith("older")
    assert by_id["RES-02"].notes["body"].startswith("The README is a plan")
    assert by_id["RES-03"].claims[1].details == "Internal experiments, Oct–Nov 2029"


def test_repos_from_links(by_id):
    assert [r.name for r in by_id["ENG-01"].repos] == ["ada-example/clawbot"]
    assert [r.name for r in by_id["EXP-02"].repos] == ["widget/widget"]


def test_award_table_rows_and_merged_card(by_id, parsed):
    awd1 = by_id["AWD-01"]
    assert awd1.name == "Example ML Challenge" and awd1.dates.display == "2030"
    assert [c.kind for c in awd1.claims] == ["fact", "lead"]  # 8.2 card merged in
    assert awd1.fields["Team achievement"] == "top 100."
    assert awd1.notes["Result bank"] == "holdout 0.981."
    assert parsed[2]["awards"] == ["Aggregate wording: site records **three hackathon wins**."]


def test_profile_and_education(parsed, by_id):
    profile = parsed[0]
    assert profile.name == "Ada Example"
    assert profile.headline == "ML Engineer — Evaluation & Agents"
    assert profile.summaries["Short recruiter summary"].startswith("ML engineer")
    edu = by_id["EDU-01"]
    assert (edu.name, edu.subtitle) == ("Example Institute of Technology, Springfield", "B.Sc., Computer Science")
    assert edu.dates.display == "Aug 2027 – Jun 2031"


def test_record_update_supersedes_old_fact(by_id, parsed):
    claims = {c.id: c for c in by_id["EDU-01"].claims}
    new = claims["EDU-01.u1"]
    assert new.text.startswith("CGPA 6.10") and new.source is None and "5 Jan 2031" in new.origin
    old = next(c for c in claims.values() if c.label == "Historical academic snapshot")
    assert (old.status, old.superseded_by, old.show_on_resume) == ("superseded", "EDU-01.u1", False)
    assert parsed[2]["problems"] == []


def test_update_problems_are_reported(mini_record):
    bad = UPDATE.model_copy(update={"entry": "EDU-09"})
    assert extract(record.read(mini_record), [bad])[2]["problems"] == ["UPD-001: no entry EDU-09"]


@pytest.mark.parametrize(
    ("raw", "display"),
    [
        ("August 2026 – present. **Location:** Kolkata.", "Aug 2026 – Present"),
        ("July – October 2025.", "Jul 2025 – Oct 2025"),
        ("July/August 2026 – present.", "Jul 2026 – Present"),
        ("August 2026 onward.", "Aug 2026 – Present"),
        ("December 2023 – February 2024.", "Dec 2023 – Feb 2024"),
        ("Sep 2026", "Sep 2026"),
        ("lifesim 2026; Horizon October 2026.", "2026"),
        ("2008–2022", "2008 – 2022"),
        ("**Selected**, April 18, 2026", "Apr 2026"),
        ("January – early April 2026.", "Jan 2026 – Apr 2026"),
        ("sometime", None),
    ],
)
def test_parse_dates(raw, display):
    assert parse_dates(raw).display == display


def test_load_updates_missing_file(tmp_path):
    assert load_updates(tmp_path / "none.yaml") == []


@pytest.mark.local
def test_real_record_extracts_every_card():
    p = paths.load()
    rec = record.read(p.career_record)
    profile, entries, notes = extract(rec, load_updates(p.record_updates))
    cards = [e for e in entries if e.kind in ("experience", "research", "engineering", "leadership")]
    assert len(cards) == 37
    bullets = [c for e in cards for c in e.claims if c.kind == "bullet"]
    assert len(bullets) == 110
    for c in bullets:
        assert rec.line(c.source.line_start) == "- " + c.text
    assert not notes["problems"], notes["problems"]
    assert profile.name and md_plain(profile.fields["Institution"])
    assert {e.id for e in entries} >= {"EDU-01", "AWD-01", "AWD-15", "SEL-09"}
