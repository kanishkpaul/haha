import pytest

from career_tailor import bank, paths, selection, verify, write
from career_tailor.models import Coverage, JobSpec, Match, Requirement, Support

R1 = Requirement(
    id="R1", text="Reproducible evaluation", quote="build reproducible evaluations", kind="basic", weight=3
)
R2 = Requirement(id="R2", text="Browser agents", quote="experience building browser agents", kind="preferred")
SPEC = JobSpec(key="test:acme:1", company="Acme", title="ML Engineer, Evaluation", location="Berlin",
               requirements=[R1, R2], keywords={"large language models": ["LLMs"]})  # fmt: skip
MATCH = Match(key=SPEC.key, track="ML evaluation", coverage=[
    Coverage(requirement_id="R1", verdict="strong",
             support=[Support(claim_id="EXP-01.b1", strength="strong", quote="Built a reproducible evaluation")]),
    Coverage(requirement_id="R2", verdict="partial", note="terminal only",
             support=[Support(claim_id="ENG-01.b1", strength="partial", quote="terminal browser agent with")]),
])  # fmt: skip


@pytest.fixture
def mini_bank(data_dir):
    return bank.compile_bank(paths.load())


@pytest.fixture
def sel(mini_bank):
    return selection.solve(mini_bank, SPEC, MATCH, selection.PlanEdits(pin=["EXP-01.b2"]))


@pytest.fixture
def draft(mini_bank, sel):
    """The record's wording, cut at a word boundary where a line would overflow its budget."""
    w = write.source_writing(mini_bank, SPEC, sel)
    picks = {p.entry_id: p for p in sel.picks}
    for b in w.bullets:
        budget = picks[b.entry_id].lines[b.claim_ids[0]] * selection.LAYOUTS[sel.paper].chars_per_line
        if len(b.text) > budget:
            b.text = b.text[: budget - 1].rsplit(" ", 1)[0].rstrip(",;") + "."
    return w


def edited(w, claim_id, text):
    for b in w.bullets:
        if b.claim_ids == [claim_id]:
            b.text = text
    return w


def messages(findings, check):
    return [f.message for f in findings if f.check == check]


def test_names_skip_clause_openers_and_split_compounds():
    text = (
        "Combined Unity tooling, a ScreenCaptureKit recorder; Python checks (Qwen3-VL 8B) on llama.cpp for ISRO's team."
    )
    assert verify.names(text) == ["Unity", "ScreenCaptureKit", "Qwen3", "VL", "llama.cpp", "ISRO"]
    assert verify.names("Ran direct-vs-English-pivot comparisons.") == ["English"]


def test_record_wording_has_known_names_and_numbers_but_may_omit_conditions(mini_bank, sel, draft):
    findings = verify.facts(draft, mini_bank, sel)
    assert {f.check for f in findings} <= {"conditions"}
    assert "ENG-01.b2: keeps 20/20 per named gate but drops its condition: Development fixtures, not open-web rate" in (
        messages(findings, "conditions")
    )


def test_names_and_tools_must_come_from_the_claims(mini_bank, sel, draft):
    w = edited(draft, "EXP-01.b1", "Built a reproducible evaluation over 10,000 utterances with PyTorch at Google.")
    assert messages(verify.facts(w, mini_bank, sel), "names") == [
        "EXP-01.b1: names PyTorch, Google, which its claims don't"
    ]


def test_numbers_must_come_from_the_claims(mini_bank, sel, draft):
    w = edited(draft, "EXP-01.b1", "Built a reproducible evaluation over 10,000 utterances in 2031.")
    assert messages(verify.facts(w, mini_bank, sel), "numbers") == ["EXP-01.b1: states 2031, which its claims don't"]


def test_a_kept_metric_keeps_its_condition(mini_bank, sel, draft):
    w = edited(draft, "EXP-01.b2", "Found that Model A kept 97.5% of tone markers versus 4.0% for Model B.")
    assert own(verify.facts(w, mini_bank, sel)) == [
        "EXP-01.b2: keeps 97.5% vs 4.0% but drops its condition: Classifiable cases only"
    ]
    w = edited(draft, "EXP-01.b2", "Model A kept 97.5% of tone markers versus 4.0% for Model B (classifiable cases).")
    assert own(verify.facts(w, mini_bank, sel)) == []
    w = edited(draft, "EXP-01.b2", "Compared tone-marker retention between Model A and Model B.")
    assert own(verify.facts(w, mini_bank, sel)) == []  # no metric, no condition needed


def own(findings):
    return [m for m in messages(findings, "conditions") if m.startswith("EXP-01.b2")]
