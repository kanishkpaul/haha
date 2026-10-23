"""Turn the record's sections into Entries, Claims and a Profile.

Claim ids are `<entry id>.<letter><n>`: b = bullet, l = lead statement (quote),
r = table row, f = fact, u = record update. Claim text keeps the record's
Markdown exactly, so every claim can be traced back to its line.
"""

import re
from pathlib import Path

import yaml

from .models import Claim, DateRange, Entry, EntryKind, Profile, RecordUpdate, Repo, SourceRef
from .record import Block, Card, Record, Section
from .textutil import md_links, md_plain

SECTION_KINDS: dict[str, str] = {
    "Identity": "identity",
    "Research, industry": "experience",
    "Research portfolio": "research",
    "Engineering and product": "engineering",
    "Ventures, leadership": "leadership",
    "Awards": "awards",
    "Skills": "skills",
    "Role-specific": "assembly",
    "Complete current GitHub": "repos",
    "Metric and version": "metrics",
}
CARD_KINDS: set[str] = {"experience", "research", "engineering", "leadership"}

_CARD_ID = re.compile(r"^([A-Z]+-\d+) · (.+)$")
_FIELD = re.compile(r"\*\*([^*]+?):\*\*\s*")
_GITHUB = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)")
_TEAM_FIELDS = ("Team", "Collaborator", "Collaborators")
_LETTER = {"bullet": "b", "lead": "l", "row": "r", "fact": "f", "update": "u"}

_MONTHS = {m[:3].lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}  # fmt: skip
_MON = r"(?:early |mid-|mid |late )?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?(?:/[A-Za-z]+)?"
_Y = r"((?:19|20)\d\d)"
_DASH = r"\s*[–—-]\s*"
_DATE_PATTERNS = [
    (re.compile(rf"{_MON} {_Y}{_DASH}(present|current)", re.I), "my-present"),
    (re.compile(rf"{_MON} {_Y}{_DASH}{_MON} {_Y}", re.I), "my-my"),
    (re.compile(rf"{_MON}{_DASH}{_MON} {_Y}", re.I), "m-my"),
    (re.compile(rf"{_MON} {_Y},? (?:onward|ongoing)", re.I), "my-present"),
    (re.compile(rf"{_Y}{_DASH}(present|current)", re.I), "y-present"),
    (re.compile(rf"{_Y}{_DASH}{_Y}"), "y-y"),
    (re.compile(rf"{_MON} {_Y}", re.I), "my"),
    (re.compile(rf"{_MON} \d{{1,2}},? {_Y}", re.I), "my"),
    (re.compile(rf"\b{_Y}\b"), "y"),
]
_COMPACT = re.compile(r"Use \*\*(.+?)\*\* as the compact public date")


def section_kind(section: Section) -> str:
    return next((kind for prefix, kind in SECTION_KINDS.items() if section.title.startswith(prefix)), "")


# ---------------------------------------------------------------- dates


def _ym(mon: str, year: str) -> str:
    return f"{year}-{_MONTHS[mon[:3].lower()]:02d}"


def _show(value: str) -> str:
    if value == "present":
        return "Present"
    if len(value) == 4:
        return value
    year, month = value.split("-")
    return f"{list(_MONTHS)[int(month) - 1].capitalize()} {year}"


def _bounds(shape: str, g: tuple[str, ...]) -> tuple[str, str]:
    match shape:
        case "my-present":
            return _ym(g[0], g[1]), "present"
        case "my-my":
            return _ym(g[0], g[1]), _ym(g[2], g[3])
        case "m-my":
            return _ym(g[0], g[2]), _ym(g[1], g[2])
        case "y-present":
            return g[0], "present"
        case "y-y":
            return g[0], g[1]
        case "my":
            return _ym(g[0], g[1]), _ym(g[0], g[1])
    return g[0], g[0]


def parse_dates(raw: str) -> DateRange:
    """Best-effort parse of a record date phrase; `display` stays None when nothing matches."""
    head = raw
    if m := _COMPACT.search(raw):
        head = m.group(1)
    head = md_plain(head.split(";")[0])
    for pattern, shape in _DATE_PATTERNS:
        m = pattern.search(head)
        if not m:
            continue
        start, end = _bounds(shape, m.groups())
        display = _show(start) if start == end else f"{_show(start)} – {_show(end)}"
        return DateRange(raw=raw, start=start, end=end, display=display)
    return DateRange(raw=raw)


# ---------------------------------------------------------------- cards


def _split_heading(heading: str) -> tuple[str, str]:
    name, _, subtitle = md_plain(heading).partition(" — ")
    return name.strip(), subtitle.strip()


def _fields(text: str) -> dict[str, str]:
    parts = _FIELD.split(text)
    return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def _add_claim(entry: Entry, kind: str, text: str, start: int, end: int, **extra) -> Claim:
    n = sum(1 for c in entry.claims if c.kind == kind) + 1
    claim = Claim(
        id=f"{entry.id}.{_LETTER[kind]}{n}",
        entry_id=entry.id,
        kind=kind,
        text=text,
        source=SourceRef(line_start=start, line_end=end),
        **extra,
    )
    entry.claims.append(claim)
    return claim


def _add_note(entry: Entry, label: str, text: str) -> None:
    entry.notes[label] = f"{entry.notes[label]}\n\n{text}" if label in entry.notes else text


def parse_blocks(entry: Entry, blocks: list[Block]) -> None:
    """Fill an entry from a card's blocks: fields before the content, claims, then notes."""
    started, group = False, ""
    for block in blocks:
        if block.kind == "subheading":
            started, group = True, block.label
            continue
        if block.kind == "labelled" and not started:
            entry.fields.update(_fields(" ".join(ln.text.strip() for ln in block.lines)))
        elif block.kind == "labelled":
            _add_note(entry, block.label, block.text)
        elif block.kind == "paragraph":
            _add_note(entry, "body", block.text)
        elif block.kind == "quote":
            _add_claim(entry, "lead", block.text, block.start, block.end, group=group)
        elif block.kind == "list":
            for line, text in block.items:
                _add_claim(entry, "bullet", text, line.no, line.no, group=group)
        elif block.kind == "table":
            for line, cells in block.rows:
                details = " | ".join(cells[2:])
                _add_claim(entry, "row", cells[1], line.no, line.no, label=md_plain(cells[0]), details=details,
                           group=group or block.header[0])  # fmt: skip
        if block.kind in ("quote", "list", "table"):
            started, group = True, ""


def _finish(entry: Entry, record: Record) -> Entry:
    raw_dates = entry.fields.get("Dates") or entry.fields.get("Founded") or entry.fields.get("Date")
    if raw_dates:
        entry.dates = parse_dates(raw_dates)
    elif entry.kind == "selection" and entry.claims and (found := parse_dates(entry.claims[0].text)).display:
        entry.dates = found  # selections carry their date inside the wording
    if stack := entry.fields.get("Stack"):
        entry.stack = [s.strip() for s in stack.rstrip(".").split(",") if s.strip()]
    entry.team = any(f in entry.fields for f in _TEAM_FIELDS)
    _collect_repos(entry, record, entry.source.line_start, entry.source.line_end)
    return entry


def _collect_repos(entry: Entry, record: Record, start: int, end: int) -> None:
    seen = {r.name for r in entry.repos}
    for line in record.lines[start - 1 : end]:
        for url in md_links(line):
            if (m := _GITHUB.match(url)) and (name := f"{m.group(1)}/{m.group(2)}") not in seen:
                seen.add(name)
                entry.repos.append(Repo(name=name, url=f"https://github.com/{name}"))


def parse_card(card: Card, kind: EntryKind, record: Record) -> Entry | None:
    m = _CARD_ID.match(card.heading)
    if not m:
        return None
    name, subtitle = _split_heading(m.group(2))
    entry = Entry(
        id=m.group(1),
        kind=kind,
        heading=m.group(2),
        name=name,
        subtitle=subtitle,
        source=SourceRef(line_start=card.line, line_end=card.end),
    )
    parse_blocks(entry, card.blocks)
    return _finish(entry, record)


# ---------------------------------------------------------------- awards, selections


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", md_plain(text).lower()) if len(w) > 2}


def parse_awards(section: Section, record: Record) -> tuple[list[Entry], list[str]]:
    """ID tables become one entry per row; an un-tabled card merges into its best-matching entry."""
    entries: list[Entry] = []
    notes: list[str] = []
    for card in section.cards:
        tables = [b for b in card.blocks if b.kind == "table" and b.header and b.header[0] == "ID"]
        for table in tables:
            for line, cells in table.rows:
                eid = md_plain(cells[0])
                kind = "selection" if eid.startswith("SEL") else "award"
                name, subtitle = _split_heading(cells[1])
                entry = Entry(id=eid, kind=kind, heading=md_plain(cells[1]), name=name, subtitle=subtitle,
                              source=SourceRef(line_start=line.no, line_end=line.no))  # fmt: skip
                if len(cells) >= 4:
                    entry.fields["Date"] = cells[2]
                _add_claim(entry, "fact", cells[-1], line.no, line.no, label=table.header[-1])
                entries.append(_finish(entry, record))
        if tables:
            notes += [f"{b.label}: {b.text}" for b in card.blocks if b.kind == "labelled"]
            continue
        if any(b.kind == "table" for b in card.blocks):
            continue  # a table without IDs (external engagement) is context, not entries
        heading = re.sub(r"^\d+\.\d+\s+", "", card.heading)
        target = max(entries, key=lambda e: len(_words(e.heading) & _words(heading)), default=None)
        if target and _words(target.heading) & _words(heading):
            parse_blocks(target, card.blocks)
            _collect_repos(target, record, card.line, card.end)
    return entries, notes


# ---------------------------------------------------------------- identity, education


def parse_profile(section: Section) -> tuple[Profile, dict[str, tuple[int, str]]]:
    """The identity table (with line numbers) plus headline and summaries from its cards."""
    rows: dict[str, tuple[int, str]] = {}
    for block in section.blocks:
        if block.kind == "table":
            for line, cells in block.rows:
                rows[md_plain(cells[0])] = (line.no, cells[1])
    profile = Profile(name=md_plain(rows.get("Name", (0, ""))[1]), fields={k: v for k, (_, v) in rows.items()})
    for card in section.cards:
        for block in card.blocks:
            if block.kind == "subheading" and not profile.headline:
                profile.headline = block.label
            elif block.kind == "quote":
                profile.summaries[card.heading] = block.text
    return profile, rows


_EDUCATION = {
    "EDU-01": ("Institution", "Degree", "Degree dates", ["Degree", "Degree dates", "Historical academic snapshot"]),
    "EDU-02": ("School", "", "School dates", ["School results", "School dates"]),
}


def parse_education(rows: dict[str, tuple[int, str]]) -> list[Entry]:
    entries = []
    for eid, (inst, degree, dates, facts) in _EDUCATION.items():
        if inst not in rows:
            continue
        used = [rows[k][0] for k in [inst, degree, dates, *facts] if k in rows]
        name = md_plain(rows[inst][1])
        entry = Entry(id=eid, kind="education", heading=name, name=name,
                      subtitle=md_plain(rows[degree][1]) if degree in rows else "",
                      source=SourceRef(line_start=min(used), line_end=max(used)))  # fmt: skip
        if dates in rows:
            entry.dates = parse_dates(rows[dates][1])
        for key in facts:
            if key in rows:
                _add_claim(entry, "fact", rows[key][1], rows[key][0], rows[key][0], label=key)
        entries.append(entry)
    return entries


# ---------------------------------------------------------------- record updates


def load_updates(path: Path) -> list[RecordUpdate]:
    if not path.is_file():
        return []
    return [RecordUpdate.model_validate(u) for u in yaml.safe_load(path.read_text(encoding="utf-8")) or []]


def apply_updates(entries: list[Entry], updates: list[RecordUpdate]) -> list[str]:
    """Add each update as a current claim and supersede what it replaces. Returns problems found."""
    by_id = {e.id: e for e in entries}
    problems = []
    for upd in updates:
        entry = by_id.get(upd.entry)
        if entry is None:
            problems.append(f"{upd.id}: no entry {upd.entry}")
            continue
        n = sum(1 for c in entry.claims if c.kind == "update") + 1
        claim = Claim(id=f"{entry.id}.u{n}", entry_id=entry.id, kind="update", text=upd.text,
                      label=upd.id, origin=f"{upd.source} ({upd.date.isoformat()})",
                      show_on_resume=upd.show_on_resume)  # fmt: skip
        if upd.supersedes_text:
            hits = [c for c in entry.claims if upd.supersedes_text in md_plain(c.text)]
            if not hits:
                problems.append(f"{upd.id}: nothing in {entry.id} contains {upd.supersedes_text!r}")
            for old in hits:
                old.status, old.superseded_by, old.show_on_resume = "superseded", claim.id, False
        entry.claims.append(claim)
    return problems


# ---------------------------------------------------------------- whole record


def extract(record: Record, updates: list[RecordUpdate] = ()) -> tuple[Profile, list[Entry], dict[str, list[str]]]:
    """Profile, entries in record order, and notes (section-level notes and problems)."""
    profile = Profile(name="", fields={})
    entries: list[Entry] = []
    notes: dict[str, list[str]] = {"awards": [], "problems": []}
    for section in record.sections:
        kind = section_kind(section)
        if kind == "identity":
            profile, rows = parse_profile(section)
            entries += parse_education(rows)
        elif kind in CARD_KINDS:
            for card in section.cards:
                entry = parse_card(card, kind, record)
                if entry is None:
                    notes["problems"].append(f"line {card.line}: card heading without an id: {card.heading}")
                else:
                    entries.append(entry)
        elif kind == "awards":
            awards, award_notes = parse_awards(section, record)
            entries += awards
            notes["awards"] += award_notes
    notes["problems"] += apply_updates(entries, list(updates))
    return profile, entries, notes
