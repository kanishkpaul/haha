import pytest

from career_tailor import numbers, paths, record
from career_tailor.extract import extract, load_updates
from career_tailor.tables import attach, attach_metrics, read_tables, resolve
from career_tailor.textutil import md_plain


@pytest.fixture
def built(mini_record):
    rec = record.read(mini_record)
    _, entries, _ = extract(rec)
    tables = read_tables(rec)
    attach_metrics(entries)
    problems = attach(tables, entries)
    return tables, {e.id: e for e in entries}, problems


def test_reference_tables(built):
    t, _, _ = built
    assert [s.area for s in t.skills][:2] == ["Core languages", "ML frameworks"]
    assert t.skills[1].skills == ["PyTorch", "Hugging Face Transformers/Hub"]
    assert t.skill_notes[0].startswith("Research familiarity")
    assert t.rules[1] == "Give team credit precisely: Teamfold is a team project."
    assert [(r.name, r.visibility) for r in t.repos] == [("ada-example/clawbot", "public"),
                                                         ("ada-example/sketch", "private")]  # fmt: skip
    assert t.repos[1].relationship == "fork of other/sketch"
    assert t.metric_notes == ["Three versus four wins: use three."]


def test_every_claim_number_is_a_metric(built):
    _, by_id, _ = built
    for entry in by_id.values():
        for claim in entry.claims:
            assert {m.text for m in claim.metrics} == {m.text for m in numbers.extract_numbers(md_plain(claim.text))}
    assert [m.text for m in by_id["EXP-01"].claims[1].metrics] == ["97.5%", "4.0%", "1,234"]


def test_metric_conditions_attach_to_matching_claims(built):
    t, by_id, problems = built
    rows = {r.work: r for r in t.metrics}
    assert "EXP-01.b2" in rows["Model A tone"].claim_ids
    assert "RES-01.r2" in rows["Model A tone"].claim_ids
    assert "ENG-02.b2" in rows["Teamfold ownership"].claim_ids
    assert any("Classifiable cases only" in c for c in by_id["EXP-01"].claims[1].conditions)
    assert problems == ["metric row matches no claim: Ghost result | 99.99% accuracy"]
    assert "RES-01.r2" not in rows["Teamfold ownership"].claim_ids


def test_repo_visibility_flows_to_entries(built):
    _, by_id, _ = built
    assert by_id["ENG-01"].repos[0].visibility == "public"
    assert by_id["EXP-02"].repos[0].visibility == "unknown"  # not in the inventory


def test_tracks_resolve_to_entries(built):
    t, by_id, _ = built
    evaluation = t.tracks[0]
    assert evaluation.lead_entries == ["RES-01", "EXP-01"]
    assert "ENG-01" in evaluation.support_entries and "EXP-02" in evaluation.support_entries
    assert t.tracks[1].unresolved == ["Holographic engine"]
    assert by_id["ENG-02"].tracks == ["Agent infrastructure"]


def test_resolve_prefers_exact_alias_and_acronyms(built):
    _, by_id, _ = built
    entries = list(by_id.values())
    assert resolve("Small Tools", entries) == ["ENG-03"]
    assert resolve("Debate bot", entries) == ["RES-03.r2"]
    assert resolve("EMC", entries) == ["AWD-01"]  # acronym of Example ML Challenge


@pytest.mark.local
def test_real_record_tables_attach():
    p = paths.load()
    rec = record.read(p.career_record)
    _, entries, _ = extract(rec, load_updates(p.record_updates))
    tables = read_tables(rec)
    attach_metrics(entries)
    problems = attach(tables, entries)
    assert len(tables.skills) == 22 and len(tables.tracks) == 12 and len(tables.repos) == 26
    assert len(tables.metrics) == 33
    assert all("kept as a note" in p for p in problems), problems
    assert {"RES-03", "ENG-01", "RES-01"} <= set(
        next(t for t in tables.tracks if t.target.startswith("Evals")).lead_entries
    )
