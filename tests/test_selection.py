import pytest

from career_tailor import bank, paths, selection
from career_tailor.models import JobSpec


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


def test_layout_budget():
    assert selection.LAYOUTS["a4"].budget(2) == 120


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
