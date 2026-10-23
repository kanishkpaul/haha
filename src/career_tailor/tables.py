"""The record's reference tables: skills (11), role tracks (12.1), selling rules (12.3),
repositories (14) and the metric reference (15), plus how they attach to entries and claims.
"""

import re
from dataclasses import dataclass, field

from . import numbers
from .extract import section_kind
from .models import Entry, MetricRow, Repo, SkillArea, Track
from .record import Block, Record
from .textutil import md_links, md_plain


@dataclass
class Tables:
    skills: list[SkillArea] = field(default_factory=list)
    skill_notes: list[str] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    structure: list[str] = field(default_factory=list)
    repos: list[Repo] = field(default_factory=list)
    metrics: list[MetricRow] = field(default_factory=list)
    metric_notes: list[str] = field(default_factory=list)


def _split(cell: str) -> list[str]:
    return [s.strip() for s in md_plain(cell).split(",") if s.strip()]


def _tables(blocks: list[Block]) -> list[Block]:
    return [b for b in blocks if b.kind == "table"]


def _items(blocks: list[Block]) -> list[str]:
    return [md_plain(text) for b in blocks if b.kind == "list" for _, text in b.items]


def read_tables(record: Record) -> Tables:
    t = Tables()
    for section in record.sections:
        kind = section_kind(section)
        everything = section.blocks + [b for c in section.cards for b in c.blocks]
        if kind == "skills":
            for table in _tables(everything):
                t.skills += [SkillArea(area=md_plain(c[0]), skills=_split(c[1]), examples=md_plain(c[2]))
                             for _, c in table.rows]  # fmt: skip
            t.skill_notes += [f"{b.label}: {md_plain(b.text)}" for b in everything if b.kind == "labelled"]
        elif kind == "assembly":
            for card in section.cards:
                if card.heading.startswith("12.1"):
                    for table in _tables(card.blocks):
                        t.tracks += [Track(target=md_plain(c[0]), lead=_split(c[1]), support=_split(c[2]),
                                           headline=md_plain(c[3])) for _, c in table.rows]  # fmt: skip
                elif card.heading.startswith("12.2"):
                    t.structure = _items(card.blocks)
                elif card.heading.startswith("12.3"):
                    t.rules = _items(card.blocks)
        elif kind == "repos":
            for table in _tables(everything):
                if not table.header[1].startswith("Visibility"):
                    continue  # the provenance table repeats names without visibility
                for _, c in table.rows:
                    links = md_links(c[0])
                    visibility, _, relationship = md_plain(c[1]).partition(";")
                    t.repos.append(Repo(
                        name=md_plain(c[0]),
                        url=links[0] if links else "",
                        visibility=visibility.strip() if visibility.strip() in ("public", "private") else "unknown",
                        relationship=relationship.strip(),
                        summary=md_plain(c[2]),
                    ))  # fmt: skip
        elif kind == "metrics":
            for table in _tables(everything):
                t.metrics += [MetricRow(work=md_plain(c[0]), result=md_plain(c[1]), conditions=md_plain(c[2]))
                              for _, c in table.rows]  # fmt: skip
            t.metric_notes = _items(everything)
    return t


# ---------------------------------------------------------------- attaching to entries

_GENERIC = set(
    "a an and the of for to in on with by from at work works project projects research agent agents system systems "
    "ai ml model models local tools tool demo prototype legacy fix upstream data team based early new current "
    "engineering engineer independent collaboration collaborations interface infrastructure evaluation "
    "platform app apps builds build".split()
)  # fmt: skip


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", md_plain(text).lower())


def _acronyms(phrase: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", md_plain(phrase))
    words = re.findall(r"[A-Za-z0-9]+", spaced)
    if len(words) < 2:
        return set()
    return {"".join(w[0] for w in words).lower(), "".join(w[0] for w in words if w.lower() not in _GENERIC).lower()}


def _aliases(heading: str) -> set[str]:
    parts = {p.strip() for p in re.split(r"\s*(?:/|—|→|&|,|\(|\))\s*", md_plain(heading)) if p.strip()}
    out = {p.lower() for p in parts}
    for p in parts:
        out |= _acronyms(p)
    return out


def resolve(item: str, entries: list[Entry]) -> list[str]:
    """Entry or claim ids an item from the tracks table refers to: exact alias first, then shared words."""
    targets: list[tuple[str, set[str], set[str]]] = []  # id, aliases, distinctive words
    for e in entries:
        texts = [e.name, e.subtitle, e.heading]
        targets.append((e.id, _aliases(e.heading) | {e.name.lower()},
                        {w for t in texts for w in _words(t)} - _GENERIC))  # fmt: skip
        for c in e.claims:
            if c.kind == "row" and c.label:
                targets.append((c.id, _aliases(c.label), set(_words(c.label)) - _GENERIC))
    found: list[str] = []
    for piece in (p.strip() for p in item.split("/") if p.strip()):
        low = piece.lower()
        exact = [tid for tid, aliases, _ in targets if low in aliases]
        if exact:
            found += exact
            continue
        words = set(_words(piece)) - _GENERIC
        scored = [(len(words & w), tid) for tid, _, w in targets if words & w]
        if scored:
            best = max(s for s, _ in scored)
            found += [tid for s, tid in scored if s == best]
    return list(dict.fromkeys(found))


def name_targets(work: str, entries: list[Entry]) -> list[str]:
    """Entries a metric row's "Work" names: the longest leading phrase that is an exact alias, else shared words."""
    words = md_plain(work).split()
    for k in range(len(words), 0, -1):
        phrase = " ".join(words[:k]).lower()
        hits = [e.id for e in entries if phrase in _aliases(e.heading) | {e.name.lower()}]
        if hits:
            return hits
    return list(dict.fromkeys(i.split(".")[0] for i in resolve(work, entries)))


def _note(entry: Entry, label: str, text: str) -> None:
    entry.notes[label] = f"{entry.notes[label]}\n\n{text}" if label in entry.notes else text


def attach(t: Tables, entries: list[Entry]) -> list[str]:
    """Attach repo visibility, metric conditions and track membership. Returns unmatched items."""
    problems: list[str] = []
    repos = {r.name: r for r in t.repos}
    for e in entries:
        e.repos = [repos.get(r.name, r) for r in e.repos]

    by_id = {e.id: e for e in entries}
    claim_tokens = {c.id: numbers.tokens(md_plain(c.text)) for e in entries for c in e.claims}
    for row in t.metrics:
        wanted = numbers.tokens(row.result)
        nontrivial = {w for w in wanted if w not in ("0", "1")}
        named = set(name_targets(row.work, entries))
        for e in entries:
            for c in e.claims:
                if c.status != "current":
                    continue
                shared = wanted & claim_tokens[c.id]
                in_named = (
                    e.id in named
                    and bool(shared)
                    and (any(numbers.is_strong(s) for s in shared) or len(shared) >= max(1, (len(nontrivial) + 1) // 2))
                )
                if in_named or any(numbers.is_unique(s) for s in shared):
                    row.claim_ids.append(c.id)
                    c.conditions.append(f"{row.result} — {row.conditions}")
        if not row.claim_ids:
            # The number lives in a card's notes rather than a claim: keep the conditions with the entry.
            for eid in named:
                _note(by_id[eid], "Metric reference", f"{row.result} — {row.conditions}")
            problems.append(f"metric row matches no claim: {row.work} | {row.result}"
                            + (f" (kept as a note on {', '.join(sorted(named))})" if named else ""))  # fmt: skip

    for track in t.tracks:
        for items, out in ((track.lead, track.lead_entries), (track.support, track.support_entries)):
            for item in items:
                ids = resolve(item, entries)
                if not ids:
                    track.unresolved.append(item)
                out += [i for i in ids if i not in out]
        for tid in track.lead_entries + track.support_entries:
            entry = by_id[tid.split(".")[0]]
            if track.target not in entry.tracks:
                entry.tracks.append(track.target)
    return problems


def attach_metrics(entries: list[Entry]) -> None:
    """Record every number each claim states, in order."""
    for e in entries:
        for c in e.claims:
            c.metrics = numbers.extract_numbers(md_plain(c.text))
