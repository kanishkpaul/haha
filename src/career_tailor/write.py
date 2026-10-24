"""Stage 4: rewrite selected claims as bullets in the job's vocabulary.

Each TailoredBullet keeps its metric and qualifier and cites its claim ids.
The parts of a resume that are facts rather than writing (contact line,
education, entry headings, dates, links) come straight from the bank; the
writing (headline, bullets, award lines, skills) is LLM work done in Claude
Code and saved as `draft.json`. `render_draft` puts both together as `draft.md`.

`write_pack` prepares the writing prompt: each bullet's source claim, its
conditions and team credit, the requirements it supports, a character budget
from the plan, and voice samples from the record's earlier resumes. `guard`
checks the result before anything is rendered: citations, budgets, numbers,
ownership words, banned phrases, drift from the sources, headline and skills.
The full fact check against the bank is the next stage's job.
"""

import json
import re
from collections import Counter
from pathlib import Path

from . import numbers
from . import selection as sel_mod
from .annotate import _variants
from .bank import Bank
from .match import entry_label, tokens
from .models import CheckReport, Claim, Entry, Finding, JobSpec, Match, Selection, TailoredBullet, Writing
from .textutil import md_links, md_plain

WRITING_FILE = "draft.json"
DRAFT_FILE = "draft.md"
PACK_FILE = "write.prompt.md"
CHECK_FILE = "write.check.md"
DRIFT = 0.6  # share of a bullet's content words that should come from its sources or the posting
MAX_SKILLS = 18

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


# ---------------------------------------------------------------- guard

# Words that claim more ownership than a source may give. Normalised so "owning" and "owned" match.
_OWNERSHIP = {"led": "lead", "lead": "lead", "leading": "lead", "leads": "lead", "sole": "sole", "solely": "sole",
              "solo": "solo", "single-handedly": "solo", "independently": "independent", "independent": "independent",
              "founded": "found", "founder": "found", "founding": "found", "owned": "own", "owning": "own",
              "owns": "own", "headed": "head", "managed": "manage", "directed": "direct",
              "architected": "architect"}  # fmt: skip


def _ownership(text: str) -> set[str]:
    words = re.findall(r"[a-z]+(?:-[a-z]+)*", text.lower())
    return {_OWNERSHIP[w] for w in words if w in _OWNERSHIP}


def sources(bank: Bank, entry: Entry, claims: list[Claim]) -> str:
    """Everything a bullet may draw facts from: its claims with their labels, details and conditions,
    plus the entry's heading, dates and team-credit note."""
    parts = [entry.name, entry.subtitle, entry.dates.raw if entry.dates else "", entry.notes.get("Team credit", "")]
    for c in claims:
        parts += [c.label, c.text, c.details, *c.conditions]
    return md_plain(" ".join(x for x in parts if x))


def guard(w: Writing, bank: Bank, spec: JobSpec, m: Match, sel: Selection) -> CheckReport:
    report = CheckReport()

    def add(check: str, severity: str, message: str, bullet: str = "") -> None:
        report.findings.append(Finding(check=check, severity=severity, message=message, bullet=bullet[:80]))

    layout = sel_mod.LAYOUTS[sel.paper]
    if w.job != spec.key:
        add("job", "error", f"draft is for {w.job}, spec is {spec.key}")
    headlines = {spec.title, bank.profile.headline} | {t.headline for t in bank.tables.tracks}
    if w.headline not in headlines:
        add("headline", "error", f"headline {w.headline!r} is neither the posting title nor a record headline")
    picks = {p.entry_id: p for p in sel.picks}
    all_picked = [c for p in sel.picks for c in p.claim_ids]
    if w.summary:
        if sel.lines_budget - sel.lines_used < 2:
            add("summary", "error", "the plan leaves no room for a summary; leave it empty")
        if len(w.summary) > 2 * layout.chars_per_line:
            add("summary", "error", f"summary is {len(w.summary)} characters; at most {2 * layout.chars_per_line}")
        known = numbers.tokens(" ".join(sources(bank, bank.entries[c.split(".")[0]], [bank.claim(c)])
                                        for c in all_picked), years=True)  # fmt: skip
        if extra := numbers.tokens(w.summary, years=True) - known:
            add("numbers", "error", f"summary has numbers no picked claim states: {', '.join(sorted(extra))}")
    keywords = set(tokens(" ".join([spec.title, *spec.keywords, *(x for v in spec.keywords.values() for x in v)])))
    cited: Counter = Counter()
    for b in w.bullets:
        text = md_plain(b.text)
        pick = picks.get(b.entry_id)
        if pick is None:
            add("cite", "error", f"{b.entry_id} is not in the plan", text)
            continue
        if not b.claim_ids or not set(b.claim_ids) <= set(pick.claim_ids):
            add("cite", "error", f"{b.entry_id} bullet must cite claims the plan picked for it: {pick.claim_ids}", text)
            continue
        cited.update(b.claim_ids)
        entry = bank.entries[b.entry_id]
        claims = [bank.claim(c) for c in b.claim_ids]
        budget = sum(pick.lines[c] for c in b.claim_ids) * layout.chars_per_line
        if len(text) > budget:
            add("length", "error", f"{'+'.join(b.claim_ids)} is {len(text)} characters; the budget is {budget}", text)
        source = sources(bank, entry, claims)
        if extra := numbers.tokens(text, years=True) - numbers.tokens(source, years=True):
            add("numbers", "error", f"{'+'.join(b.claim_ids)} states {', '.join(sorted(extra))}, "
                                    "which its claims don't", text)  # fmt: skip
        if upgraded := _ownership(text) - _ownership(source):
            add("ownership", "error", f"{'+'.join(b.claim_ids)} claims {', '.join(sorted(upgraded))} ownership "
                                      "that its claims don't state", text)  # fmt: skip
        lowered = text.lower()
        for phrase in BANNED:
            if re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", lowered):
                add("voice", "error", f"{'+'.join(b.claim_ids)} uses the banned phrase {phrase!r}", text)
        words = [t for t in tokens(text) if t not in keywords]
        if words:
            known_words = set(tokens(source))
            share = sum(t in known_words for t in words) / len(words)
            if share < DRIFT:
                add("drift", "warning", f"{'+'.join(b.claim_ids)}: only {share:.0%} of its words come from its "
                                        "sources; check it says nothing new", text)  # fmt: skip
        if entry.kind not in sel_mod.ONE_LINE_KINDS and not text.endswith("."):
            add("style", "warning", f"{'+'.join(b.claim_ids)} should end with a full stop", text)
    for cid in all_picked:
        if cited[cid] == 0:
            add("cite", "error", f"{cid} is in the plan but no bullet cites it")
        elif cited[cid] > 1:
            add("cite", "error", f"{cid} is cited by {cited[cid]} bullets; cite each claim once")
    terms = [t for group in w.skills.values() for t in group]
    for term in terms:
        if not known_skill(bank, term):
            add("skills", "error", f"{term!r} is not in the record's skills bank")
    if len(terms) > MAX_SKILLS:
        add("skills", "warning", f"{len(terms)} skills listed; keep at most {MAX_SKILLS}")
    if len(w.skills) > 4:
        add("skills", "warning", f"{len(w.skills)} skill groups; keep three or four")
    return report


def render_check(report: CheckReport) -> str:
    lines = ["# Draft check", ""]
    lines.append("**OK**" if report.ok else "**Fix the errors below, then rerun `tailor write`.**")
    lines.append("")
    for f in report.findings:
        lines.append(f"- {f.severity}: [{f.check}] {f.message}" + (f" — “{f.bullet}”" if f.bullet else ""))
    if not report.findings:
        lines.append("- No findings.")
    return "\n".join(lines) + "\n"
