import pytest

from career_tailor import bank, cli, ingest, jobspec, match, paths, selection, verify, write
from career_tailor.models import (
    BulletReview,
    Coverage,
    JobSpec,
    Match,
    Posting,
    Requirement,
    Review,
    ScreenerVerdict,
    Support,
    TailoredBullet,
)

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


# ---------------------------------------------------------------- lint


def test_clean_draft_has_no_lint(mini_bank, sel, draft):
    assert [f for f in verify.lint(draft, mini_bank, SPEC, sel) if f.severity == "error"] == []


def test_links_belong_in_headings_and_must_be_public(mini_bank, sel, draft):
    w = edited(draft, "EXP-01.b1", "Built a reproducible evaluation (github.com/ada-example/probe).")
    assert messages(verify.lint(w, mini_bank, SPEC, sel), "links") == [
        "EXP-01.b1: links go in entry headings, not bullets"
    ]
    entry = mini_bank.entries["ENG-01"]
    entry.public_links = ["https://github.com/ada-example/sketch"]  # private in the repository inventory
    assert "ENG-01 links github.com/ada-example/sketch, a private repository" in messages(
        verify.lint(draft, mini_bank, SPEC, sel), "links"
    )


def test_team_results_say_so(mini_bank, sel, draft):
    award = next(p for p in sel.picks if p.section == "Awards and selections")
    mini_bank.entries[award.entry_id].team = True
    line = next(b for b in draft.bullets if b.entry_id == award.entry_id)
    line.text = "Top-100 finish, Example ML Challenge 2030."
    assert messages(verify.lint(draft, mini_bank, SPEC, sel), "team") == [
        f"{line.claim_ids[0]}: a team result must say it is a team's"
    ]
    line.text = "Top-100 team finish, Example ML Challenge 2030."
    mini_bank.entries["ENG-01"].team = True
    assert messages(verify.lint(draft, mini_bank, SPEC, sel), "team") == [
        "ENG-01 is team work, but no bullet or the role says so"
    ]


def test_policy_rules(mini_bank, sel, draft):
    w = edited(draft, "EXP-01.b1", "Solved 13 games with Arnesh and my team.")
    mini_bank.claim("EXP-01.b2").status = "superseded"
    found = verify.lint(w, mini_bank, SPEC, sel)
    assert messages(found, "policy") == [
        "EXP-01.b2: cites EXP-01.b2, which is superseded or hidden",
        "EXP-01.b1: report scores and levels, not 'solved N games'",
        "EXP-01.b1: name Arnesh Banerjee or Arnesh Gade in full; they are different people",
    ]
    assert messages(found, "style") == ["EXP-01.b1: no first person"]


def test_style_rules(mini_bank, sel, draft):
    w = edited(draft, "EXP-01.b1", "built it on 10/22/2030,  with a hidden​mark and the model's own scorer.")
    w = edited(w, "EXP-01.b2", "Kept the model’s tone markers.")
    found = messages(verify.lint(w, mini_bank, SPEC, sel), "style")
    assert "EXP-01.b1: contains hidden or control characters" in found
    assert "EXP-01.b1: write dates as words (Oct 2026), not numbers" in found
    assert "EXP-01.b1: double space" in found and "EXP-01.b1: start with a capital letter" in found
    assert "straight and curly apostrophes are mixed; use one kind" in found


def test_acronyms_in_both_forms(mini_bank, sel, draft):
    w = edited(draft, "EXP-01.b1", "Built a reproducible evaluation of LLMs over 10,000 utterances.")
    assert messages(verify.lint(w, mini_bank, SPEC, sel), "keywords") == [
        "the posting's term appears in one form only; add “large language models” once for screeners that "
        "search the other"
    ]
    w.skills = {"Models": ["large language models"]}
    assert messages(verify.lint(w, mini_bank, SPEC, sel), "keywords") == []


def test_numbers_from_two_claims_in_one_line(mini_bank, sel, draft):
    w = draft.model_copy(deep=True)
    w.bullets = [b for b in w.bullets if b.claim_ids not in (["EXP-01.b1"], ["EXP-01.b2"])]
    w.bullets.append(TailoredBullet(entry_id="EXP-01", claim_ids=["EXP-01.b1", "EXP-01.b2"],
                                    text="Evaluated 10,000 utterances; Model A kept 97.5% versus 4.0%."))  # fmt: skip
    assert messages(verify.lint(w, mini_bank, SPEC, sel), "protocol") == [
        "EXP-01.b1+EXP-01.b2: results from EXP-01.b1, EXP-01.b2 in one line; check they come from the same protocol"
    ]


# ---------------------------------------------------------------- review


def honest_review(w, sel):
    return Review(
        job=SPEC.key,
        bullets=[BulletReview(claim_ids=b.claim_ids, entailed=True) for b in w.bullets],
        screener=[ScreenerVerdict(requirement_id="R1", verdict="meets", quote="Built a reproducible evaluation"),
                  ScreenerVerdict(requirement_id="R2", verdict="unknown")],
    )  # fmt: skip


def test_page_text_is_what_a_screener_reads(mini_bank, sel, draft):
    text = verify.page_text(mini_bank, SPEC, sel, draft)
    assert text.startswith("Ada Example\n\nML Engineer, Evaluation\n")
    assert "**" not in text and "\n- " not in text and "# " not in text
    assert "Example University — Research Collaborator, Speech · Sep 2030 – Present" in text


def test_pack_has_bullets_sources_requirements_and_page(mini_bank, sel, draft, tmp_path):
    pack = verify.write_pack(tmp_path, mini_bank, SPEC, sel, draft).read_text()
    assert "### EXP-01.b2 · Example University (team work)" in pack
    assert "- Condition: 97.5% vs 4.0% — Classifiable cases only" in pack
    assert "- R1 (basic): “build reproducible evaluations”" in pack
    assert "### Resume text" in pack and '"screener"' in pack


def test_an_honest_review_passes_and_grades(mini_bank, sel, draft):
    text = verify.page_text(mini_bank, SPEC, sel, draft)
    review = honest_review(draft, sel)
    assert verify.check_review(review, draft, SPEC, sel, text) == []
    assert verify.grade(SPEC, review) == ("A", 1.0, 0.0)


def test_review_must_cover_every_bullet_and_requirement(mini_bank, sel, draft):
    review = honest_review(draft, sel)
    gone = review.bullets.pop()
    review.bullets[0].entailed = False
    review.bullets[0].problem = "says 'benchmark' where the claim says 'evaluation'"
    review.screener.pop()
    found = verify.check_review(review, draft, SPEC, sel, verify.page_text(mini_bank, SPEC, sel, draft))
    assert f"{'+'.join(gone.claim_ids)} has no entailment review" in messages(found, "review")
    assert messages(found, "entailment") == [
        f"{'+'.join(review.bullets[0].claim_ids)} doesn't follow from its claims: says 'benchmark' where the claim "
        "says 'evaluation'"
    ]
    assert messages(found, "screener") == ["requirements the screener didn't judge: R2"]


def test_screener_quotes_are_verbatim_and_supported_asks_loop_back(mini_bank, sel, draft):
    review = honest_review(draft, sel)
    review.screener[0] = ScreenerVerdict(requirement_id="R1", verdict="meets", quote="Led evaluation at scale")
    found = verify.check_review(review, draft, SPEC, sel, verify.page_text(mini_bank, SPEC, sel, draft))
    assert messages(found, "screener")[0].startswith("R1 meets, but its quote isn't a verbatim run")
    review.screener[0] = ScreenerVerdict(requirement_id="R1", verdict="unknown")
    found = verify.check_review(review, draft, SPEC, sel, verify.page_text(mini_bank, SPEC, sel, draft))
    assert messages(found, "screener") == [
        "R1 is supported on the page but reads as unknown: rewrite its bullet in the posting's words (tailor write)"
    ]
    assert verify.grade(SPEC, review)[0] == "D"


POSTING = Posting(key=SPEC.key, ats="lever", org="acme", company="Acme", title=SPEC.title,
                  url="https://jobs.lever.co/acme/1", fetched_at="2030-01-01T00:00:00+00:00",
                  text="- build reproducible evaluations\n- experience building browser agents\n")  # fmt: skip


def test_cli_check_reports_then_grades(data_dir, capsys, draft, sel):
    p = paths.load()
    bank.save(bank.compile_bank(p), p.bank)
    folder = ingest.save_posting(p.applications, POSTING, {})
    (folder / jobspec.SPEC_FILE).write_text(SPEC.model_dump_json())
    with pytest.raises(SystemExit) as done:
        cli.main(["check", folder.name])
    assert done.value.code == 1 and "run `tailor write" in capsys.readouterr().err
    (folder / match.MATCH_FILE).write_text(MATCH.model_dump_json())
    (folder / selection.SELECTION_FILE).write_text(sel.model_dump_json())
    (folder / write.WRITING_FILE).write_text(draft.model_dump_json())
    with pytest.raises(SystemExit) as done:
        cli.main(["check", folder.name])
    out = capsys.readouterr().out
    assert done.value.code == 1  # the fixture's record wording drops a condition
    assert "error: [conditions] ENG-01.b2: keeps 20/20 per named gate" in out and "next: fill" in out
    report = (folder / verify.REPORT_FILE).read_text()
    assert (
        report.startswith("# Check report: ML Engineer, Evaluation at Acme") and "**Waiting for the review.**" in report
    )
    assert (folder / verify.PACK_FILE).is_file()

    (folder / verify.REVIEW_FILE).write_text(honest_review(draft, sel).model_dump_json())
    with pytest.raises(SystemExit):
        cli.main(["check", folder.name])
    assert "screener grade A" in capsys.readouterr().out
    report = (folder / verify.REPORT_FILE).read_text()
    assert "Estimated screener grade: **A** (basic 100% Meets, preferred 0%)." in report
    assert "| R1 | basic | meets | Built a reproducible evaluation |" in report
    assert f"{len(draft.bullets)} of {len(draft.bullets)} bullets follow from their claims." in report
