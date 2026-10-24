"""Stage 5: check the tailored resume against the evidence bank.

Deterministic checks first: every number, name, tool and date in a bullet must
appear in its cited claims, and a metric that keeps its number keeps its
condition (record section 15). `lint` applies the record's policy (sections
12.3, 14 and 15: team credit, repository visibility, protocol boundaries) and
style rules. `write_pack` asks for an entailment review of each bullet and a
screener's reading of the page; `check_review` validates `review.json`, sends
supported requirements that read as Unknown back to writing, and grades the
page A–D the way a HiredScore-style screen would.
"""

import json
import re
from pathlib import Path

from . import numbers
from . import selection as sel_mod
from .bank import Bank
from .jobspec import norm
from .match import tokens
from .models import Finding, JobSpec, Review, Selection, TailoredBullet, Writing
from .textutil import md_plain
from .write import entry_link, render_draft, sources

PACK_FILE = "verify.prompt.md"
REVIEW_FILE = "review.json"
REPORT_FILE = "check_report.md"

# A name or tool: has a capital after the first letter (ScreenCaptureKit, CNN/GRU), a digit (Qwen3, Base64),
# an inner dot (llama.cpp), or a leading capital mid-sentence (Unify, Munich). Hyphenated compounds are
# checked part by part, so "direct-vs-English-pivot" asks only about "English".
_WORD = re.compile(r"(?<![\w.+#/'’-])[A-Za-z][\w.+#/'’-]*")
_SENTENCE_START = re.compile(r"(?:^|[:;(]\s*|[.!?]\s+)$")


def _clean(word: str) -> str:
    word = re.sub(r"['’]s$", "", word)
    return word.rstrip(".,;:)-/'’")


def _proper(part: str, opens_clause: bool) -> bool:
    inner_caps = any(ch.isupper() for ch in part[1:])
    technical = any(ch.isdigit() for ch in part) or re.search(r"\w\.\w", part)
    return bool(inner_caps or technical or (part[0].isupper() and not opens_clause))


def names(text: str) -> list[str]:
    """Names, tools and other proper terms in a bullet, skipping words that open a sentence or clause."""
    out = []
    for m in _WORD.finditer(text):
        word = _clean(m.group())
        opens = bool(_SENTENCE_START.search(text[: m.start()]))
        for i, part in enumerate(p for p in word.split("-") if p):
            if part[0].isalpha() and _proper(part, opens and i == 0):
                out.append(part)
    return list(dict.fromkeys(out))


def _label(b: TailoredBullet) -> str:
    return "+".join(b.claim_ids)


def _finding(check: str, b: TailoredBullet, message: str, severity: str = "error") -> Finding:
    return Finding(check=check, severity=severity, message=f"{_label(b)}: {message}", bullet=md_plain(b.text)[:80])


def facts(w: Writing, bank: Bank, sel: Selection) -> list[Finding]:
    findings: list[Finding] = []
    picked = {p.entry_id for p in sel.picks}
    for b in w.bullets:
        if b.entry_id not in picked:
            continue  # the draft guard reports bullets outside the plan
        text = md_plain(b.text)
        entry = bank.entries[b.entry_id]
        claims = [bank.claim(c) for c in b.claim_ids]
        source = sources(bank, entry, claims)
        lowered = source.lower()

        if extra := numbers.tokens(text, years=True) - numbers.tokens(source, years=True):
            findings.append(_finding("numbers", b, f"states {', '.join(sorted(extra))}, which its claims don't"))
        if unknown := [n for n in names(text) if n.lower() not in lowered]:
            findings.append(_finding("names", b, f"names {', '.join(unknown)}, which its claims don't"))
        said = set(tokens(text)) | numbers.tokens(text, years=True)
        for claim in claims:
            for condition in claim.conditions:
                metric, _, qualifier = md_plain(condition).partition(" — ")
                if not qualifier or not numbers.tokens(text, years=True) & numbers.tokens(metric, years=True):
                    continue
                if not (set(tokens(qualifier)) | numbers.tokens(qualifier, years=True)) & said:
                    findings.append(_finding("conditions", b, f"keeps {metric} but drops its condition: {qualifier}"))
    return findings


# ---------------------------------------------------------------- policy and style lint

_LINK = re.compile(r"https?://|www\.|github\.com/|\b[\w-]+\.(?:com|io|ai|org|dev|net)\b/?")
# Team wording, or "with" followed by a named person ("with Prof. Naskar"), not "packaging with signal profiles".
_TEAM = re.compile(
    r"\b(?:[Tt]eam|[Cc]o-[A-Za-z]+|[Hh]elped|[Cc]ollaborat\w*|[Jj]ointly)\b|\bwith (?:Prof\.|Dr\.|[A-Z][a-z]+ [A-Z])"
)
_SOLVED = re.compile(r"\bsolved\s+\d+(?:\s+of\s+\d+)?\s+(?:games?|levels?|tasks?|puzzles?)\b", re.I)
_ARNESH = re.compile(r"\bArnesh\b(?!\s+(?:Banerjee|Gade)\b)")
_FIRST_PERSON = re.compile(r"\bI\b|\b(?:my|mine|we|our|ours|me)\b")
_HIDDEN = re.compile(r"[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\x00-\x08\x0b-\x1f]")
_NUMERIC_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b")
_ACRONYM = re.compile(r"^[A-Z][A-Z0-9]+s?$")


def _texts(w: Writing, plain: bool = True) -> list[tuple[str, str]]:
    """(where, text) for every written string on the page, as read (plain) or as typed."""
    out = [("headline", w.headline), ("summary", w.summary)]
    out += [(_label(b), md_plain(b.text) if plain else b.text) for b in w.bullets]
    out += [(f"skills: {group}", ", ".join(terms)) for group, terms in w.skills.items()]
    return [(where, text) for where, text in out if text]


def lint(w: Writing, bank: Bank, spec: JobSpec, sel: Selection) -> list[Finding]:
    findings: list[Finding] = []

    def add(check: str, severity: str, message: str, text: str = "") -> None:
        findings.append(Finding(check=check, severity=severity, message=message, bullet=text[:80]))

    repos = {r.name.lower(): r for r in bank.tables.repos}
    by_entry: dict[str, list[TailoredBullet]] = {}
    for b in w.bullets:
        by_entry.setdefault(b.entry_id, []).append(b)
    for pick in sel.picks:
        entry = bank.entries[pick.entry_id]
        bullets = by_entry.get(entry.id, [])
        link = entry_link(bank, entry)
        if link:
            repo = repos.get(link.removeprefix("github.com/").lower())
            if repo is None:
                add("links", "warning", f"{entry.id} links {link}, which isn't in the repository inventory")
            elif repo.visibility != "public":
                add("links", "error", f"{entry.id} links {link}, a {repo.visibility} repository")
        if entry.team:
            if entry.kind in sel_mod.ONE_LINE_KINDS:
                for b in bullets:
                    if not re.search(r"\bteam\b", b.text, re.I):
                        add("team", "error", f"{_label(b)}: a team result must say it is a team's", b.text)
            elif not _TEAM.search(entry.subtitle) and not any(_TEAM.search(b.text) for b in bullets):
                add("team", "warning", f"{entry.id} is team work, but no bullet or the role says so")
    for b in w.bullets:
        text = md_plain(b.text)
        if _LINK.search(text):
            add("links", "error", f"{_label(b)}: links go in entry headings, not bullets", text)
        for cid in b.claim_ids:
            claim = bank.claim(cid) if cid.split(".")[0] in bank.entries else None
            if claim is not None and (claim.status != "current" or not claim.show_on_resume):
                add("policy", "error", f"{_label(b)}: cites {cid}, which is superseded or hidden", text)
        if len(b.claim_ids) > 1:
            said = numbers.tokens(text, years=False)
            owners = [c for c in b.claim_ids if said & numbers.tokens(md_plain(bank.claim(c).text))]
            if len(owners) > 1:
                add("protocol", "warning", f"{_label(b)}: results from {', '.join(owners)} in one line; check they "
                                           "come from the same protocol", text)  # fmt: skip
    for where, text in _texts(w):
        if _SOLVED.search(text):
            add("policy", "error", f"{where}: report scores and levels, not 'solved N games'", text)
        if _ARNESH.search(text):
            add("policy", "error", f"{where}: name Arnesh Banerjee or Arnesh Gade in full; they are different people",
                text)  # fmt: skip
        if _FIRST_PERSON.search(text):
            add("style", "error", f"{where}: no first person", text)
        if _NUMERIC_DATE.search(text):
            add("style", "warning", f"{where}: write dates as words (Oct 2026), not numbers", text)
        if where not in ("headline", "summary") and not where.startswith("skills") and text[:1].islower():
            add("style", "warning", f"{where}: start with a capital letter", text)
    for where, text in _texts(w, plain=False):
        if _HIDDEN.search(text):
            add("style", "error", f"{where}: contains hidden or control characters", text)
        if "  " in text.strip():
            add("style", "warning", f"{where}: double space", text)
    page = " ".join(text for _, text in _texts(w))
    if "'" in page and "’" in page:
        add("style", "warning", "straight and curly apostrophes are mixed; use one kind")
    for term, synonyms in spec.keywords.items():
        forms = [term, *synonyms]
        short = [f for f in forms if _ACRONYM.match(f)]
        long = [f for f in forms if " " in f]
        if not short or not long:
            continue
        has_short = any(re.search(rf"(?<!\w){re.escape(f)}(?!\w)", page) for f in short)
        has_long = any(f.lower() in page.lower() for f in long)
        if has_short != has_long:
            missing = long[0] if has_short else short[0]
            add("keywords", "warning", f"the posting's term appears in one form only; add “{missing}” once for "
                                       "screeners that search the other")  # fmt: skip
    return findings


# ---------------------------------------------------------------- review


def page_text(bank: Bank, spec: JobSpec, sel: Selection, w: Writing) -> str:
    """The draft as a screener reads it: plain text, one line per resume line."""
    lines = []
    for line in render_draft(bank, spec, sel, w).split("\n"):
        line = re.sub(r"^(?:#+\s*|-\s+)", "", line.strip())
        lines.append(md_plain(line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


_PACK = """\
# Review the draft for {company}: {title}

Write `{review_path}`: JSON matching the schema at the end. Then run `uv run tailor check {folder}`.

## 1. Entailment, bullet by bullet

For each bullet below, `entailed` is true only if everything it says follows from its sources:
paraphrase and omission are fine; a broader scope, a stronger ownership verb, a number or name that
isn't there, a dropped qualifier that changes the meaning, or a historical result stated as current
is not. When false, `problem` says exactly what isn't supported. One review per bullet, identified by
its `claim_ids`.

{bullets}

## 2. Screener

Now forget the sources. Read only the resume text below, as a recruiter's screen or an Ashby-style
reviewer would, and judge each requirement: `meets` only if a line on the page shows it (copy that
line or a run of at least three words from it, verbatim, into `quote`); `unknown` if the page only
hints at it; `does not meet` if the page shows nothing for it. Be strict: a screener doesn't infer.

{requirements}

### Resume text

```text
{page}```

## Schema

```json
{schema}
```
"""


def write_pack(folder: Path, bank: Bank, spec: JobSpec, sel: Selection, w: Writing) -> Path:
    blocks = []
    for b in w.bullets:
        entry = bank.entries[b.entry_id]
        claims = [bank.claim(c) for c in b.claim_ids]
        lines = [
            f"### {_label(b)} · {entry.name}" + (" (team work)" if entry.team else ""),
            "",
            f"Bullet: {b.text}",
            "",
        ]
        lines += [f"- Source `{c.id}`: {sel_mod.display_text(c)}" + (f" ({md_plain(c.details)})" if c.details else "")
                  for c in claims]  # fmt: skip
        lines += [f"- Condition: {md_plain(cond)}" for c in claims for cond in c.conditions]
        if entry.notes.get("Team credit"):
            lines.append(f"- Team credit: {md_plain(entry.notes['Team credit'])}")
        blocks.append("\n".join(lines))
    reqs = [f"- {r.id} ({r.kind}): “{r.quote}”" for r in spec.requirements if r.kind != "knockout"]
    pack = folder / PACK_FILE
    pack.write_text(_PACK.format(
        company=spec.company, title=spec.title, review_path=folder / REVIEW_FILE, folder=folder,
        bullets="\n\n".join(blocks), requirements="\n".join(reqs), page=page_text(bank, spec, sel, w),
        schema=json.dumps(Review.model_json_schema(), indent=2),
    ), encoding="utf-8")  # fmt: skip
    return pack


def grade(spec: JobSpec, review: Review) -> tuple[str, float | None, float | None]:
    """A–D from the screener's verdicts: the weighted share of basic requirements it marks Meets
    (A ≥ 90% with none unmet, B ≥ 75%, C ≥ 50%, D below), plus the preferred share for context."""
    verdicts = {v.requirement_id: v.verdict for v in review.screener}

    def share(kind: str) -> float | None:
        reqs = [r for r in spec.requirements if r.kind == kind]
        total = sum(r.weight for r in reqs)
        return sum(r.weight for r in reqs if verdicts.get(r.id) == "meets") / total if total else None

    basic = share("basic")
    if basic is None:
        basic = share("responsibility")
    unmet = any(verdicts.get(r.id) == "does not meet" for r in spec.requirements if r.kind == "basic")
    if basic is None:
        letter = "?"
    elif basic >= 0.9 and not unmet:
        letter = "A"
    elif basic >= 0.75:
        letter = "B"
    elif basic >= 0.5:
        letter = "C"
    else:
        letter = "D"
    return letter, basic, share("preferred")


def check_review(review: Review, w: Writing, spec: JobSpec, sel: Selection, text: str) -> list[Finding]:
    findings: list[Finding] = []

    def add(check: str, severity: str, message: str) -> None:
        findings.append(Finding(check=check, severity=severity, message=message))

    if review.job != spec.key:
        add("review", "error", f"review is for {review.job}, draft is for {spec.key}")
    wanted = {tuple(b.claim_ids) for b in w.bullets}
    seen = [tuple(r.claim_ids) for r in review.bullets]
    for key in sorted(wanted - set(seen)):
        add("review", "error", f"{'+'.join(key)} has no entailment review")
    for key in sorted(set(seen) - wanted):
        add("review", "error", f"{'+'.join(key)} is reviewed but isn't a bullet in the draft")
    for r in review.bullets:
        if not r.entailed:
            why = r.problem or "no problem given; say what isn't supported"
            add("entailment", "error", f"{'+'.join(r.claim_ids)} doesn't follow from its claims: {why}")
    judged = {r.id for r in spec.requirements if r.kind != "knockout"}
    ids = [v.requirement_id for v in review.screener]
    if missing := sorted(judged - set(ids), key=lambda i: (len(i), i)):
        add("screener", "error", f"requirements the screener didn't judge: {', '.join(missing)}")
    if extra := sorted(set(ids) - judged, key=lambda i: (len(i), i)):
        add("screener", "error", f"the screener judged items that aren't requirements: {', '.join(extra)}")
    page = norm(text)
    for v in review.screener:
        if v.verdict == "meets" and (len(v.quote.split()) < 3 or norm(md_plain(v.quote)) not in page):
            add("screener", "error", f"{v.requirement_id} meets, but its quote isn't a verbatim run of three or "
                                     f"more words from the page: {v.quote[:70]!r}")  # fmt: skip
        planned = sel.covered.get(v.requirement_id, "none")
        if planned == "strong" and v.verdict != "meets":
            add("screener", "error", f"{v.requirement_id} is supported on the page but reads as {v.verdict}: "
                                     "rewrite its bullet in the posting's words (tailor write)")  # fmt: skip
        elif planned == "partial" and v.verdict == "does not meet":
            add("screener", "warning", f"{v.requirement_id} has partial support on the page, but reads as "
                                       "does not meet")  # fmt: skip
    return findings


# ---------------------------------------------------------------- report


def render_report(spec: JobSpec, w: Writing, checks: list[Finding], review: Review | None,
                  review_findings: list[Finding], fit: list[str] | None = None) -> str:  # fmt: skip
    findings = checks + review_findings
    ok = not any(f.severity == "error" for f in findings)
    lines = [f"# Check report: {spec.title} at {spec.company}", ""]
    if review is None:
        lines += [
            "**Waiting for the review.** Fill `review.json` from `verify.prompt.md`, then run `tailor check`.",
            "",
        ]
    else:
        lines += ["**Ready for rendering.**" if ok else "**Fix the errors below, then rerun `tailor check`.**", ""]
        letter, basic, preferred = grade(spec, review)
        shares = [f"basic {basic:.0%} Meets" if basic is not None else "",
                  f"preferred {preferred:.0%}" if preferred is not None else ""]  # fmt: skip
        lines += [f"Estimated screener grade: **{letter}** ({', '.join(x for x in shares if x)}).", ""]

    def section(title: str, items: list[Finding]) -> None:
        lines.extend([f"## {title}", ""])
        for f in items:
            lines.append(f"- {f.severity}: [{f.check}] {f.message}" + (f" — “{f.bullet}”" if f.bullet else ""))
        if not items:
            lines.append("- No findings.")
        lines.append("")

    if fit:
        lines += ["## Render", "", *[f"- {step}" for step in fit], ""]
    section("Facts, policy, style and extraction", checks)
    if review is not None:
        entailed = sum(r.entailed for r in review.bullets)
        lines += [f"{entailed} of {len(w.bullets)} bullets follow from their claims.", ""]
        section("Review", review_findings)
        reqs = {r.id: r for r in spec.requirements}
        lines += ["## Screener", "", "| Req | Kind | Verdict | Line on the page |", "|---|---|---|---|"]
        for v in review.screener:
            r = reqs.get(v.requirement_id)
            kind = r.kind if r else "?"
            lines.append(f"| {v.requirement_id} | {kind} | {v.verdict} | {v.quote.replace('|', '/')} |")
        lines.append("")
    return "\n".join(lines)
