"""Stage 3: choose what fits the page.

This module holds the page model: how many lines each item costs at the
template's measure, which sections an entry belongs to, and which items must
not appear together because they describe the same work (two entries citing one
proof card, overlay `same_work` links, or two claims sharing a distinctive
number). Costs are estimates until the renderer (M7) measures real lines.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from . import numbers
from .bank import Bank
from .match import eligible
from .models import Claim, Entry, JobSpec
from .textutil import md_plain

SECTIONS = {
    "experience": "Experience",
    "research": "Research",
    "engineering": "Projects",
    "leadership": "Leadership",
    "award": "Awards and selections",
    "selection": "Awards and selections",
    "education": "Education",
}
SECTION_ORDER = ["Education", "Experience", "Research", "Projects", "Leadership", "Awards and selections"]
ONE_LINE_KINDS = {"award", "selection"}  # shown as a single line, no entry heading


@dataclass(frozen=True)
class Layout:
    """Lines and characters for one-column 10 pt type with half-inch margins. M7 calibrates these."""

    paper: str
    lines_per_page: float
    chars_per_line: int  # bullet text width
    header: float = 3.0  # name, contact line, headline
    section: float = 1.5  # section heading plus the space above it
    entry: float = 1.0  # entry heading: name, role, dates
    education: float = 2.0  # degree line and dates/grade line
    skills: float = 3.0  # skills lines (section heading counted separately)
    max_bullet_lines: int = 2

    def budget(self, pages: int) -> float:
        return self.lines_per_page * pages


LAYOUTS = {
    "letter": Layout(paper="letter", lines_per_page=56, chars_per_line=100),
    "a4": Layout(paper="a4", lines_per_page=60, chars_per_line=96),
}
_LETTER_PLACES = re.compile(
    r"\b(?:United States|USA|U\.S\.|US|Canada|Toronto|Montreal|Vancouver|San Francisco|New York|NYC|Seattle|"
    r"Berkeley|Boston|Austin|Chicago|Los Angeles|Palo Alto|Mountain View|Menlo Park|Washington|"
    r"CA|WA|NY|MA|TX)\b"
)


def paper_for(spec: JobSpec) -> str:
    """Letter when the first listed location is in the US or Canada, A4 otherwise."""
    first = re.split(r"[;&|/]| or ", spec.location or "")[0]
    return "letter" if _LETTER_PLACES.search(first) else "a4"


def display_text(claim: Claim) -> str:
    """What the resume line starts from: table rows as "Title: text", everything else as written."""
    text = md_plain(claim.text)
    return f"{md_plain(claim.label)}: {text}" if claim.kind == "row" and claim.label else text


def claim_lines(claim: Claim, entry: Entry, layout: Layout) -> int:
    """Lines a claim takes as a bullet (capped: M5 writes to this budget), or one line for an award."""
    if entry.kind in ONE_LINE_KINDS:
        return 1
    return min(math.ceil(len(display_text(claim)) / layout.chars_per_line), layout.max_bullet_lines)


def is_catalog(entry: Entry) -> bool:
    """Catalog entries (small tools, early builds) hold one table row per project."""
    return bool(entry.claims) and all(c.kind == "row" for c in entry.claims)


# ---------------------------------------------------------------- same work

Side = tuple[str, ...]  # claim ids; one side of an exclusion


def _side(bank: Bank, ref: str, pool: dict[str, Claim]) -> Side:
    """An entry id stands for all its eligible claims; a claim id for itself."""
    if "." in ref:
        return (ref,) if ref in pool else ()
    return tuple(c.id for c in bank.entries[ref].claims if c.id in pool)


def exclusions(bank: Bank) -> list[tuple[Side, Side, str]]:
    """Pairs of claim sets that must not both appear, with the reason."""
    pool = {c.id: c for _, c in eligible(bank)}
    out: list[tuple[Side, Side, str]] = []
    seen: set[tuple[Side, Side]] = set()

    def add(a: Side, b: Side, why: str) -> None:
        if a and b and not set(a) & set(b):
            key = tuple(sorted((a, b)))
            if key not in seen:
                seen.add(key)
                out.append((a, b, why))

    # Entries citing one proof card describe the same work, unless both are award lines
    # (two placements can share one source). An entry stands whole when every claim cites the card.
    citing: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for claim in pool.values():
        for card in claim.evidence_ids:
            citing[card][claim.entry_id].append(claim.id)
    for card, by_entry in citing.items():
        for e1, e2 in combinations(sorted(by_entry), 2):
            a, b = bank.entries[e1], bank.entries[e2]
            if a.kind in ONE_LINE_KINDS and b.kind in ONE_LINE_KINDS:
                continue
            sides = []
            for entry in (a, b):
                whole = _side(bank, entry.id, pool)
                sides.append(whole if set(whole) <= set(by_entry[entry.id]) else tuple(by_entry[entry.id]))
            add(sides[0], sides[1], f"same work (proof card {card})")
    for entry in bank.entries.values():
        for ref in entry.same_work:
            add(_side(bank, entry.id, pool), _side(bank, ref, pool), "same work (annotation)")
    # Two claims carrying one distinctive number state the same result: within an entry any specific
    # number counts (54/55, 1.97); across entries that share a proof card only a distinctive one (56,000).
    numbers_of = {c.id: {t for t in numbers.tokens(md_plain(c.text)) if numbers.is_strong(t)} for c in pool.values()}
    for c1, c2 in combinations(sorted(pool), 2):
        a, b = pool[c1], pool[c2]
        shared = numbers_of[c1] & numbers_of[c2]
        if a.entry_id != b.entry_id:
            shared = {t for t in shared if numbers.is_unique(t)} if set(a.evidence_ids) & set(b.evidence_ids) else set()
        if shared:
            add((c1,), (c2,), f"same result ({', '.join(sorted(shared))})")
    return out
