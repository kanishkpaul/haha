"""Stage 1: turn a posting into a JobSpec.

The reading is LLM work: `write_pack` prepares a prompt pack and Claude writes
`jobspec.json`. `validate` then checks it against the posting deterministically:
every requirement must quote the posting verbatim, the title must be exact, and
requirement-like bullets the spec skipped are reported.
"""

import json
import re
from pathlib import Path

from .models import CheckReport, Finding, JobSpec, Posting
from .paths import WORK_DIR, prepared

SPEC_FILE = "jobspec.json"
PACK_FILE = f"{WORK_DIR}/jobspec.prompt.md"
CHECK_FILE = f"{WORK_DIR}/jobspec.check.md"

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
_REQUIREMENT_HEADINGS = re.compile(
    r"requirement|qualification|good fit|you have|you bring|you'll need|you will need|what we're looking for|"
    r"looking for|nice to have|bonus|strong candidates|preferred|ideal candidate|about you|skills",
    re.I,
)


def norm(text: str) -> str:
    """Comparison form: unified quotes and dashes, no bullets or bold, lowercase, single spaces."""
    text = text.translate(_QUOTES).replace("**", "")
    text = re.sub(r"(?m)^\s*(?:[-*•]|\d+\.)\s+", "", text)
    return " ".join(text.split()).lower()


def requirement_bullets(text: str) -> list[str]:
    """Bullets under requirement-like headings (Markdown headings or short all-caps/colon lines)."""
    bullets, active = [], False
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        is_heading = stripped.startswith("#") or (
            not stripped.startswith("- ") and len(stripped) < 80 and (stripped.isupper() or stripped.endswith(":"))
        )
        if is_heading:
            active = bool(_REQUIREMENT_HEADINGS.search(stripped))
        elif stripped.startswith("- ") and active:
            bullets.append(stripped[2:])
    return bullets


def validate(spec: JobSpec, posting: Posting) -> CheckReport:
    report = CheckReport()

    def add(check: str, severity: str, message: str) -> None:
        report.findings.append(Finding(check=check, severity=severity, message=message))

    body = norm(posting.text)
    if spec.key != posting.key:
        add("posting", "error", f"spec is for {spec.key}, posting is {posting.key}")
    if spec.title != posting.title:
        add("title", "error", f"title must be exactly as posted: {posting.title!r}, not {spec.title!r}")
    ids = [r.id for r in spec.requirements]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        add("ids", "error", f"duplicate requirement ids: {', '.join(dupes)}")
    if not any(r.kind in ("basic", "responsibility") for r in spec.requirements):
        add("coverage", "error", "no basic requirements or responsibilities")
    for r in spec.requirements:
        if norm(r.quote) not in body:
            add("quote", "error", f"{r.id} quote is not verbatim from the posting: {r.quote[:80]!r}")
        elif len(r.quote.split()) < 3:
            add("quote", "warning", f"{r.id} quote is very short; quote enough to identify the line")
        if r.kind == "knockout" and r.category not in ("authorization", "location", "education", "experience",
                                                       "availability"):  # fmt: skip
            add("knockout", "warning", f"{r.id} is a knockout but its category is {r.category}")
    for term, synonyms in spec.keywords.items():
        if not any(norm(t) in body for t in [term, *synonyms]):
            add("keywords", "warning", f"keyword {term!r} and its synonyms don't appear in the posting")
    quotes = [norm(r.quote) for r in spec.requirements]
    for bullet in requirement_bullets(posting.text):
        nb = norm(bullet)
        if not any(q in nb or nb in q for q in quotes):
            add("recall", "warning", f"requirement-like bullet not covered: {bullet[:90]!r}")
    return report


def render_check(report: CheckReport, spec: JobSpec | None) -> str:
    lines = ["# Job spec check", ""]
    if spec:
        kinds = {
            k: sum(r.kind == k for r in spec.requirements) for k in ("basic", "preferred", "knockout", "responsibility")
        }
        lines.append(f"{spec.title} at {spec.company}: " + ", ".join(f"{n} {k}" for k, n in kinds.items()) + ".")
        lines.append("")
    lines.append("**OK**" if report.ok else "**Fix the errors below, then rerun `tailor job --check`.**")
    lines.append("")
    lines += [f"- {f.severity}: [{f.check}] {f.message}" for f in report.findings] or ["- No findings."]
    return "\n".join(lines) + "\n"


def check_folder(folder: Path) -> tuple[CheckReport, JobSpec | None]:
    posting = Posting.model_validate_json((folder / "posting.json").read_text(encoding="utf-8"))
    spec_path = folder / SPEC_FILE
    if not spec_path.is_file():
        report = CheckReport(findings=[Finding(check="spec", severity="error",
                                               message=f"no {SPEC_FILE} yet; fill it from {PACK_FILE}")])  # fmt: skip
        return report, None
    try:
        spec = JobSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    except ValueError as err:
        return CheckReport(findings=[Finding(check="schema", severity="error", message=str(err)[:500])]), None
    report = validate(spec, posting)
    prepared(folder / CHECK_FILE).write_text(render_check(report, spec), encoding="utf-8")
    return report, spec


# ---------------------------------------------------------------- prompt pack

_PACK = """\
# Read this posting into a job spec

Write `{spec_path}`: JSON matching the schema at the end. Then run `uv run tailor job --check {folder}`.

## How to read it

The spec drives what goes on the resume, and screeners judge requirement by requirement
(Ashby marks each Meets / Does not meet / Unknown; Workday HiredScore grades on basic vs preferred).

- `key`: `{key}`. `title`: exactly `{title}`. `company`: the company's proper name.
- One requirement per distinct ask. `quote` is copied character for character from the posting
  (a fragment of a line is fine; at least a few words). `text` names it in a few words.
- `kind`:
  - `basic`: listed as required (requirements, qualifications, "you may be a good fit if", must-haves).
  - `preferred`: listed as nice to have, bonus, "strong candidates may also have", preferred.
  - `responsibility`: what the job does, when it implies a skill the resume should show.
  - `knockout`: a hard filter: work authorisation or visa, required location or on-site presence,
    a required degree, a mandatory number of years, start date or availability. Only when the
    posting states it as a requirement; "we sponsor visas" is not a knockout, it is a note.
- `category`: skill, experience, education, authorization, location, availability, domain, trait, other.
- `weight`: 3 for the role's core asks (title-defining, repeated, or first in the list), 2 for
  standard asks, 1 for minor ones. Preferred items are usually 1.
- `keywords`: the terms a recruiter would search for, as written in the posting, each with
  synonyms and both acronym forms, e.g. "RLHF": ["reinforcement learning from human feedback"].
  Only terms that appear in the posting.
- `seniority`: as stated or clearly implied ("not stated" otherwise). `pages`: 1 for industry
  roles, 2 for research-scientist roles, fellowships or residencies that ask for a CV.
- `notes`: anything that matters for applying and isn't a requirement: visa sponsorship, salary,
  "no formal background required", application questions, deadlines.
- Don't add requirements the posting doesn't state, and don't judge the candidate here.

## Posting

{posting_md}

## Schema

```json
{schema}
```
"""


def write_pack(folder: Path) -> Path:
    posting = Posting.model_validate_json((folder / "posting.json").read_text(encoding="utf-8"))
    pack = prepared(folder / PACK_FILE)
    pack.write_text(_PACK.format(
        spec_path=folder / SPEC_FILE, folder=folder, key=posting.key, title=posting.title,
        posting_md=(folder / "posting.md").read_text(encoding="utf-8").strip(),
        schema=json.dumps(JobSpec.model_json_schema(), indent=2),
    ), encoding="utf-8")  # fmt: skip
    return pack
