import re

import pytest

from career_tailor import bank, cli, ingest, jobspec, match, paths, selection, write
from career_tailor.models import Coverage, JobSpec, Match, Posting, Requirement, Support, TailoredBullet

R1 = Requirement(
    id="R1", text="Reproducible evaluation", quote="build reproducible evaluations", kind="basic", weight=3
)
R2 = Requirement(id="R2", text="Browser agents", quote="experience building browser agents", kind="preferred")
SPEC = JobSpec(key="test:acme:1", company="Acme", title="ML Engineer, Evaluation", location="Berlin",
               requirements=[R1, R2], keywords={"evaluation": ["evals"], "browser agents": []})  # fmt: skip
POSTING = Posting(key=SPEC.key, ats="lever", org="acme", company="Acme", title=SPEC.title,
                  url="https://jobs.lever.co/acme/1", fetched_at="2030-01-01T00:00:00+00:00",
                  text="- build reproducible evaluations\n- experience building browser agents\n")  # fmt: skip
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
    return selection.solve(mini_bank, SPEC, MATCH)


def test_contact_line_is_plain_text(mini_bank):
    line = write.contact_line(mini_bank)
    assert line == "Springfield, Example State · +00 0000 000000 · ada@example.com · github.com/ada-example"


def test_entry_links_only_point_at_the_candidates_own_repositories(mini_bank):
    entry = mini_bank.entries["ENG-01"]
    entry.public_links = ["https://github.com/other/clawbot", "https://www.linkedin.com/posts/x",
                          "https://github.com/ada-example/clawbot"]  # fmt: skip
    assert write.entry_link(mini_bank, entry) == "github.com/ada-example/clawbot"
    entry.public_links = ["https://github.com/other/clawbot"]
    assert write.entry_link(mini_bank, entry) == ""


def test_education_lines(mini_bank):
    first, second = write.education_lines(mini_bank)
    assert first == "**Example Institute of Technology, Springfield** — B.Sc., Computer Science · Aug 2027 – Jun 2031"
    assert second == "Expected graduation June 2031"


def test_skills_come_from_the_skills_bank(mini_bank, sel):
    skills = write.suggested_skills(mini_bank, sel)
    assert skills and all(write.known_skill(mini_bank, s) for terms in skills.values() for s in terms)
    areas = [a.area for a in mini_bank.tables.skills]
    assert list(skills) == sorted(skills, key=areas.index)
    assert write.known_skill(mini_bank, "hugging face transformers")  # a slash form of "Transformers/Hub"
    assert not write.known_skill(mini_bank, "Kubernetes")


def test_source_writing_and_draft(mini_bank, sel):
    w = write.source_writing(mini_bank, SPEC, sel)
    assert w.headline == "ML Engineer, Evaluation"
    assert sum(len(p.claim_ids) for p in sel.picks) == len(w.bullets)
    draft = write.render_draft(mini_bank, SPEC, sel, w)
    assert draft.startswith("# Ada Example\n\n**ML Engineer, Evaluation**")
    assert draft.index("## Education") < draft.index("## Experience") < draft.index("## Skills")
    assert "**Example University** — Research Collaborator, Speech · Sep 2030 – Present" in draft
    assert "- Built a reproducible evaluation over 10,000 utterances" in draft
    award = next(p for p in sel.picks if p.section == "Awards and selections")
    award_line = next(b.text for b in w.bullets if b.entry_id == award.entry_id)
    assert f"- {award_line}" in draft and f"**{mini_bank.entries[award.entry_id].name}**" not in draft


RECORD_WITH_ARCHIVE = """# Record

## 17. Megadoc

• Not an archive bullet.

## 18. Prior resumes

```text
• Built a reproducible evaluation harness for browser agents with seeded runs and
  regression cases.
• Organised a campus dance festival.
```
"""


def test_style_seeds_come_from_section_18_closest_first():
    seeds = write.style_seeds(RECORD_WITH_ARCHIVE, SPEC)
    assert seeds[0] == ("Built a reproducible evaluation harness for browser agents with seeded runs and "
                        "regression cases.")  # fmt: skip
    assert "Not an archive bullet." not in seeds and len(seeds) == 2
    assert write.style_seeds("# no archive", SPEC) == []


def test_pack_lists_each_claim_with_budget_conditions_and_requirements(mini_bank, sel, tmp_path):
    pack = write.write_pack(tmp_path, mini_bank, SPEC, MATCH, sel, RECORD_WITH_ARCHIVE).read_text()
    budget = (
        selection.LAYOUTS["a4"].chars_per_line * next(p for p in sel.picks if p.entry_id == "EXP-01").lines["EXP-01.b1"]
    )
    assert f"- `EXP-01.b1` · bullet, at most {budget} characters · proof none" in pack
    assert "  - Supports R1 (basic): “build reproducible evaluations”" in pack
    if "EXP-01.b2" in {c for p in sel.picks for c in p.claim_ids}:
        assert "  - Condition to keep with its metric: 97.5% vs 4.0% — Classifiable cases only" in pack
    assert "ongoing: present tense" in pack
    assert "Keywords: evaluation (evals), browser agents." in pack
    assert "“spearheaded”" in pack and "`ML Engineer, Evaluation`" in pack
    assert "- Built a reproducible evaluation harness for browser agents" in pack
    assert '"headline"' in pack
    assert sum(len(p.claim_ids) for p in sel.picks) == len(re.findall(r"\n- `[A-Z]+-\d+\.\w+`", pack))


def test_summary_only_when_the_page_has_room(mini_bank, sel, tmp_path):
    full = sel.model_copy(update={"lines_used": sel.lines_budget})
    assert 'leave it empty ("")' in write.write_pack(tmp_path, mini_bank, SPEC, MATCH, full, "").read_text()
    roomy = sel.model_copy(update={"lines_used": sel.lines_budget - 5})
    assert "optional, at most" in write.write_pack(tmp_path, mini_bank, SPEC, MATCH, roomy, "").read_text()


# ---------------------------------------------------------------- guard


def errors(report, check=None):
    return [f.message for f in report.findings if f.severity == "error" and (check is None or f.check == check)]


def edited(w, claim_id, text):
    for b in w.bullets:
        if b.claim_ids == [claim_id]:
            b.text = text
    return w


def fitted(bank_, sel_):
    """The record's wording, cut at a word boundary where a line would overflow its budget."""
    w = write.source_writing(bank_, SPEC, sel_)
    picks = {p.entry_id: p for p in sel_.picks}
    for b in w.bullets:
        budget = picks[b.entry_id].lines[b.claim_ids[0]] * selection.LAYOUTS[sel_.paper].chars_per_line
        if len(b.text) > budget:
            b.text = b.text[: budget - 1].rsplit(" ", 1)[0].rstrip(",;") + "."
    return w


@pytest.fixture
def baseline(mini_bank, sel):
    return fitted(mini_bank, sel)


def test_the_record_wording_passes(mini_bank, sel, baseline):
    report = write.guard(baseline, mini_bank, SPEC, MATCH, sel)
    assert report.ok, errors(report)


def test_invented_numbers_are_rejected(mini_bank, sel, baseline):
    w = edited(baseline, "EXP-01.b1", "Built a reproducible evaluation over 12,000 utterances across 12 languages.")
    assert errors(write.guard(w, mini_bank, SPEC, MATCH, sel), "numbers") == [
        "EXP-01.b1 states 12000, which its claims don't"
    ]


def test_upgraded_ownership_is_rejected(mini_bank, sel, baseline):
    w = edited(baseline, "EXP-01.b1", "Led and independently built a reproducible evaluation over 10,000 utterances.")
    assert errors(write.guard(w, mini_bank, SPEC, MATCH, sel), "ownership") == [
        "EXP-01.b1 claims independent, lead ownership that its claims don't state"
    ]


def test_banned_phrases_and_length(mini_bank, sel, baseline):
    w = edited(baseline, "EXP-01.b1", "Spearheaded a reproducible evaluation over 10,000 utterances. " + "x" * 300)
    report = write.guard(w, mini_bank, SPEC, MATCH, sel)
    assert errors(report, "voice") == ["EXP-01.b1 uses the banned phrase 'spearheaded'"]
    assert errors(report, "length")[0].startswith("EXP-01.b1 is 362 characters; the budget is")


def test_every_picked_claim_cited_once_by_its_own_entry(mini_bank, sel, baseline):
    w = baseline.model_copy(deep=True)
    first = w.bullets.pop(0)
    w.bullets.append(w.bullets[0].model_copy())
    w.bullets.append(TailoredBullet(entry_id="ENG-03", claim_ids=["ENG-03.r1"], text="Built a desktop app."))
    w.bullets.append(TailoredBullet(entry_id=first.entry_id, claim_ids=["ENG-01.b1"], text="Built an agent."))
    messages = errors(write.guard(w, mini_bank, SPEC, MATCH, sel), "cite")
    assert f"{first.claim_ids[0]} is in the plan but no bullet cites it" in messages
    assert f"{w.bullets[0].claim_ids[0]} is cited by 2 bullets; cite each claim once" in messages
    assert "ENG-03 is not in the plan" in messages or any(m.startswith("ENG-03 bullet") for m in messages)
    assert any(m.startswith(f"{first.entry_id} bullet must cite claims the plan picked") for m in messages)


def test_merging_two_claims_of_one_entry_is_allowed(mini_bank, sel, baseline):
    pick = next(p for p in sel.picks if len(p.claim_ids) >= 2 and p.section != "Awards and selections")
    a, b = pick.claim_ids[:2]
    w = baseline.model_copy(deep=True)
    w.bullets = [x for x in w.bullets if x.claim_ids not in ([a], [b])]
    text = " ".join(next(x.text for x in baseline.bullets if x.claim_ids == [c]) for c in (a, b))
    w.bullets.append(TailoredBullet(entry_id=pick.entry_id, claim_ids=[a, b], text=text[:150].rstrip() + "."))
    assert not errors(write.guard(w, mini_bank, SPEC, MATCH, sel), "cite")


def test_headline_summary_and_skills(mini_bank, sel, baseline):
    w = baseline.model_copy(update={"headline": "Chief AI Officer", "summary": "Ships fast.",
                                    "skills": {"Tools": ["Python", "Kubernetes"]}})  # fmt: skip
    full = sel.model_copy(update={"lines_used": sel.lines_budget})
    report = write.guard(w, mini_bank, SPEC, MATCH, full)
    assert errors(report, "headline") == [
        "headline 'Chief AI Officer' is neither the posting title nor a record headline"
    ]
    assert errors(report, "summary") == ["the plan leaves no room for a summary; leave it empty"]
    assert errors(report, "skills") == ["'Kubernetes' is not in the record's skills bank"]
    track = mini_bank.tables.tracks[0].headline
    assert not errors(write.guard(baseline.model_copy(update={"headline": track}), mini_bank, SPEC, MATCH, sel))


def test_drift_is_a_warning(mini_bank, sel, baseline):
    w = edited(baseline, "EXP-01.b1", "Pioneered multilingual speech science with novel acoustic phonetics tooling.")
    report = write.guard(w, mini_bank, SPEC, MATCH, sel)
    assert report.ok
    drift = [f for f in report.findings if f.check == "drift"]
    assert len(drift) == 1 and drift[0].severity == "warning" and drift[0].message.startswith("EXP-01.b1: only")


def test_cli_write_pack_then_draft(data_dir, capsys):
    p = paths.load()
    bank.save(bank.compile_bank(p), p.bank)
    folder = ingest.save_posting(p.applications, POSTING, {})
    (folder / jobspec.SPEC_FILE).write_text(SPEC.model_dump_json())
    with pytest.raises(SystemExit) as done:
        cli.main(["write", folder.name])
    assert done.value.code == 1 and "run `tailor plan" in capsys.readouterr().err
    (folder / match.MATCH_FILE).write_text(MATCH.model_dump_json())
    with pytest.raises(SystemExit):
        cli.main(["plan", folder.name])
    with pytest.raises(SystemExit) as done:
        cli.main(["write", folder.name])
    assert done.value.code == 0 and "next: fill" in capsys.readouterr().out
    assert (folder / write.PACK_FILE).is_file()
    b = bank.load(p.bank)
    sel = selection.Selection.model_validate_json((folder / selection.SELECTION_FILE).read_text())
    (folder / write.WRITING_FILE).write_text(fitted(b, sel).model_dump_json())
    with pytest.raises(SystemExit) as done:
        cli.main(["write", folder.name])
    assert done.value.code == 0
    assert "draft: " in capsys.readouterr().out
    assert (folder / write.DRAFT_FILE).read_text().startswith("# Ada Example")
    assert "**OK**" in (folder / write.CHECK_FILE).read_text()
