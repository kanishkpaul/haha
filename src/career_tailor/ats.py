"""Stage 7: wrap the Resume-Matcher CLI (bin/rm) for scoring and parse checks.

`call` runs one tool and returns its JSON; `parse_check` asks RM to parse-check
a PDF or DOCX the way an ATS would read it, and turns its failed checks into
findings; `score` gives RM's keyword match and ATS score for the page, passing
the job keywords and resume data inline. Nothing here writes to RM's database.
"""

import json
import re
import subprocess
from pathlib import Path

from . import selection as sel_mod
from .bank import Bank
from .models import Finding, JobSpec, Selection, Writing
from .paths import RM_CLI
from .textutil import md_links, md_plain
from .write import REQUIRED_KINDS, education_lines, posting_terms


class RMError(RuntimeError):
    pass


def call(tool: str, args: dict, timeout: float = 120) -> dict:
    done = subprocess.run([str(RM_CLI), tool, json.dumps(args)], capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RMError(f"bin/rm {tool} failed: {(done.stderr or done.stdout).strip()[-800:]}")
    return json.loads(done.stdout)


def available() -> bool:
    return RM_CLI.exists()


def parse_check(path: Path) -> tuple[dict, list[Finding]]:
    """RM's parse check of a rendered file, with failed or warned checks as findings."""
    result = call("parse_check_file", {"file_path": str(path.resolve())})
    findings = []
    for check in result.get("checks", []):
        if check.get("status") in ("pass", "skip", "not_applicable"):
            continue
        severity = "error" if check.get("severity") == "high" and check.get("status") == "fail" else "warning"
        params = ", ".join(f"{k}={v}" for k, v in (check.get("params") or {}).items())
        findings.append(Finding(check="ats", severity=severity,
                                message=f"{path.suffix[1:].upper()} {check.get('id')}: {check.get('status')}"
                                        + (f" ({params})" if params else "")))  # fmt: skip
    return result, findings


SCORE_FILE = "ats_score.json"


def job_keywords(spec: JobSpec) -> dict:
    """RM's job keywords from the spec. RM scores the required, preferred and other terms literally."""
    terms = posting_terms(spec)
    return {
        "company": spec.company,
        "role": spec.title,
        "required_skills": terms["required"],
        "preferred_skills": terms["preferred"],
        "keywords": terms["other"],
        "experience_requirements": [r.text for r in spec.requirements
                                    if r.kind in REQUIRED_KINDS and r.category == "experience"],
        "education_requirements": [r.text for r in spec.requirements if r.category == "education"],
        "key_responsibilities": [r.text for r in spec.requirements if r.kind == "responsibility"],
        "seniority_level": spec.seniority or None,
    }  # fmt: skip


def resume_data(bank: Bank, sel: Selection, w: Writing) -> dict:
    """The page as RM's resume data: the same headline, entries, bullets, awards and skills that get rendered."""
    fields = bank.profile.fields
    bullets: dict[str, list[str]] = {}
    for b in w.bullets:
        bullets.setdefault(b.entry_id, []).append(md_plain(b.text))
    work, projects, awards = [], [], []
    for n, pick in enumerate(sel.picks, 1):
        entry = bank.entries[pick.entry_id]
        dates = entry.dates.display if entry.dates and entry.dates.display else ""
        if entry.kind in sel_mod.ONE_LINE_KINDS:
            awards += bullets.get(entry.id, [])
        elif entry.kind == "experience":
            work.append({"id": n, "title": md_plain(entry.subtitle), "company": md_plain(entry.name), "years": dates,
                         "description": bullets.get(entry.id, [])})  # fmt: skip
        else:
            projects.append({"id": n, "name": md_plain(entry.name), "role": md_plain(entry.subtitle), "years": dates,
                             "description": bullets.get(entry.id, [])})  # fmt: skip
    education = []
    if edu := bank.entries.get("EDU-01"):
        lines = education_lines(bank)
        education.append({"id": 1, "institution": md_plain(edu.name), "degree": md_plain(edu.subtitle.split(" — ")[0]),
                          "years": edu.dates.display if edu.dates and edu.dates.display else "",
                          "description": md_plain(lines[1]) if len(lines) > 1 else None})  # fmt: skip

    def link(key: str) -> str | None:
        value = fields.get(key, "")
        return (md_links(value) or [md_plain(value)])[0] or None

    return {
        "personalInfo": {"name": bank.profile.name, "title": md_plain(w.headline),
                         "email": md_plain(fields.get("Primary email", "")), "phone": md_plain(fields.get("Phone", "")),
                         "location": md_plain(fields.get("Location", "")), "website": link("Website"),
                         "linkedin": link("LinkedIn"), "github": link("GitHub")},
        "summary": md_plain(w.summary),
        "workExperience": work,
        "education": education,
        "personalProjects": projects,
        "additional": {"technicalSkills": [t for terms in w.skills.values() for t in terms], "awards": awards},
    }  # fmt: skip


def score(spec: JobSpec, bank: Bank, sel: Selection, w: Writing) -> tuple[dict, list[Finding]]:
    """RM's keyword match and ATS score for the page. Required posting terms missing from it are warnings:
    an ATS search matches the posting's words, so a synonym on the page doesn't count."""
    keywords, data = job_keywords(spec), resume_data(bank, sel, w)
    result = call("score_resume", {"resume_data": data, "keywords": keywords})
    text = " ".join(_text(data)).lower()
    findings = []
    for term in keywords["required_skills"]:
        if not re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text):
            alt = [s for s in spec.keywords.get(term, []) if re.search(rf"(?<!\w){re.escape(s.lower())}(?!\w)", text)]
            hint = f"; the page says {', '.join(repr(a) for a in alt)}" if alt else ""
            findings.append(Finding(check="ats", severity="warning",
                                    message=f"required posting term {term!r} isn't on the page{hint}"))  # fmt: skip
    return {"keywords": keywords, **result}, findings


def _text(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _text(v)]
    if isinstance(value, list):
        return [t for v in value for t in _text(v)]
    return []


def score_lines(result: dict) -> list[str]:
    subs = result.get("sub_scores") or {}
    parts = ", ".join(f"{k.replace('_', ' ')} {v:g}" for k, v in subs.items() if v is not None)
    lines = [f"Resume-Matcher ATS score: {result.get('overall_score')}/100 ({parts})"]
    if missing := result.get("missing_keywords"):
        lines.append(f"Posting terms not on the page: {', '.join(missing)}")
    return lines
