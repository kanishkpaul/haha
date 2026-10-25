import pytest

from career_tailor import assemble, bank, paths, selection, write
from career_tailor.models import Coverage, JobSpec, Match, Requirement, SourceRef, Support

R1 = Requirement(
    id="R1", text="Reproducible evaluation", quote="build reproducible evaluations", kind="basic", weight=3
)
SPEC = JobSpec(key="test:acme:1", company="Acme", title="ML Engineer, Evaluation", location="Berlin", requirements=[R1])
MATCH = Match(key=SPEC.key, track="ML evaluation", coverage=[
    Coverage(requirement_id="R1", verdict="strong",
             support=[Support(claim_id="EXP-01.b1", strength="strong", quote="Built a reproducible evaluation")]),
])  # fmt: skip


@pytest.fixture
def mini_bank(data_dir):
    return bank.compile_bank(paths.load())


@pytest.fixture
def sel(mini_bank):
    return selection.solve(mini_bank, SPEC, MATCH)


@pytest.fixture
def draft(mini_bank, sel):
    return write.source_writing(mini_bank, SPEC, sel)


def test_every_bullet_is_traced_to_its_claims_and_record_lines(mini_bank, sel, draft):
    prov = assemble.provenance(mini_bank, SPEC, sel, draft)
    assert prov.record_sha256 == mini_bank.manifest.record_sha256
    assert prov.lines[0].section == "Headline" and prov.lines[0].source == "posting title"
    bullets = [x for x in prov.lines if x.section not in ("Headline", "Education", "Skills")]
    assert [x.text for x in bullets] == [b.text for b in draft.bullets]
    for line, b in zip(bullets, draft.bullets, strict=True):
        assert [c.claim_id for c in line.claims] == b.claim_ids
        for c in line.claims:
            assert c.text == mini_bank.claim(c.claim_id).text
            assert c.record_lines == mini_bank.claim(c.claim_id).source
    skills = [x for x in prov.lines if x.section == "Skills"]
    assert len(skills) == sum(len(t) for t in draft.skills.values())
    assert all(x.source.startswith("record skills bank") for x in skills)


def test_record_headline_and_education_sources(mini_bank, sel, draft):
    track = mini_bank.tables.tracks[0]
    prov = assemble.provenance(mini_bank, SPEC, sel, draft.model_copy(update={"headline": track.headline}))
    assert prov.lines[0].source == f"record track headline ({track.target})"
    edu = [x for x in prov.lines if x.section == "Education"]
    assert edu and all(x.entry_id == "EDU-01" for x in edu)


def test_check_passes_a_complete_trace(mini_bank, sel, draft):
    prov = assemble.provenance(mini_bank, SPEC, sel, draft)
    record = paths.load().career_record
    assert assemble.check(prov, sel, record) == []


def test_check_catches_untraced_lines_and_bad_ranges(mini_bank, sel, draft):
    prov = assemble.provenance(mini_bank, SPEC, sel, draft)
    record = paths.load().career_record
    bullets = [x for x in prov.lines if x.section not in ("Headline", "Education", "Skills")]
    bullets[0].claims[0].record_lines = SourceRef(line_start=10**6, line_end=10**6)
    bullets[1].claims = []
    unplanned = sel.model_copy(update={"picks": [p for p in sel.picks if p.entry_id != bullets[2].entry_id]})
    messages = [f.message for f in assemble.check(prov, unplanned, record)]
    assert any("fall outside the record" in m for m in messages)
    assert "line has no claims" in messages
    assert any("isn't in the plan" in m for m in messages)
