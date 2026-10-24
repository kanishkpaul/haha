"""Stage 4: rewrite selected claims as bullets in the job's vocabulary.

Each TailoredBullet keeps its metric and qualifier and cites its claim ids.
The parts of a resume that are facts rather than writing (contact line,
education, entry headings, dates, links) come straight from the bank; the
writing (headline, bullets, award lines, skills) is LLM work done in Claude
Code and saved as `draft.json`. `render_draft` puts both together as `draft.md`.

`write_pack` prepares the writing prompt: each bullet's source claim, its
conditions and team credit, the requirements it supports, a character budget
from the plan, and voice samples from the record's earlier resumes.
"""

import json
import re
from collections import Counter
from pathlib import Path

from . import selection as sel_mod
from .annotate import _variants
from .bank import Bank
from .match import entry_label, tokens
from .models import Entry, JobSpec, Match, Selection, TailoredBullet, Writing
from .textutil import md_links, md_plain

WRITING_FILE = "draft.json"
DRAFT_FILE = "draft.md"
PACK_FILE = "write.prompt.md"

# Filler a screener reads past, and claims of quality the bullet should show instead.
BANNED = [
    "spearheaded", "leveraged", "leveraging", "utilized", "utilizing", "passionate", "results-driven",
    "cutting-edge", "state-of-the-art", "world-class", "best-in-class", "synergy", "rockstar", "ninja",
    "seamless", "seamlessly", "game-changing", "revolutionary", "innovative", "proven track record",
    "detail-oriented", "hardworking", "responsible for", "helped to", "various", "etc.", "successfully",
    "robust and scalable", "deep understanding", "extensive experience",
]  # fmt: skip

CONTACT_FIELDS = ["Location", "Phone", "Primary email", "Website", "GitHub", "LinkedIn"]


def contact_line(bank: Bank) -> str:
    fields = bank.profile.fields
    return " · ".join(md_plain(fields[f]) for f in CONTACT_FIELDS if fields.get(f))


def _github_owner(bank: Bank) -> str:
    """The candidate's GitHub account, so entry links never point at someone else's repository."""
    links = md_links(bank.profile.fields.get("GitHub", "")) or [bank.profile.fields.get("GitHub", "")]
    match = re.search(r"github\.com/([\w.-]+)", links[0])
    return match.group(1).lower() if match else ""


def entry_link(bank: Bank, entry: Entry) -> str:
    """A public repository of the candidate's own for the entry heading, as plain text, or nothing."""
    owner = _github_owner(bank)
    for url in entry.public_links:
        found = re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/?$", url)
        if owner and found and found.group(1).lower() == owner:
            return f"github.com/{found.group(1)}/{found.group(2)}"
    return ""


def education_lines(bank: Bank) -> list[str]:
    """The degree line and the expected-graduation-and-grade line for EDU-01, from its current claims."""
    edu = bank.entries.get("EDU-01")
    if edu is None:
        return []
    dates = edu.dates.display if edu.dates and edu.dates.display else ""
    degree = edu.subtitle.split(" — ")[0]  # the specialisation suffix is too long for a resume line
    first = f"**{edu.name}** — {degree}" + (f" · {dates}" if dates else "")
    current = [c for c in edu.claims if c.status == "current" and c.show_on_resume]
    expected = next((md_plain(c.text) for c in current if "expected graduation" in c.text.lower()), "")
    expected = expected.split(";")[-1].strip()
    expected = expected[:1].upper() + expected[1:]
    grades = [md_plain(c.text) for c in current if c.kind == "update"]
    second = "; ".join(x for x in [expected, *grades] if x)
    return [first, second] if second else [first]


def suggested_skills(bank: Bank, sel: Selection) -> dict[str, list[str]]:
    """Skills-bank terms tagged on the picked claims and their entries, by skills-bank area.
    An award line shows one result, so only its own claim's skills count, not its entry's."""
    picked = {cid for p in sel.picks for cid in p.claim_ids}
    found: set[str] = set()
    for p in sel.picks:
        entry = bank.entries[p.entry_id]
        if entry.kind not in sel_mod.ONE_LINE_KINDS:
            found.update(entry.skills)
        for c in entry.claims:
            if c.id in picked:
                found.update(c.skills)
    out = {}
    for area in bank.tables.skills:
        terms = [s for s in area.skills if s in found]
        if terms:
            out[area.area] = terms
    return out


def known_skill(bank: Bank, term: str) -> bool:
    """True if `term` is a skills-bank entry or one of its slash forms ("Hugging Face Transformers")."""
    wanted = term.strip().lower()
    return any(wanted == v.lower() for area in bank.tables.skills for s in area.skills for v in _variants(s))


def source_writing(bank: Bank, spec: JobSpec, sel: Selection) -> Writing:
    """The plan as written in the record: a baseline draft before any rewriting."""
    bullets = [
        TailoredBullet(entry_id=p.entry_id, claim_ids=[cid], text=sel_mod.display_text(bank.claim(cid)))
        for p in sel.picks
        for cid in p.claim_ids
    ]
    return Writing(job=spec.key, headline=spec.title, bullets=bullets, skills=suggested_skills(bank, sel))


def render_draft(bank: Bank, spec: JobSpec, sel: Selection, writing: Writing) -> str:
    lines = [f"# {bank.profile.name}", "", f"**{writing.headline}**", "", contact_line(bank), ""]
    if writing.summary:
        lines += [writing.summary, ""]
    lines += ["## Education", "", *[f"{x}  " for x in education_lines(bank)], ""]
    by_entry: dict[str, list[TailoredBullet]] = {}
    for b in writing.bullets:
        by_entry.setdefault(b.entry_id, []).append(b)
    section = None
    for pick in sel.picks:
        entry = bank.entries[pick.entry_id]
        if pick.section != section:
            if lines[-1]:
                lines.append("")
            section = pick.section
            lines += [f"## {section}", ""]
        bullets = by_entry.get(entry.id, [])
        if entry.kind in sel_mod.ONE_LINE_KINDS:
            lines += [f"- {b.text}" for b in bullets]
            continue
        role = f" — {entry.subtitle}" if entry.subtitle else ""
        dates = f" · {entry.dates.display}" if entry.dates and entry.dates.display else ""
        link = entry_link(bank, entry)
        lines.append(f"**{entry.name}**{role}{dates}" + (f" · {link}" if link else ""))
        lines += [f"- {b.text}" for b in bullets]
        lines.append("")
    if lines[-1]:
        lines.append("")
    if writing.skills:
        lines += ["## Skills", ""]
        lines += [f"**{group}:** {', '.join(terms)}  " for group, terms in writing.skills.items()]
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- prompt pack


def style_seeds(record_text: str, spec: JobSpec, n: int = 8) -> list[str]:
    """Bullets from the record's earlier resumes (section 18) closest to the job, as voice samples."""
    start = record_text.find("\n## 18.")
    if start < 0:
        return []
    bullets: list[str] = []
    for line in record_text[start:].split("\n"):
        if line.startswith("• "):
            bullets.append(line[2:].strip())
        elif bullets and line.startswith("  ") and line.strip():
            bullets[-1] += " " + line.strip()
    job = Counter(tokens(" ".join([spec.title, *spec.keywords, *(r.text for r in spec.requirements)])))
    scored = sorted(dict.fromkeys(bullets), key=lambda b: -sum(job[t] for t in set(tokens(b))))
    return scored[:n]


def char_budget(lines: int, layout: sel_mod.Layout) -> int:
    return lines * layout.chars_per_line


_PACK = """\
# Write the resume for {company}: {title}

Write `{draft_path}`: JSON matching the schema at the end. Then run `uv run tailor write {folder}`.

## Rules

The plan (`plan.md`) already chose what goes on the page. Your job is the wording.

- **One bullet per claim below**, citing its id in `claim_ids`, under its own `entry_id`. Two claims of
  the same entry may share one bullet (cite both) when that reads better and fits both budgets combined.
- **Facts come only from the cited claims.** Every number, name, tool, dataset and outcome must be in
  them. You may drop detail to fit; never add any. Keep numbers exactly as written.
- **Keep each metric's condition** (listed under the claim) whenever you keep the metric.
- **Ownership stays as the source says it.** "Co-led" never becomes "led"; "helped build" never becomes
  "built"; team work names the part the candidate owned. Never add "solo", "independently", "founded"
  or "sole" unless the claim says so.
- **Shape:** start with the strongest true verb; say what was done, how it was measured, and how
  (Google's X-Y-Z), in that order of priority. No first person; end each bullet with a full stop.
  Past tense for finished work, present tense for entries that run to Present.
- **The posting's words where they are true.** If the claim is an evaluation, call it one; if the posting
  says "benchmark" and the claim built one, say "benchmark". Don't stretch a word to cover something the
  claim doesn't show. Keywords: {keywords}.
- **Character budget** per bullet is a hard limit: the plan's line budget at this page's measure.
- **Banned:** {banned}.
- **Public disclosure:** generic capture-stack wording, no confidential game names or assets, no
  patient-level data, private repositories never linked.
- **Award lines:** one line each, result first: "Top-250 team finish, Amazon ML Challenge 2026 (entity
  resolution)". Keep team results as team results.

## Headline and summary

- `headline`: the posting title, `{title}`, when it honestly describes the candidate's work.
  Otherwise one of the record's headlines: {headlines}.
- `summary`: {summary_rule}

## Skills

`skills`: three or four groups with short labels ("Languages", "ML and evaluation", ...), at most 18
terms, the job's most relevant first. Use only skills-bank terms; these are the ones the picked work
shows:

{skills}

## Claims to write

{entries}

## Voice samples

Bullets from the candidate's earlier resumes, closest to this job. Match their density and tone.
Their facts may be outdated or superseded: never copy a fact from here.

{seeds}

## Schema

```json
{schema}
```
"""


def write_pack(folder: Path, bank: Bank, spec: JobSpec, m: Match, sel: Selection, record_text: str) -> Path:
    layout = sel_mod.LAYOUTS[sel.paper]
    reqs = {r.id: r for r in spec.requirements}
    supports: dict[str, list[str]] = {}
    for cov in m.coverage:
        for sup in cov.support:
            supports.setdefault(sup.claim_id, []).append(cov.requirement_id)
    blocks = []
    for pick in sel.picks:
        entry = bank.entries[pick.entry_id]
        ongoing = bool(entry.dates and entry.dates.end == "present")
        head = f"### {entry_label(entry)}" + (" · ongoing: present tense" if ongoing else "")
        lines = [head, ""]
        if entry.notes.get("Team credit"):
            lines += [f"Team credit: {md_plain(entry.notes['Team credit'])}", ""]
        elif entry.team:
            lines += ["Team work: name the candidate's own part.", ""]
        for cid in pick.claim_ids:
            claim = bank.claim(cid)
            kind = "award line" if entry.kind in sel_mod.ONE_LINE_KINDS else "bullet"
            budget = char_budget(pick.lines[cid], layout)
            lines.append(f"- `{cid}` · {kind}, at most {budget} characters · proof {claim.proof}")
            lines.append(f"  - Source: {sel_mod.display_text(claim)}")
            if claim.details:
                lines.append(
                    f"  - Context (not for the resume unless it is a fact you need): {md_plain(claim.details)}"
                )
            for cond in claim.conditions:
                lines.append(f"  - Condition to keep with its metric: {md_plain(cond)}")
            for rid in sorted(set(supports.get(cid, [])), key=lambda r: (len(r), r)):
                lines.append(f"  - Supports {rid} ({reqs[rid].kind}): “{reqs[rid].quote}”")
        blocks.append("\n".join(lines))
    slack = sel.lines_budget - sel.lines_used
    summary_rule = (
        f'leave it empty (""): the page is full ({sel.lines_used:g} of {sel.lines_budget:g} lines).'
        if slack < 2
        else f"optional, at most {char_budget(2, layout)} characters, only if it earns its space; facts from the "
        "claims above only."
    )
    headlines = sorted({t.headline for t in bank.tables.tracks} | ({bank.profile.headline} - {""}))
    keywords = ", ".join(f"{k} ({', '.join(v)})" if v else k for k, v in spec.keywords.items()) or "none listed"
    skills = "\n".join(f"- {area}: {', '.join(terms)}" for area, terms in suggested_skills(bank, sel).items())
    seeds = "\n".join(f"- {s}" for s in style_seeds(record_text, spec)) or "- (none in the record)"
    pack = folder / PACK_FILE
    pack.write_text(_PACK.format(
        company=spec.company, title=spec.title, draft_path=folder / WRITING_FILE, folder=folder,
        keywords=keywords, banned=", ".join(f"“{b}”" for b in BANNED), headlines="; ".join(f"“{h}”" for h in headlines),
        summary_rule=summary_rule, skills=skills or "- (none)", entries="\n\n".join(blocks), seeds=seeds,
        schema=json.dumps(Writing.model_json_schema(), indent=2),
    ), encoding="utf-8")  # fmt: skip
    return pack
