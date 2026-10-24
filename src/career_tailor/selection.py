"""Stage 3: choose what fits the page.

The page model says how many lines each item costs at the template's measure,
which section an entry belongs to, and which items must not appear together
because they describe the same work (two entries citing one proof card, overlay
`same_work` links, or two claims sharing a distinctive number). Costs are
estimates until the renderer (M7) measures real lines.

`solve` picks entries and claims with CP-SAT: a boolean per entry and per claim,
the page budget and section rules as constraints, and an objective led by
weighted requirement coverage from `match.json`, then proof, metrics, recency
and fit to the job's track. `render_plan` explains the result in `plan.md`; your
pins and bans go in `plan.edits.yaml` and are applied on every run.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path

import yaml
from ortools.sat.python import cp_model

from . import numbers
from .bank import Bank
from .match import eligible, entry_label, weighted_coverage
from .models import Claim, Entry, JobSpec, Match, Pick, PlanEdits, Selection
from .textutil import md_plain

SELECTION_FILE = "selection.json"
PLAN_FILE = "plan.md"
EDITS_FILE = "plan.edits.yaml"

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


# ---------------------------------------------------------------- solver

KIND_FACTOR = {"basic": 3, "responsibility": 2, "preferred": 1}
PROOF_VALUE = {"collected": 12, "accepted": 8, "account": 3, "none": 3}
KIND_VALUE = {"experience": 60}  # screeners read research and industry experience first (record section 12)
# An award line earns its place with a placement or a selection, not an interview or an invitation.
_PLACED = re.compile(
    r"\b(?:top[- ]|\d+(?:st|nd|rd|th)\b|winner|won\b|runner-up|selected|first place|accepted into)", re.I
)
SCALE = 10  # line costs in tenths of a line


@dataclass(frozen=True)
class Rules:
    """Section limits for a page count, following the record's assembly guidance (section 12)."""

    projects: tuple[int, int]  # research + engineering entries
    awards: tuple[int, int]  # one-line award and selection entries
    leadership: int
    bullets: dict[str, int]  # max bullets per entry by kind ("catalog" for catalog entries)

    @classmethod
    def for_pages(cls, pages: int) -> "Rules":
        if pages == 1:
            bullets = {"experience": 4, "research": 3, "engineering": 3, "leadership": 2, "catalog": 2}
            return cls(projects=(2, 4), awards=(2, 3), leadership=1, bullets=bullets)
        bullets = {"experience": 5, "research": 4, "engineering": 4, "leadership": 3, "catalog": 3}
        return cls(projects=(2, 4 + 3 * (pages - 1)), awards=(2, 3 + 3 * (pages - 1)), leadership=2, bullets=bullets)


class SelectionError(ValueError):
    pass


def _recency(entry: Entry) -> int:
    end = (entry.dates.end or entry.dates.start or "") if entry.dates else ""
    if end == "present":
        return 10
    year = int(end[:4]) if end[:4].isdigit() else 0
    return {2026: 8, 2025: 4, 2024: 2}.get(year, 0)


def _sort_key(entry: Entry) -> tuple[str, str]:
    if not entry.dates:
        return ("", "")
    end = entry.dates.end or entry.dates.start or ""
    return ("9999" if end == "present" else end, entry.dates.start or "")


def fixed_lines(layout: Layout, pages: int, bank: Bank) -> float:
    """Header, education and skills: on every resume, outside the solver's choice."""
    education = layout.education + (1 if pages > 1 and "EDU-02" in bank.entries else 0)
    return layout.header + layout.section + education + layout.section + layout.skills


def solve(
    bank: Bank,
    spec: JobSpec,
    m: Match,
    edits: PlanEdits | None = None,
    time_limit: float = 20.0,
    lines_per_page: float | None = None,
    only: set[str] | None = None,
) -> Selection:
    """Pick the page. `lines_per_page` replaces the layout's estimate with a measured one (the fit loop and
    saved calibration); `only` limits the choice to these claim ids (the bullets already written)."""
    edits = edits or PlanEdits()
    pages = edits.pages or spec.pages
    paper = paper_for(spec)
    layout = LAYOUTS[paper]
    if lines_per_page:
        layout = replace(layout, lines_per_page=lines_per_page)
    rules = Rules.for_pages(pages)
    pool = [(e, c) for e, c in eligible(bank) if e.kind != "education"]
    claims = {c.id: c for _, c in pool}
    entries = {e.id: e for e, _ in pool}
    unknown = [r for r in edits.pin + edits.ban if r not in claims and r not in entries]
    if unknown:
        raise SelectionError(f"plan.edits.yaml names items that can't go on a resume: {', '.join(unknown)}")

    model = cp_model.CpModel()
    x = {eid: model.new_bool_var(f"x_{eid}") for eid in entries}
    y = {cid: model.new_bool_var(f"y_{cid}") for cid in claims}
    cost = []
    by_entry: dict[str, list[str]] = defaultdict(list)
    for e, c in pool:
        by_entry[e.id].append(c.id)
        model.add_implication(y[c.id], x[e.id])
        cost.append(SCALE * claim_lines(c, e, layout) * y[c.id])
    for eid, cids in by_entry.items():
        entry = entries[eid]
        chosen = sum(y[c] for c in cids)
        if entry.kind in ONE_LINE_KINDS:
            model.add(chosen == x[eid])
            continue
        model.add(chosen >= x[eid])
        kind = "catalog" if is_catalog(entry) else entry.kind
        model.add(chosen <= rules.bullets[kind] * x[eid])
        if kind in ("experience", "research", "engineering"):
            available = [c for c in cids if only is None or c in only]
            model.add(chosen >= min(2, len(available)) * x[eid])
        cost.append(int(SCALE * layout.entry) * x[eid])
    sections: dict[str, list[str]] = defaultdict(list)
    for eid, entry in entries.items():
        sections[SECTIONS[entry.kind]].append(eid)
    for name, eids in sections.items():
        used = model.new_bool_var(f"s_{name}")
        for eid in eids:
            model.add_implication(x[eid], used)
        cost.append(int(SCALE * layout.section) * used)
    budget = layout.budget(pages) - fixed_lines(layout, pages, bank)
    model.add(sum(cost) <= int(SCALE * budget))

    def count(kinds: set[str]) -> cp_model.LinearExpr:
        return sum(x[eid] for eid, e in entries.items() if e.kind in kinds)

    if any(e.kind == "experience" for e in entries.values()):
        model.add(count({"experience"}) >= 1)
    model.add(count({"research", "engineering"}) >= rules.projects[0])
    model.add(count({"research", "engineering"}) <= rules.projects[1])
    award_lines = count(ONE_LINE_KINDS)
    model.add(award_lines <= rules.awards[1])
    if sum(1 for e in entries.values() if e.kind in ONE_LINE_KINDS) >= rules.awards[0]:
        model.add(award_lines >= rules.awards[0])
    model.add(count({"leadership"}) <= rules.leadership)
    for a, b, _ in exclusions(bank):
        for ca in a:
            for cb in b:
                if ca in y and cb in y:
                    model.add(y[ca] + y[cb] <= 1)
    for ref in edits.pin:
        model.add((y if ref in claims else x)[ref] == 1)
    for ref in edits.ban:
        model.add((y if ref in claims else x)[ref] == 0)
    if only is not None:
        for cid in claims.keys() - only:
            model.add(y[cid] == 0)

    reqs = {r.id: r for r in spec.requirements}
    value = []
    for cov in m.coverage:
        supports = [s for s in cov.support if s.claim_id in y]
        if not supports or cov.requirement_id not in reqs:
            continue
        r = reqs[cov.requirement_id]
        w = r.weight * KIND_FACTOR.get(r.kind, 1)
        n = sum(y[s.claim_id] for s in supports)
        hit = model.new_bool_var(f"any_{r.id}")
        model.add(hit <= n)
        extra = model.new_int_var(0, 2, f"extra_{r.id}")
        model.add(extra <= n - hit)
        value += [50 * w * hit, 10 * w * extra]
        strong = [y[s.claim_id] for s in supports if s.strength == "strong"]
        if strong:
            top = model.new_bool_var(f"strong_{r.id}")
            model.add(top <= sum(strong))
            value.append(50 * w * top)
    track = next((t for t in bank.tables.tracks if t.target == m.track), None)
    lead = set(track.lead_entries) if track else set()
    support = set(track.support_entries) if track else set()
    for cid, c in claims.items():
        quality = PROOF_VALUE[c.proof] + (6 if c.metrics else 0) + (4 if c.kind == "lead" else 0)
        quality += 15 if cid in lead else 8 if cid in support else 0
        if ".n" in cid:  # promoted from the entry's notes: supporting detail, not a headline bullet
            quality -= 8
        if entries[c.entry_id].kind in ONE_LINE_KINDS:
            quality += 15 if _PLACED.search(md_plain(c.text)) else 0
        value.append(quality * y[cid])
    for eid, entry in entries.items():
        fit = 25 if eid in lead else 12 if eid in support else 0
        recency = _recency(entry) // (2 if entry.kind in ONE_LINE_KINDS else 1)
        value.append((fit + recency + KIND_VALUE.get(entry.kind, 0)) * x[eid])
    model.maximize(sum(value))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 1  # deterministic: the same inputs give the same plan
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SelectionError("no plan fits: check pins in plan.edits.yaml against the page budget and exclusions")

    picked = {cid for cid in claims if solver.value(y[cid])}
    return _selection(bank, spec, m, edits, pages, layout, budget, picked, solver.value(sum(cost)) / SCALE)


def _selection(bank, spec, m, edits, pages, layout, budget, picked, used) -> Selection:
    fixed = fixed_lines(layout, pages, bank)
    supports_of: dict[str, list[str]] = defaultdict(list)
    for cov in m.coverage:
        for sup in cov.support:
            supports_of[sup.claim_id].append(cov.requirement_id)
    picks = []
    for entry in bank.entries.values():
        cids = [c.id for c in entry.claims if c.id in picked]
        if not cids:
            continue
        covers = sorted({r for c in cids for r in supports_of.get(c, [])}, key=lambda r: (len(r), r))
        lines = {c: claim_lines(bank.claim(c), entry, layout) for c in cids}
        picks.append(Pick(entry_id=entry.id, section=SECTIONS[entry.kind], claim_ids=cids, lines=lines, covers=covers))
    picks.sort(key=lambda p: _sort_key(bank.entries[p.entry_id]), reverse=True)  # newest first within a section
    picks.sort(key=lambda p: SECTION_ORDER.index(p.section))
    covered = {}
    for cov in m.coverage:
        chosen = [s for s in cov.support if s.claim_id in picked]
        covered[cov.requirement_id] = (
            "strong" if any(s.strength == "strong" for s in chosen) else "partial" if chosen else "none"
        )
    excluded = _excluded(bank, m, edits, picked)
    return Selection(job=spec.key, track=m.track, paper=layout.paper, pages=pages, lines_budget=budget + fixed,
                     lines_used=round(used + fixed, 1), picks=picks, covered=covered, excluded=excluded,
                     edits=edits)  # fmt: skip


def _excluded(bank: Bank, m: Match, edits: PlanEdits, picked: set[str]) -> dict[str, str]:
    """Why each entry with matched support was left out."""
    picked_entries = {c.split(".")[0] for c in picked}
    pairs = exclusions(bank)
    out = {}
    for entry_id in dict.fromkeys(s.claim_id.split(".")[0] for c in m.coverage for s in c.support):
        if entry_id in picked_entries or entry_id not in bank.entries:
            continue
        own = {c.id for c in bank.entries[entry_id].claims}
        if entry_id in edits.ban or own & set(edits.ban) == own:
            out[entry_id] = "banned in plan.edits.yaml"
            continue
        rivals = sorted({c.split(".")[0] for a, b, _ in pairs for side, other in ((a, b), (b, a))
                         if set(side) & own for c in other if c in picked})  # fmt: skip
        out[entry_id] = f"same work as {', '.join(rivals)}" if rivals else "page budget"
    return out


# ---------------------------------------------------------------- plan


def load_edits(folder: Path) -> PlanEdits:
    path = folder / EDITS_FILE
    if not path.is_file():
        return PlanEdits()
    return PlanEdits.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _coverage_line(spec: JobSpec, m: Match, sel: Selection) -> str:
    placed = [c.model_copy(update={"verdict": sel.covered.get(c.requirement_id, "none")}) for c in m.coverage]
    after = m.model_copy(update={"coverage": placed})
    parts = []
    for kind, label in (("basic", "basic"), ("preferred", "preferred"), ("responsibility", "responsibilities")):
        share, best = weighted_coverage(spec, after, kind), weighted_coverage(spec, m, kind)
        if share is not None:
            parts.append(f"{label} {share:.0%}" + (f" (matched {best:.0%})" if best != share else ""))
    return "Coverage on the page (strong counts fully, partial half): " + ", ".join(parts) + "."


def render_plan(bank: Bank, spec: JobSpec, m: Match, sel: Selection) -> str:
    reqs = {r.id: r for r in spec.requirements}
    verdict = {c.requirement_id: c.verdict for c in m.coverage}
    supports: dict[str, list[str]] = defaultdict(list)
    for cov in m.coverage:
        for sup in cov.support:
            supports[sup.claim_id].append(cov.requirement_id)
    page = "page" if sel.pages == 1 else "pages"
    lines = [
        f"# Plan: {spec.title} at {spec.company}", "",
        f"{sel.paper.capitalize() if sel.paper == 'letter' else 'A4'}, {sel.pages} {page}, about {sel.lines_used:g} "
        f"of {sel.lines_budget:g} lines (estimated until rendering). Track: **{sel.track}**.", "",
        _coverage_line(spec, m, sel), "",
    ]  # fmt: skip
    lost = [r for r, v in sel.covered.items() if v != verdict.get(r)]
    if lost:
        changes = "; ".join(f"{r} {reqs[r].text} ({verdict[r]} → {sel.covered[r]})" for r in lost)
        lines += [f"Lost to the page budget or your edits: {changes}.", ""]
    if sel.edits.pin or sel.edits.ban or sel.edits.pages:
        applied = [f"pinned {', '.join(sel.edits.pin)}" if sel.edits.pin else "",
                   f"banned {', '.join(sel.edits.ban)}" if sel.edits.ban else "",
                   f"{sel.edits.pages} {page}" if sel.edits.pages else ""]  # fmt: skip
        lines += ["Edits applied: " + "; ".join(a for a in applied if a) + ".", ""]
    edu = bank.entries.get("EDU-01")
    if edu:
        lines += ["## Education", "", f"- {entry_label(edu)} (always shown)", ""]
    section = None
    for pick in sel.picks:
        entry = bank.entries[pick.entry_id]
        if pick.section != section:
            if section is not None and lines[-1]:
                lines.append("")
            section = pick.section
            lines += [f"## {section}", ""]
        one_line = entry.kind in ONE_LINE_KINDS
        if not one_line:
            team = " (team work)" if entry.team else ""
            lines += [f"### {entry_label(entry)}{team}", ""]
        for cid in pick.claim_ids:
            claim = bank.claim(cid)
            covers = ", ".join(sorted(set(supports.get(cid, [])), key=lambda r: (len(r), r)))
            facts = [f"{pick.lines[cid]} line" + ("s" if pick.lines[cid] > 1 else ""), f"proof {claim.proof}"]
            if covers:
                facts.append(covers)
            prefix = f"{entry.name}: " if one_line else ""
            lines.append(f"- `{cid}` ({'; '.join(facts)}) {prefix}{display_text(claim)}")
        if not one_line:
            lines.append("")
    if lines[-1]:
        lines.append("")
    lines += ["## Skills", "", "Written in the next stage from the skills bank, for this track.", ""]
    if sel.excluded:
        lines += ["## Left out", "", "| Entry | Why | Would support |", "|---|---|---|"]
        for entry_id, why in sel.excluded.items():
            would = sorted({r for c in bank.entries[entry_id].claims for r in supports.get(c.id, [])},
                           key=lambda r: (len(r), r))  # fmt: skip
            lines.append(f"| {entry_label(bank.entries[entry_id])} | {why} | {', '.join(would)} |")
        lines.append("")
    lines += [
        "## Change this plan", "",
        f"Write `{EDITS_FILE}` next to this file, then run `uv run tailor plan` again:", "",
        "```yaml",
        "pin: [RES-06, ENG-02.b4]   # entries or claims that must appear",
        "ban: [AWD-07]              # entries or claims that must not",
        "pages: 1                   # optional: override the page count",
        "```", "",
    ]  # fmt: skip
    return "\n".join(lines)
