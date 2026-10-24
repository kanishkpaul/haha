import pytest

from career_tailor import bank, match, paths
from career_tailor.models import JobSpec, Requirement


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
