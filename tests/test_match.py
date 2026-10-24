import pytest

from career_tailor import bank, match, paths
from career_tailor.models import Coverage, JobSpec, Match, Requirement, Support


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
