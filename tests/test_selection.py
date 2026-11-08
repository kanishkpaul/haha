import pytest

from career_tailor import bank, paths, selection
from career_tailor.models import Coverage, JobSpec, Match, PlanEdits, Requirement, Support


@pytest.fixture
def mini_bank(data_dir):
    return bank.compile_bank(paths.load())


@pytest.mark.parametrize(
    "location, paper",
    [("San Francisco, CA | Seattle, WA", "letter"), ("Toronto (remote)", "letter"), ("London & San Francisco", "a4"),
     ("Paris; New York", "a4"), ("", "a4")],
)  # fmt: skip
def test_paper_follows_the_first_location(location, paper):
    assert selection.paper_for(JobSpec(key="k", company="c", title="t", location=location)) == paper


def test_line_costs(mini_bank):
    layout = selection.LAYOUTS["letter"]
    row = mini_bank.claim("RES-03.r1")
    assert selection.display_text(row) == "Sampler: Designed and implemented a composite sampler."
    assert selection.claim_lines(row, mini_bank.entries["RES-03"], layout) == 1
    long = mini_bank.claim("ENG-01.b2")
    long.text = "word " * 100
    assert selection.claim_lines(long, mini_bank.entries["ENG-01"], layout) == layout.max_bullet_lines
    award = mini_bank.entries["AWD-01"]
    assert selection.claim_lines(award.claims[0], award, layout) == 1
    short = selection.layout_for("letter", PlanEdits(one_line=["ENG-01.b2"]))
    assert selection.claim_lines(long, mini_bank.entries["ENG-01"], short) == 1  # `one_line:` budgets it at one


def test_layout_budget():
    assert selection.LAYOUTS["a4"].budget(2) == 120


def test_dense_layout_fits_more_per_line_and_page():
    plain, dense = selection.layout_for("a4", PlanEdits()), selection.layout_for("a4", PlanEdits(dense=True))
    assert plain == selection.LAYOUTS["a4"]
    assert dense.chars_per_line > plain.chars_per_line and dense.lines_per_page > plain.lines_per_page
    assert dense.skills > plain.skills  # the dense page carries a fuller skills block
    assert selection.layout_for("a4", PlanEdits(dense=True), lines_per_page=50).lines_per_page == 50




def test_entries_sharing_a_proof_card_exclude_each_other(mini_bank):
    for entry_id in ("EXP-01", "RES-01"):
        for claim in mini_bank.entries[entry_id].claims:
            claim.evidence_ids = ["CAREER-001"]
    pairs = {(a, b): why for a, b, why in selection.exclusions(mini_bank)}
    exp = tuple(c.id for c in mini_bank.entries["EXP-01"].claims)
    res = tuple(c.id for c in mini_bank.entries["RES-01"].claims)
    assert pairs[(exp, res)] == "same work (proof card CAREER-001)"


def test_partial_citation_excludes_only_the_citing_claims(mini_bank):
    mini_bank.claim("ENG-01.b1").evidence_ids = ["CAREER-002"]
    mini_bank.claim("ENG-02.b1").evidence_ids = ["CAREER-002"]
    mini_bank.claim("ENG-02.b2").evidence_ids = ["CAREER-002"]
    pairs = [(a, b) for a, b, _ in selection.exclusions(mini_bank)]
    assert (("ENG-01.b1",), ("ENG-02.b1", "ENG-02.b2")) in pairs


def test_award_lines_sharing_a_card_can_both_appear(mini_bank):
    mini_bank.entries["AWD-01"].claims[0].evidence_ids = ["CAREER-003"]
    mini_bank.entries["AWD-02"].claims[0].evidence_ids = ["CAREER-003"]
    assert not [w for _, _, w in selection.exclusions(mini_bank) if "CAREER-003" in w]


def test_annotation_same_work(mini_bank):
    mini_bank.entries["AWD-02"].same_work = ["ENG-03.r1"]
    pairs = {(a, b): why for a, b, why in selection.exclusions(mini_bank)}
    award = tuple(c.id for c in mini_bank.entries["AWD-02"].claims)
    assert pairs[(award, ("ENG-03.r1",))] == "same work (annotation)"


def test_one_result_stated_twice_in_an_entry(mini_bank):
    mini_bank.claim("EXP-01.b1").text = "Found 54/55 events in the held-out set."
    mini_bank.claim("EXP-01.b2").text = "Detected 54/55 events with two channels."
    mini_bank.claim("ENG-01.b1").text = "Unrelated 54/55 elsewhere."  # different entry, no shared card
    pairs = {(a, b): why for a, b, why in selection.exclusions(mini_bank)}
    assert pairs[(("EXP-01.b1",), ("EXP-01.b2",))] == "same result (54/55)"
    assert not any("ENG-01.b1" in a + b for a, b in pairs if pairs[(a, b)].startswith("same result"))


# ---------------------------------------------------------------- solver

R1 = Requirement(
    id="R1", text="Reproducible evaluation", quote="build reproducible evaluations", kind="basic", weight=3
)
R2 = Requirement(id="R2", text="Browser agents", quote="experience building browser agents", kind="preferred")
SPEC = JobSpec(key="test:acme:1", company="Acme", title="ML Engineer", location="Berlin", requirements=[R1, R2])


def coverage(**supports):
    return Match(key=SPEC.key, track="ML evaluation", coverage=[
        Coverage(requirement_id=rid, verdict="strong" if any(s == "strong" for _, s in sups) else "partial",
                 support=[Support(claim_id=c, strength=s, quote="x x x") for c, s in sups])
        for rid, sups in supports.items()
    ])  # fmt: skip


MATCH = coverage(R1=[("EXP-01.b1", "strong")], R2=[("ENG-01.b1", "partial")])


@pytest.fixture
def tight(monkeypatch):
    """A page with room for only a few items, so the solver has to choose."""
    layouts = {k: selection.Layout(paper=k, lines_per_page=32, chars_per_line=100) for k in ("letter", "a4")}
    monkeypatch.setattr(selection, "LAYOUTS", layouts)


def test_plan_fits_the_budget_and_follows_the_rules(mini_bank):
    sel = selection.solve(mini_bank, SPEC, MATCH)
    assert sel.paper == "a4" and sel.pages == 1 and sel.track == "ML evaluation"
    assert sel.lines_used <= sel.lines_budget == 60
    sections = [p.section for p in sel.picks]
    assert "Experience" in sections and sections == sorted(sections, key=selection.SECTION_ORDER.index)
    projects = [p for p in sel.picks if p.section in ("Research", "Projects")]
    assert 2 <= len(projects) <= 4
    assert all(len(p.claim_ids) == 1 for p in sel.picks if p.section == "Awards and selections")
    assert sel.covered == {"R1": "strong", "R2": "partial"}
    assert next(p for p in sel.picks if p.entry_id == "EXP-01").covers == ["R1"]


def test_tight_page_keeps_what_the_job_asks_for(mini_bank, tight):
    sel = selection.solve(mini_bank, SPEC, MATCH)
    picked = {c for p in sel.picks for c in p.claim_ids}
    assert {"EXP-01.b1", "ENG-01.b1"} <= picked
    assert sel.lines_used <= 32
    everything = {c.id for e in mini_bank.entries.values() if e.kind != "education" for c in e.claims}
    assert len(picked) < len(everything) / 2  # the page really is tight


def test_same_work_is_never_picked_twice(mini_bank):
    for entry_id in ("EXP-01", "RES-01"):
        for claim in mini_bank.entries[entry_id].claims:
            claim.evidence_ids = ["CAREER-001"]
    m = coverage(R1=[("EXP-01.b1", "strong"), ("RES-01.l1", "strong")], R2=[("ENG-01.b1", "partial")])
    sel = selection.solve(mini_bank, SPEC, m)
    entries = {p.entry_id for p in sel.picks}
    assert len(entries & {"EXP-01", "RES-01"}) == 1
    left = ({"EXP-01", "RES-01"} - entries).pop()
    assert sel.excluded[left] == f"same work as {(entries & {'EXP-01', 'RES-01'}).pop()}"


def test_pins_and_bans(mini_bank, tight):
    edits = PlanEdits(pin=["ENG-03.r1"], ban=["EXP-01"])
    sel = selection.solve(mini_bank, SPEC, MATCH, edits)
    picked = {c for p in sel.picks for c in p.claim_ids}
    assert "ENG-03.r1" in picked and not any(c.startswith("EXP-01.") for c in picked)
    assert sel.covered["R1"] == "none"
    assert sel.excluded["EXP-01"] == "banned in plan.edits.yaml"
    assert sel.edits == edits


def test_bad_edits_are_reported(mini_bank, tight):
    with pytest.raises(selection.SelectionError, match="ENG-99"):
        selection.solve(mini_bank, SPEC, MATCH, PlanEdits(pin=["ENG-99"]))
    everything = [c.id for e in mini_bank.entries.values() if e.kind != "education" for c in e.claims]
    with pytest.raises(selection.SelectionError, match="no plan fits"):
        selection.solve(mini_bank, SPEC, MATCH, PlanEdits(pin=everything))


def test_pages_override_and_determinism(mini_bank):
    two = selection.solve(mini_bank, SPEC, MATCH, PlanEdits(pages=2))
    assert two.pages == 2 and two.lines_budget == 120
    assert selection.solve(mini_bank, SPEC, MATCH) == selection.solve(mini_bank, SPEC, MATCH)


def test_dense_plan_has_a_bigger_budget_and_looser_limits(mini_bank):
    plain = selection.solve(mini_bank, SPEC, MATCH, PlanEdits())
    dense = selection.solve(mini_bank, SPEC, MATCH, PlanEdits(dense=True))
    assert dense.lines_budget > plain.lines_budget and dense.edits.dense
    assert len(dense.picks) >= len(plain.picks)


def test_lead_opens_a_section(mini_bank):
    plain = selection.solve(mini_bank, SPEC, MATCH)
    exp = [p.entry_id for p in plain.picks if p.section == "Experience"]
    assert len(exp) > 1
    led = selection.solve(mini_bank, SPEC, MATCH, edits=PlanEdits(lead=[exp[-1]]))
    assert [p.entry_id for p in led.picks if p.section == "Experience"][0] == exp[-1]


def test_pinned_awards_lift_the_award_cap(mini_bank):
    awards = [e.id for e in mini_bank.entries.values() if e.kind in selection.ONE_LINE_KINDS]
    cap = selection.Rules.for_pages(1).awards[1]
    if len(awards) > cap:
        sel = selection.solve(mini_bank, SPEC, MATCH, edits=PlanEdits(pin=awards[: cap + 1]))
        assert sum(p.entry_id in awards for p in sel.picks) == cap + 1
