import pytest

from career_tailor import bank, cli, ingest, jobspec, match, paths
from career_tailor.models import Coverage, JobSpec, Match, Posting, Requirement, Support


@pytest.fixture
def mini_bank(data_dir):
    return bank.compile_bank(paths.load())


def spec(*reqs, title="ML Engineer, Evaluation", keywords=None):
    return JobSpec(key="test:acme:1", company="Acme", title=title, requirements=list(reqs), keywords=keywords or {})


R_EVAL = Requirement(id="R1", text="Reproducible evaluation", quote="build reproducible evaluations", kind="basic")
R_AGENT = Requirement(id="R2", text="Browser agents", quote="experience building browser agents", kind="basic")
R_LOCAL = Requirement(id="R3", text="Local inference", quote="Run models locally with llama.cpp", kind="basic")
R_KO = Requirement(id="R4", text="London", quote="In-person in London", kind="knockout", category="location")


def test_stem_and_tokens():
    assert {match.stem(w) for w in ("evals", "evaluation", "evaluate")} == {"eval"}
    assert match.stem("agents") == "agent" and match.stem("building") == "build"
    assert match.tokens("**Built** a [browser agent](https://x.y) with the C++ API") == [
        "built", "browser", "agent", "c++", "api"
    ]  # fmt: skip


def test_bm25_prefers_rare_matching_terms():
    index = match.BM25([["eval", "python"], ["python", "web"], ["python", "web", "ui"]])
    scores = index.scores(["eval", "python"])
    assert scores[0] > scores[1] > 0 and scores[1] > scores[2]


def test_retrieve_finds_relevant_claims_and_skips_knockouts(mini_bank):
    r = match.retrieve(mini_bank, spec(R_EVAL, R_AGENT, R_KO))
    assert set(r.hits) == {"R1", "R2"}
    assert r.hits["R1"][0].claim_id == "EXP-01.b1"  # "Built a reproducible evaluation ..."
    assert r.hits["R2"][0].claim_id.startswith("ENG-01.")  # Clawbot, the browser agent
    assert all(c.score > 0 for cands in r.hits.values() for c in cands)


def test_skill_hits_are_reported(mini_bank):
    r = match.retrieve(mini_bank, spec(R_LOCAL))
    hits = [c for c in r.hits["R3"] if c.skills]
    assert hits and hits[0].claim_id == "ENG-01.b2" and hits[0].skills == ["llama.cpp/GGUF"]


def test_tracks_rank_and_boost_lead_entries(mini_bank):
    r = match.retrieve(mini_bank, spec(R_EVAL, R_AGENT))
    assert r.tracks[0] == "ML evaluation"
    roles = {c.claim_id.split(".")[0]: c.track for c in r.hits["R1"]}
    assert roles.get("EXP-01") == "lead"
    agent = match.retrieve(mini_bank, spec(R_AGENT, title="AI Engineer, Agents"))
    assert agent.tracks[0] == "Agent infrastructure"


def test_hidden_and_superseded_claims_are_not_candidates(mini_bank):
    claim = mini_bank.claim("EXP-01.b1")
    claim.show_on_resume = False
    r = match.retrieve(mini_bank, spec(R_EVAL))
    assert "EXP-01.b1" not in [c.claim_id for c in r.hits["R1"]]


def test_at_most_three_candidates_per_entry(mini_bank):
    r = match.retrieve(mini_bank, spec(R_EVAL, R_AGENT, R_LOCAL))
    for cands in r.hits.values():
        entries = [c.claim_id.split(".")[0] for c in cands]
        assert max(entries.count(e) for e in entries) <= match.PER_ENTRY
        assert len(cands) <= match.TOP_K


def test_fact_labels_are_not_read_as_claim_text(mini_bank):
    award = mini_bank.entries["AWD-01"]
    fact = next(c for c in award.claims if c.kind == "fact")
    assert fact.label not in match.claim_text(fact)


def good_match(**changes):
    m = Match(key="test:acme:1", track="ML evaluation", coverage=[
        Coverage(requirement_id="R1", verdict="strong", support=[
            Support(claim_id="EXP-01.b1", strength="strong", quote="Built a reproducible evaluation over 10,000 utterances"),
        ]),
        Coverage(requirement_id="R2", verdict="partial", note="terminal, not GUI", support=[
            Support(claim_id="ENG-01.b1", strength="partial", quote="terminal browser agent with exact approvals"),
        ]),
    ])  # fmt: skip
    return m.model_copy(update=changes)


def findings(report, check):
    return [f.message for f in report.findings if f.check == check]


def test_valid_match_passes(mini_bank):
    report = match.validate(good_match(), spec(R_EVAL, R_AGENT, R_KO), mini_bank)
    assert report.ok, report.findings
    assert report.findings == []


def test_quotes_must_be_verbatim(mini_bank):
    m = good_match()
    m.coverage[0].support[0].quote = "Built a reproducible benchmark over 10,000 utterances"
    report = match.validate(m, spec(R_EVAL, R_AGENT), mini_bank)
    assert findings(report, "quote") == [
        "R1 quote is not verbatim from EXP-01.b1: 'Built a reproducible benchmark over 10,000 utterances'"
    ]


def test_quotes_ignore_markdown_and_typography(mini_bank):
    m = good_match()
    m.coverage[0].support[0] = Support(claim_id="ENG-02.b2", strength="strong",
                                       quote="Delivered 123 non-merge commits and 87 merged PRs")  # fmt: skip
    assert match.validate(m, spec(R_EVAL, R_AGENT), mini_bank).ok  # the claim has **bold** around the numbers


def test_unknown_hidden_and_duplicate_claims(mini_bank):
    mini_bank.claim("ENG-01.b1").show_on_resume = False
    m = good_match()
    m.coverage[0].support.append(Support(claim_id="ENG-99.b1", strength="partial", quote="does not exist anywhere"))
    m.coverage[0].support.append(m.coverage[0].support[0])
    messages = findings(match.validate(m, spec(R_EVAL, R_AGENT), mini_bank), "claim")
    assert "R1 cites ENG-99.b1, which does not exist" in messages
    assert "R2 cites ENG-01.b1, which is hidden or superseded" in messages
    assert findings(match.validate(m, spec(R_EVAL, R_AGENT), mini_bank), "support") == [
        "R1 cites EXP-01.b1 more than once"
    ]


def test_every_requirement_judged_once_and_knockouts_left_out(mini_bank):
    m = good_match()
    m.coverage.append(Coverage(requirement_id="R4", verdict="none", note="London"))
    m.coverage.append(Coverage(requirement_id="R1", verdict="none", note="again"))
    report = match.validate(m, spec(R_EVAL, R_AGENT, R_LOCAL, R_KO), mini_bank)
    assert findings(report, "ids") == [
        "requirements judged twice: R1",
        "requirements not judged: R3",
        "R4 is a knockout; knockouts are judged in knockouts.md, not here",
    ]


def test_verdicts_must_agree_with_supports(mini_bank):
    m = good_match()
    m.coverage[0].support[0].strength = "partial"
    m.coverage[1].verdict = "none"
    report = match.validate(m, spec(R_EVAL, R_AGENT), mini_bank)
    assert findings(report, "verdict") == ["R1 is strong but has no strong support", "R2 is none but lists supports"]


def test_track_and_key_are_checked(mini_bank):
    report = match.validate(good_match(track="Astronaut", key="other"), spec(R_EVAL, R_AGENT), mini_bank)
    assert findings(report, "job") == ["match is for other, spec is test:acme:1"]
    assert findings(report, "track")[0].startswith("unknown track 'Astronaut'")


def test_pack_lists_requirements_hits_and_every_claim(mini_bank, tmp_path):
    s = spec(R_EVAL, R_AGENT, R_KO)
    r = match.retrieve(mini_bank, s)
    pack = match.write_pack(tmp_path, mini_bank, s, r).read_text()
    assert "### R1 · basic · weight 2 · skill" in pack and "### R4" not in pack
    assert "Retrieval suggests **ML evaluation**" in pack
    assert "`ENG-02.b1` Co-led a six-person team" in pack and "team work" in pack
    assert "conditions: 97.5% vs 4.0% — Classifiable cases only" in pack
    assert '"track"' in pack
    eligible = len(match.eligible(mini_bank))
    assert pack.split("## Every eligible claim")[1].count("\n- `") == eligible
    assert (tmp_path / match.HITS_FILE).is_file()


def test_weighted_coverage_counts_partial_half():
    s = spec(R_EVAL, R_AGENT.model_copy(update={"weight": 3}), R_LOCAL.model_copy(update={"kind": "preferred"}))
    m = good_match()
    assert match.weighted_coverage(s, m, "basic") == pytest.approx((2 * 1 + 3 * 0.5) / 5)
    assert match.weighted_coverage(s, m, "preferred") == 0.0  # R3 not judged counts as none
    assert match.weighted_coverage(s, m, "responsibility") is None


def test_gap_report_sorts_requirements_into_tiers(mini_bank):
    s = spec(R_EVAL, R_AGENT, R_LOCAL, R_KO)
    m = good_match()
    m.coverage.append(Coverage(requirement_id="R3", verdict="none", note="no local inference shown"))
    text = match.gap_report(s, m, mini_bank, match.retrieve(mini_bank, s))
    supported = text.split("## Supported")[1].split("## Weakly")[0]
    weak = text.split("## Weakly supported (Unknown risk)")[1].split("## Unsupported")[0]
    unsupported = text.split("## Unsupported (never written into the resume)")[1].split("##")[0]
    assert "| R1 | basic | 2 | Reproducible evaluation | `EXP-01.b1` Example University" in supported
    assert "| R2 |" in weak and "(partial)" in weak and "terminal, not GUI |" in weak
    assert "| R3 | basic | 2 | Local inference | no local inference shown |" in unsupported
    assert "Screener view of the 3 basic requirements: 1 Meets, 1 Unknown, 1 Does not meet." in text
    assert "Weighted coverage (strong counts fully, partial half): basic 50%." in text
    assert "- R4: “In-person in London”" in text
    assert "Track: **ML evaluation**." in text
    assert "- EXP-01 · Example University — Research Collaborator, Speech" in text and ": 1 (R1)" in text


def test_evidence_used_counts_requirements_not_citations(mini_bank):
    m = good_match()
    m.coverage[0].support.append(
        Support(claim_id="EXP-01.b2", strength="partial", quote="Model A kept 97.5% of tone markers")
    )
    text = match.gap_report(spec(R_EVAL, R_AGENT), m, mini_bank)
    line = next(x for x in text.splitlines() if x.startswith("- EXP-01 "))
    assert line.endswith(": 1 (R1)")


def test_gap_report_flags_account_only_proof(mini_bank):
    mini_bank.claim("EXP-01.b1").proof = "account"
    text = match.gap_report(spec(R_EVAL, R_AGENT), good_match(), mini_bank)
    assert "## Resting on your account alone" in text and "- R1 (Reproducible evaluation)" in text


POSTING_TEXT = """## Requirements

- We want people who build reproducible evaluations.
- Prior experience building browser agents.
- In-person in London three days a week.
"""


def application(p):
    posting = Posting(key="test:acme:1", ats="lever", org="acme", company="Acme", title="ML Engineer, Evaluation",
                      url="https://jobs.lever.co/acme/1", fetched_at="2030-01-01T00:00:00+00:00", text=POSTING_TEXT)  # fmt: skip
    folder = ingest.save_posting(p.applications, posting, {})
    (folder / jobspec.SPEC_FILE).write_text(spec(R_EVAL, R_AGENT, R_KO).model_dump_json())
    return folder


def test_cli_plan_writes_pack_then_gap_report(data_dir, capsys):
    p = paths.load()
    bank.save(bank.compile_bank(p), p.bank)
    folder = application(p)
    with pytest.raises(SystemExit) as done:
        cli.main(["plan", folder.name])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "suggested track 'ML evaluation'" in out and f"next: fill {folder / match.MATCH_FILE}" in out
    assert (folder / match.PACK_FILE).is_file() and not (folder / match.GAP_FILE).exists()

    (folder / match.MATCH_FILE).write_text(good_match().model_dump_json())
    with pytest.raises(SystemExit) as done:
        cli.main(["plan", folder.name])
    assert done.value.code == 0
    assert "gap report: 1 supported, 1 weak, 0 unsupported; basic coverage 75%" in capsys.readouterr().out
    assert (folder / match.GAP_FILE).read_text().startswith("# Gap report: ML Engineer, Evaluation at Acme")


def test_cli_plan_rejects_an_ungrounded_match(data_dir, capsys):
    p = paths.load()
    bank.save(bank.compile_bank(p), p.bank)
    folder = application(p)
    bad = good_match()
    bad.coverage[0].support[0].quote = "Led evaluation at a frontier lab"
    (folder / match.MATCH_FILE).write_text(bad.model_dump_json())
    with pytest.raises(SystemExit) as done:
        cli.main(["plan", folder.name])
    assert done.value.code == 1
    assert "error: [quote] R1 quote is not verbatim from EXP-01.b1" in capsys.readouterr().out
    assert not (folder / match.GAP_FILE).exists()
    assert "**Fix the errors below" in (folder / match.CHECK_FILE).read_text()


@pytest.mark.local
def test_real_bank_retrieval_on_a_saved_spec():
    p = paths.load()
    folder = next(iter(sorted(p.applications.glob("anthropic-*/jobspec.json"))), None)
    if folder is None:
        pytest.skip("no Anthropic application folder")
    s = JobSpec.model_validate_json(folder.read_text())
    r = match.retrieve(bank.load(p.bank), s)
    assert r.tracks[0].startswith("Evals")
    assert all(r.hits[q.id] for q in s.requirements if q.kind in ("basic", "preferred"))
