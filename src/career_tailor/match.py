"""Stage 2: map each JobSpec requirement to supporting claims.

Retrieval is deterministic: BM25 over claim text plus skills-bank hits, with a
boost for entries on the job's role track (record section 12.1). Knockouts are
left to `knockouts.py`.

Judgement is LLM work: `write_pack` lists each requirement with its retrieved
claims (and every other eligible claim), and Claude writes `match.json`. Each
support quotes the claim verbatim; `validate` checks the quotes, the claim ids
and that verdicts agree with their supports.
"""

import json
import math
import re
from collections import Counter
from pathlib import Path

from .annotate import Tagger
from .bank import Bank
from .jobspec import norm
from .models import CheckReport, Claim, Entry, Finding, Hit, JobSpec, Match, Requirement, Retrieval, Track
from .textutil import md_plain

MATCH_FILE = "match.json"
PACK_FILE = "match.prompt.md"
HITS_FILE = "match.hits.json"

TOP_K = 8
MAX_SUPPORT = 4
PER_ENTRY = 3
SKILL_BONUS = 0.4
ENTRY_SHARE = 0.4  # how much the whole entry's relevance counts next to the claim's own
TRACK_BOOST = {"lead": 1.25, "support": 1.1}

_STOP = set(
    "a an and are as at be by can do for from has have in into is it its of on or our such that the their this "
    "to we will with you your who what when how not all any more most other some than very well within "
    "experience ability strong including etc".split()
)
_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ities", "ity", "ies", "ed", "es", "s")
# Stems that share a prefix but not a suffix rule: "evals", "evaluation", "evaluate" -> "eval".
_PREFIXES = ("eval", "benchmark", "experiment", "statistic", "reproduc", "interpret", "optimi", "measur", "debug")


def stem(word: str) -> str:
    for prefix in _PREFIXES:
        if word.startswith(prefix):
            return prefix
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+#]*", md_plain(text).lower())
    return [stem(w) for w in words if w not in _STOP and len(w) > 1]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = [Counter(d) for d in docs]
        self.lengths = [len(d) for d in docs]
        self.avg = sum(self.lengths) / len(docs) if docs else 0.0
        df = Counter(t for d in self.docs for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query: list[str]) -> list[float]:
        terms = set(query)
        out = []
        for doc, length in zip(self.docs, self.lengths, strict=True):
            norm_len = self.k1 * (1 - self.b + self.b * length / self.avg) if self.avg else self.k1
            out.append(sum(self.idf[t] * doc[t] * (self.k1 + 1) / (doc[t] + norm_len) for t in terms if t in doc))
        return out


def eligible(bank: Bank) -> list[tuple[Entry, Claim]]:
    """Claims that may go on a resume: current and not hidden by the overlay."""
    return [(e, c) for e in bank.entries.values() for c in e.claims if c.status == "current" and c.show_on_resume]


def claim_text(claim: Claim) -> str:
    """The claim as a screener would read it, without Markdown. Table rows keep their title
    and detail column; a fact's label is a column name, so it is left out."""
    parts = (claim.label, claim.text, claim.details) if claim.kind == "row" else (claim.text,)
    return md_plain(" ".join(x for x in parts if x))


def _doc(entry: Entry, claim: Claim) -> list[str]:
    return tokens(" ".join([claim_text(claim), entry.name, entry.subtitle, " ".join(claim.skills)]))


def _entry_doc(entry: Entry) -> list[str]:
    parts = [entry.name, entry.subtitle, *entry.stack, *entry.skills, *entry.notes.values()]
    parts += [claim_text(c) for c in entry.claims if c.status == "current" and c.show_on_resume]
    return tokens(" ".join(parts))


def rank_tracks(tracks: list[Track], spec: JobSpec, relevance: dict[str, float]) -> list[str]:
    """Role tracks by fit to the job, best first. A suggestion: the judgement pack confirms it.

    Two signals: the posting's title and keywords against the track's name and headline, and how
    relevant the track's lead entries (and, at a third of the weight, support entries) are to the job.
    """
    if not tracks:
        return []
    index = BM25([tokens(f"{t.target} {t.headline}") for t in tracks])
    headline = _weighted(index, Counter(tokens(spec.title) * 3 + tokens(" ".join(spec.keywords))))
    top = max(headline, default=0.0) or 1.0

    def mean(items: list[str]) -> float:
        values = [relevance.get(i.split(".")[0], 0.0) for i in items]
        return sum(values) / len(values) if values else 0.0

    scores = [
        h / top + mean(t.lead_entries) + mean(t.support_entries) / 3 for t, h in zip(tracks, headline, strict=True)
    ]
    order = sorted(range(len(tracks)), key=lambda i: -scores[i])
    return [tracks[i].target for i in order if scores[i] > 0]


def job_query(spec: JobSpec) -> Counter:
    """The whole job as one weighted query: each requirement by its weight, the title six times."""
    query = Counter(tokens(spec.title) * 6)
    for req in spec.requirements:
        if req.kind != "knockout":
            query.update({t: w * req.weight for t, w in query_tokens(req, spec).items()})
    return query


def query_text(req: Requirement, spec: JobSpec) -> str:
    """The requirement plus the spec keywords (and synonyms) that its quote mentions."""
    quote = norm(req.quote)
    extra = [
        " ".join([term, *syns])
        for term, syns in spec.keywords.items()
        if any(norm(t) and norm(t) in quote for t in [term, *syns])
    ]
    return " ".join([req.text, req.quote, *req.keywords, *extra])


def query_tokens(req: Requirement, spec: JobSpec) -> Counter:
    """Weighted query terms: the requirement's short name and keywords count twice, the quote once,
    so the posting's filler words don't outrank what is being asked for."""
    focus = tokens(" ".join([req.text, *req.keywords]))
    rest = tokens(query_text(req, spec))
    return Counter(focus) + Counter(rest)


def _weighted(index: BM25, query: Counter) -> list[float]:
    total = [0.0] * len(index.docs)
    for term, weight in query.items():
        for i, s in enumerate(index.scores([term])):
            total[i] += s * weight
    return total


def _track_role(track: Track | None, entry: Entry, claim: Claim) -> str:
    if track is None:
        return ""
    if claim.id in track.lead_entries or entry.id in track.lead_entries:
        return "lead"
    if claim.id in track.support_entries or entry.id in track.support_entries:
        return "support"
    return ""


def retrieve(bank: Bank, spec: JobSpec, top_k: int = TOP_K) -> Retrieval:
    pool = eligible(bank)
    index = BM25([_doc(e, c) for e, c in pool])
    entry_ids = list(dict.fromkeys(e.id for e, _ in pool))
    entry_index = BM25([_entry_doc(bank.entries[i]) for i in entry_ids])
    tagger = Tagger(bank.tables.skills)
    job_raw = _weighted(entry_index, job_query(spec))
    job_top = max(job_raw, default=0.0) or 1.0
    relevance = {i: s / job_top for i, s in zip(entry_ids, job_raw, strict=True)}
    ranked = rank_tracks(bank.tables.tracks, spec, relevance)
    track = next((t for t in bank.tables.tracks if ranked and t.target == ranked[0]), None)
    out: dict[str, list[Hit]] = {}
    for req in spec.requirements:
        if req.kind == "knockout":
            continue
        wanted = set(tagger.find(query_text(req, spec)))
        query = query_tokens(req, spec)
        raw = _weighted(index, query)
        top = max(raw, default=0.0) or 1.0
        entry_raw = _weighted(entry_index, query)
        entry_top = max(entry_raw, default=0.0) or 1.0
        entry_score = {i: s / entry_top for i, s in zip(entry_ids, entry_raw, strict=True)}
        scored = []
        for (entry, claim), bm in zip(pool, raw, strict=True):
            shared = [s for s in claim.skills if s in wanted]
            if bm <= 0 and not shared:
                continue
            score = (1 - ENTRY_SHARE) * bm / top + ENTRY_SHARE * entry_score[entry.id] + SKILL_BONUS * len(shared)
            if score <= 0:
                continue
            role = _track_role(track, entry, claim)
            score *= TRACK_BOOST.get(role, 1.0)
            scored.append(Hit(claim_id=claim.id, score=round(score, 3), skills=shared, track=role))
        scored.sort(key=lambda c: -c.score)
        picked, per_entry = [], Counter()
        for cand in scored:
            entry_id = cand.claim_id.split(".")[0]
            if per_entry[entry_id] < PER_ENTRY:
                picked.append(cand)
                per_entry[entry_id] += 1
            if len(picked) == top_k:
                break
        out[req.id] = picked
    return Retrieval(key=spec.key, tracks=ranked, hits=out)


# ---------------------------------------------------------------- judgement


def judged(spec: JobSpec) -> list[Requirement]:
    """The requirements a match must cover: everything but knockouts."""
    return [r for r in spec.requirements if r.kind != "knockout"]


def validate(m: Match, spec: JobSpec, bank: Bank) -> CheckReport:
    report = CheckReport()

    def add(check: str, severity: str, message: str) -> None:
        report.findings.append(Finding(check=check, severity=severity, message=message))

    if m.key != spec.key:
        add("job", "error", f"match is for {m.key}, spec is {spec.key}")
    if m.track not in {t.target for t in bank.tables.tracks}:
        add("track", "error", f"unknown track {m.track!r}; use one of the record's section 12.1 targets")
    wanted = {r.id for r in judged(spec)}
    knockouts = {r.id for r in spec.requirements} - wanted
    ids = [c.requirement_id for c in m.coverage]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        add("ids", "error", f"requirements judged twice: {', '.join(dupes)}")
    if missing := sorted(wanted - set(ids), key=_req_order):
        add("ids", "error", f"requirements not judged: {', '.join(missing)}")
    for rid in sorted(set(ids) & knockouts, key=_req_order):
        add("ids", "error", f"{rid} is a knockout; knockouts are judged in knockouts.md, not here")
    for rid in sorted(set(ids) - wanted - knockouts, key=_req_order):
        add("ids", "error", f"{rid} is not a requirement in the spec")
    pool = {c.id: c for _, c in eligible(bank)}
    for cov in m.coverage:
        rid = cov.requirement_id
        strengths = {s.strength for s in cov.support}
        if cov.verdict == "strong" and "strong" not in strengths:
            add("verdict", "error", f"{rid} is strong but has no strong support")
        if cov.verdict == "partial" and (not cov.support or "strong" in strengths):
            add("verdict", "error", f"{rid} is partial, so it needs supports and none of them strong")
        if cov.verdict == "none" and cov.support:
            add("verdict", "error", f"{rid} is none but lists supports")
        if cov.verdict != "strong" and not cov.note:
            add("note", "warning", f"{rid} is {cov.verdict}; say in the note what is missing")
        if len(cov.support) > MAX_SUPPORT:
            add("support", "warning", f"{rid} lists {len(cov.support)} supports; keep the best {MAX_SUPPORT}")
        cited = [s.claim_id for s in cov.support]
        if dupes := sorted({c for c in cited if cited.count(c) > 1}):
            add("support", "error", f"{rid} cites {', '.join(dupes)} more than once")
        for sup in cov.support:
            claim = pool.get(sup.claim_id)
            if claim is None:
                why = "is hidden or superseded" if _exists(bank, sup.claim_id) else "does not exist"
                add("claim", "error", f"{rid} cites {sup.claim_id}, which {why}")
            elif norm(md_plain(sup.quote)) not in norm(claim_text(claim)):
                add("quote", "error", f"{rid} quote is not verbatim from {sup.claim_id}: {sup.quote[:80]!r}")
            elif len(sup.quote.split()) < 3:
                add("quote", "warning", f"{rid} quote from {sup.claim_id} is very short; quote the words that show it")
    return report


def _req_order(rid: str) -> tuple:
    return (len(rid), rid)


def _exists(bank: Bank, claim_id: str) -> bool:
    try:
        bank.claim(claim_id)
    except (KeyError, StopIteration):
        return False
    return True


def load_match(folder: Path) -> tuple[Match | None, CheckReport]:
    path = folder / MATCH_FILE
    if not path.is_file():
        missing = Finding(check="match", severity="error", message=f"no {MATCH_FILE} yet; fill it from {PACK_FILE}")
        return None, CheckReport(findings=[missing])
    try:
        return Match.model_validate_json(path.read_text(encoding="utf-8")), CheckReport()
    except ValueError as err:
        return None, CheckReport(findings=[Finding(check="schema", severity="error", message=str(err)[:500])])


# ---------------------------------------------------------------- prompt pack


def _claim_line(bank: Bank, claim: Claim) -> str:
    entry = bank.entries[claim.entry_id]
    facts = [f"proof: {claim.proof}"]
    if entry.team:
        facts.append("team work")
    if claim.conditions:
        facts.append("conditions: " + "; ".join(md_plain(c) for c in claim.conditions))
    return f"`{claim.id}` {claim_text(claim)} *({'; '.join(facts)})*"


def _entry_label(entry: Entry) -> str:
    dates = f", {entry.dates.display}" if entry.dates and entry.dates.display else ""
    subtitle = f" — {entry.subtitle}" if entry.subtitle else ""
    return f"{entry.id} · {entry.name}{subtitle}{dates}"


_PACK = """\
# Match {company}'s requirements to the evidence bank

Write `{match_path}`: JSON matching the schema at the end. Then run `uv run tailor plan {folder}`.

## How to judge

A screener reads the resume requirement by requirement and marks each Meets, Does not meet or
Unknown. Judge as that screener would, using only the claims below. These verdicts decide what
the resume says: an unsupported requirement is never written into it.

- `key`: `{key}`.
- `track`: the role track the resume should lead with, one of: {tracks}.
  Retrieval suggests **{suggested}**; keep it unless another track fits the job better.
- One `coverage` item per requirement below, with its `requirement_id`. Knockouts are judged
  separately (`knockouts.md`) and are not listed here.
- `support`: up to {max_support} claims, best first. Cite any claim id in this pack, not only the
  retrieved ones. `quote` is copied character for character from the claim text shown (Markdown
  removed; at least three words): the words that show the requirement is met.
  - `strong`: the claim on its own shows the requirement is met; a screener reading it would mark Meets.
  - `partial`: related or adjacent evidence; it shows part of the ask, a smaller setting, or the
    skill without the stated context.
- `verdict`: `strong` if any support is strong, `partial` if all are partial, `none` if nothing fits.
- `note`: for partial and none, what is missing, in a few words ("no production systems",
  "statistics not shown"). Optional for strong.
- Be strict:
  - Traits ("rigorous", "fast-moving") count only when a claim shows the behaviour.
    Self-description doesn't count.
  - Team results support only the part the candidate owned, so read the claim's wording.
  - A metric counts only with its conditions.
  - Years or seniority asks need dates that show them.
  - Prefer claims with stronger proof (collected > accepted > account) when two say the same thing.
- Don't infer skills the claims don't state. "Built a Python sidecar" shows Python; it does not
  show production Python at scale.

## Requirements and retrieved claims

{requirements}

## Every eligible claim

{appendix}

## Schema

```json
{schema}
```
"""


def write_pack(folder: Path, bank: Bank, spec: JobSpec, retrieval: Retrieval) -> Path:
    blocks = []
    for req in judged(spec):
        lines = [f"### {req.id} · {req.kind} · weight {req.weight} · {req.category}", "", f"> {req.quote}", ""]
        hits = retrieval.hits.get(req.id, [])
        lines += [f"- {_claim_line(bank, bank.claim(h.claim_id))}" for h in hits] or ["- (no claims retrieved)"]
        blocks.append("\n".join(lines))
    appendix, current = [], None
    for entry, claim in eligible(bank):
        if entry.id != current:
            appendix += ["", f"**{_entry_label(entry)}**" + (" (team work)" if entry.team else "")]
            current = entry.id
        appendix.append(f"- {_claim_line(bank, claim)}")
    targets = [t.target for t in bank.tables.tracks]
    pack = folder / PACK_FILE
    pack.write_text(_PACK.format(
        company=spec.company, match_path=folder / MATCH_FILE, folder=folder, key=spec.key,
        tracks=", ".join(f"`{t}`" for t in targets), suggested=retrieval.tracks[0] if retrieval.tracks else targets[0],
        max_support=MAX_SUPPORT, requirements="\n\n".join(blocks), appendix="\n".join(appendix).strip(),
        schema=json.dumps(Match.model_json_schema(), indent=2),
    ), encoding="utf-8")  # fmt: skip
    (folder / HITS_FILE).write_text(retrieval.model_dump_json(indent=2), encoding="utf-8")
    return pack
