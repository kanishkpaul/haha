import re

import pytest

from career_tailor import bank, paths, selection, write
from career_tailor.models import Coverage, JobSpec, Match, Requirement, Support

R1 = Requirement(
    id="R1", text="Reproducible evaluation", quote="build reproducible evaluations", kind="basic", weight=3
)
R2 = Requirement(id="R2", text="Browser agents", quote="experience building browser agents", kind="preferred")
SPEC = JobSpec(key="test:acme:1", company="Acme", title="ML Engineer, Evaluation", location="Berlin",
               requirements=[R1, R2], keywords={"evaluation": ["evals"], "browser agents": []})  # fmt: skip
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
