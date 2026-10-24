"""Stage 4: rewrite selected claims as bullets in the job's vocabulary.

Each TailoredBullet keeps its metric and qualifier and cites its claim ids.
The parts of a resume that are facts rather than writing (contact line,
education, entry headings, dates, links) come straight from the bank; the
writing (headline, bullets, award lines, skills) is LLM work done in Claude
Code and saved as `draft.json`. `render_draft` puts both together as `draft.md`.
"""

import re

from . import selection as sel_mod
from .annotate import _variants
from .bank import Bank
from .models import Entry, JobSpec, Selection, TailoredBullet, Writing
from .textutil import md_links, md_plain

WRITING_FILE = "draft.json"
DRAFT_FILE = "draft.md"

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
    """Skills-bank terms tagged on the picked claims and their entries, by skills-bank area."""
    picked = {cid for p in sel.picks for cid in p.claim_ids}
    found: set[str] = set()
    for p in sel.picks:
        entry = bank.entries[p.entry_id]
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
