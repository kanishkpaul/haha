"""Knockout pre-check: judge a job spec's hard filters against the candidate's eligibility facts.

Deterministic rules, deliberately conservative: anything the rules can't settle is "check".
Statuses: pass, condition (possible with an action or a sponsor), blocker, check.
"""

import re
from pathlib import Path

import yaml

from .models import Candidate, JobSpec, Knockout, OpportunityMatch, Posting

KNOCKOUT_FILE = "knockouts.md"

_SPONSOR_YES = re.compile(r"(we|will|can|do)\s+(?:\w+\s+){0,3}sponsor|visa sponsorship(?! is not| not)|sponsor visas|"
                          r"relocation (?:and visa )?support", re.I)  # fmt: skip
_SPONSOR_NO = re.compile(r"(unable|not able|cannot|can't|do not|don't|won't|will not)\s+(?:\w+\s+){0,3}sponsor|"
                         r"no visa sponsorship|without (?:the need for )?(?:visa )?sponsorship|"
                         r"sponsorship is not (?:available|offered)", re.I)  # fmt: skip
_COUNTRIES = {
    "US": r"\b(us|u\.s\.|usa|united states|america)\b", "UK": r"\b(uk|u\.k\.|united kingdom|britain)\b",
    "EU": r"\b(eu|european union|europe)\b", "Canada": r"\bcanada\b", "India": r"\bindia\b",
    "Singapore": r"\bsingapore\b", "Australia": r"\baustralia\b",
}  # fmt: skip
_YEARS = re.compile(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?(?:years|yrs)", re.I)
_EQUIVALENT = re.compile(r"equivalent|or similar|or related|self-taught|no formal|don't require|do not require", re.I)


def load_candidate(path: Path) -> Candidate | None:
    if not path.is_file():
        return None
    return Candidate.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def sponsorship(text: str) -> str:
    """What the posting says about visa sponsorship: "offered", "not offered", or "not stated"."""
    if _SPONSOR_NO.search(text):
        return "not offered"
    if _SPONSOR_YES.search(text):
        return "offered"
    return "not stated"


def _countries(text: str) -> list[str]:
    return [name for name, pattern in _COUNTRIES.items() if re.search(pattern, text, re.I)]


def judge(quote: str, category: str, candidate: Candidate, visa: str) -> tuple[str, str]:
    q = quote.lower()
    if category == "authorization":
        places = _countries(quote)
        where = ", ".join(places) or "the job country"
        if places and all(p in candidate.authorized_to_work_in for p in places):
            return "pass", f"authorised in {where}"
        if visa == "not offered" or "without sponsorship" in q or "without visa" in q:
            return "blocker", f"needs work authorisation in {where} and the posting won't sponsor"
        if visa == "offered":
            return "condition", f"needs a visa for {where}; the posting says it sponsors (not guaranteed)"
        return "check", f"needs work authorisation in {where}; sponsorship not stated"
    if category == "location":
        if "remote" in q and candidate.remote_ok and not re.search(r"remote[^.]*\b(us|uk|eu|within)\b", q):
            return "pass", "remote allowed"
        if candidate.country.lower() in q or candidate.location.split(",")[0].lower() in q:
            return "pass", f"located in {candidate.location}"
        if candidate.open_to_relocation is False:
            return "blocker", f"requires presence away from {candidate.location}; you're not relocating"
        reloc = "relocation undecided" if candidate.open_to_relocation is None else "open to relocation"
        visa_note = {
            "offered": "sponsors visas",
            "not offered": "won't sponsor",
            "not stated": "sponsorship not stated",
        }[visa]
        status = "blocker" if visa == "not offered" else "condition"
        return status, f"requires on-site presence away from {candidate.location} ({reloc}; posting {visa_note})"
    if category == "education":
        if re.search(r"\b(ph\.?d|doctor)", q) and candidate.degree_level != "phd":
            if _EQUIVALENT.search(q):
                return "condition", "asks for a PhD or equivalent experience; argue equivalence from research work"
            return "blocker", "requires a PhD"
        if re.search(r"\b(master|m\.?s\.?c?\b|m\.?tech|graduate degree)", q) and candidate.degree_level in (
            "none",
            "bachelor",
        ):
            if _EQUIVALENT.search(q):
                return "condition", "asks for a master's or equivalent experience"
            return "blocker", "requires a master's degree"
        if candidate.degree_status == "in progress":
            return "condition", f"bachelor's in progress, expected {candidate.degree_expected}"
        return "pass", "degree requirement met"
    if category == "experience":
        if m := _YEARS.search(q):
            years = int(m.group(1))
            if years <= candidate.full_time_experience_years:
                return "pass", f"{years} years required"
            if _EQUIVALENT.search(q) or "research" in q:
                return "condition", f"asks for {years}+ years; research collaborations may count, argue it explicitly"
            return "blocker", f"requires {years}+ years; you have {candidate.full_time_experience_years:g} full-time"
        return "check", "experience requirement without a number of years"
    if category == "availability":
        return (
            "condition",
            f"check the start date against your earliest full-time start ({candidate.earliest_full_time_start})",
        )
    return "check", "not a category the rules judge"


def evaluate(spec: JobSpec, posting: Posting, candidate: Candidate) -> list[Knockout]:
    visa = sponsorship(posting.text)
    return [Knockout(requirement_id=r.id, quote=r.quote, status=status, reason=reason)
            for r in spec.requirements if r.kind == "knockout"
            for status, reason in [judge(r.quote, r.category, candidate, visa)]]  # fmt: skip


def render(spec: JobSpec, posting: Posting, results: list[Knockout], candidate: Candidate | None,
           match: OpportunityMatch | None) -> str:  # fmt: skip
    worst = next((s for s in ("blocker", "condition", "check") if any(k.status == s for k in results)), "pass")
    verdict = {"blocker": "**Blocked** by a hard requirement.", "condition": "**Possible, with conditions.**",
               "check": "**Needs a check** before applying.", "pass": "**No knockouts found.**"}[worst]  # fmt: skip
    lines = [f"# Knockouts: {spec.title} at {spec.company}", "", verdict, "",
             f"Visa sponsorship in the posting: **{sponsorship(posting.text)}**.", ""]  # fmt: skip
    if results:
        lines += ["| Req | Status | Why | Posting says |", "|---|---|---|---|"]
        lines += [f"| {k.requirement_id} | {k.status} | {k.reason} | “{k.quote}” |" for k in results]
    else:
        lines.append("The spec lists no knockout requirements.")
    if match:
        lines += ["", "## Opportunity database", "",
                  f"{match.id} · {match.name} · gate **{match.gate or 'n/a'}**, class **{match.cls or 'n/a'}**"
                  + (f", score {match.score}" if match.score else "")]  # fmt: skip
        details = {"Condition": match.condition, "Top risk": match.top_risk, "Mobility": match.mobility,
                   "Lead with": match.lead_items, "Deadline": match.deadline}  # fmt: skip
        for label, value in details.items():
            if value:
                lines.append(f"- **{label}:** {value}")
    else:
        lines += ["", "Not found in the opportunity database."]
    if candidate and candidate.assumptions:
        lines += ["", "## Assumptions in candidate.yaml", ""] + [f"- {a}" for a in candidate.assumptions]
    return "\n".join(lines) + "\n"
