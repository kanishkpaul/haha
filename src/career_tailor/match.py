"""Stage 2: map each JobSpec requirement to supporting claims.

Retrieval is deterministic: BM25 over claim text plus skills-bank hits, with a
boost for entries on the job's role track (record section 12.1). Knockouts are
left to `knockouts.py`.
"""

import math
import re
from collections import Counter

from .annotate import Tagger
from .bank import Bank
from .jobspec import norm
from .models import Claim, Entry, Hit, JobSpec, Requirement, Retrieval, Track
from .textutil import md_plain

TOP_K = 8
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
