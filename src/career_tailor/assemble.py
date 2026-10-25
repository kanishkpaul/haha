"""Stage 8: assemble the application folder.

`provenance` traces every line of the page to the claims it was written from,
their line ranges in the career record (or the record update that supersedes
them) and the proof cards and artifacts behind each claim. `check` confirms the
trace is complete: every bullet has claims, every claim is on the page's plan,
and every line range sits inside the record the bank was built from.

`tailor run` stops at two gates, the plan and the final check. An approval is
stored in `approvals.json` with a hash of the files it covers, so a changed
plan or page needs approving again. `status` says where an application stands
and what to do next.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import selection as sel_mod
from .bank import Bank
from .models import ClaimTrace, Finding, JobSpec, LineTrace, ProofTrace, Provenance, Selection, Writing
from .textutil import md_plain
from .write import education_lines

PROVENANCE_FILE = "provenance.json"
APPROVALS_FILE = "approvals.json"
GATES = {
    # the plan's inputs, not selection.json: the fit loop may trim the selection, and the final gate covers that
    "plan": ("jobspec.json", "match.json", "plan.edits.yaml"),
    "final": ("selection.json", "draft.json", "review.json", "check_report.md"),
}


def claim_trace(bank: Bank, claim_id: str) -> ClaimTrace:
    claim = bank.claim(claim_id)
    cards = [
        ProofTrace(card=card.id, title=card.title, strength=card.strength, verification=card.verification_status,
                   artifacts=card.artifacts)
        for card in (bank.proof.get(e) for e in claim.evidence_ids) if card
    ]  # fmt: skip
    return ClaimTrace(claim_id=claim.id, text=claim.text, record_lines=claim.source, origin=claim.origin,
                      proof=claim.proof, cards=cards)  # fmt: skip


def _headline_source(bank: Bank, spec: JobSpec, headline: str) -> str:
    if headline == spec.title:
        return "posting title"
    track = next((t.target for t in bank.tables.tracks if t.headline == headline), "")
    return f"record track headline ({track})" if track else "record profile headline"


def provenance(bank: Bank, spec: JobSpec, sel: Selection, w: Writing) -> Provenance:
    lines = [LineTrace(section="Headline", text=w.headline, source=_headline_source(bank, spec, w.headline))]
    if w.summary:
        lines.append(LineTrace(section="Summary", text=w.summary, source="written from the page's claims"))
    if edu := bank.entries.get("EDU-01"):
        current = [c.id for c in edu.claims if c.status == "current" and c.show_on_resume]
        for text in education_lines(bank):
            lines.append(LineTrace(section="Education", entry_id=edu.id, text=md_plain(text),
                                   source=f"{edu.id} heading and current claims",
                                   claims=[claim_trace(bank, c) for c in current
                                           if md_plain(bank.claim(c).text).split(";")[-1].strip().lower()
                                           in md_plain(text).lower()]))  # fmt: skip
    for pick in sel.picks:
        entry = bank.entries[pick.entry_id]
        for b in (x for x in w.bullets if x.entry_id == entry.id):
            lines.append(LineTrace(section=pick.section, entry_id=entry.id, text=b.text,
                                   source="award line" if entry.kind in sel_mod.ONE_LINE_KINDS else "",
                                   claims=[claim_trace(bank, c) for c in b.claim_ids]))  # fmt: skip
    on_page = {c for b in w.bullets for c in b.claim_ids}
    for group, terms in w.skills.items():
        for term in terms:
            tagged = sorted(c for c in on_page if term in bank.claim(c).skills)
            entries = [p.entry_id for p in sel.picks if term in bank.entries[p.entry_id].skills
                       and bank.entries[p.entry_id].kind not in sel_mod.ONE_LINE_KINDS]  # fmt: skip
            area = next((a.area for a in bank.tables.skills if term in a.skills), "")
            source = f"record skills bank ({area})" if area else "record skills bank"
            if entries:
                source += f"; tagged on {', '.join(entries)}"
            lines.append(LineTrace(section="Skills", text=f"{group}: {term}", source=source,
                                   claims=[claim_trace(bank, c) for c in tagged]))  # fmt: skip
    return Provenance(job=spec.key, record_path=bank.manifest.record_path, record_sha256=bank.manifest.record_sha256,
                      bank_built_at=bank.manifest.built_at, lines=lines)  # fmt: skip


def check(prov: Provenance, sel: Selection, record: Path | None = None) -> list[Finding]:
    """Every bullet traced to planned claims; every record line range inside the record."""
    findings = []

    def error(message: str, line: LineTrace) -> None:
        findings.append(Finding(check="provenance", severity="error", message=message, bullet=line.text[:60]))

    planned = {c for p in sel.picks for c in p.claim_ids}
    total = len(record.read_text(encoding="utf-8").splitlines()) if record and record.is_file() else None
    for line in prov.lines:
        if line.section not in ("Headline", "Summary", "Education", "Skills") and not line.claims:
            error("line has no claims", line)
        for c in line.claims:
            span = c.record_lines
            if line.section not in ("Education", "Skills") and c.claim_id not in planned:
                error(f"{c.claim_id} isn't in the plan", line)
            if span is None and not c.origin:
                error(f"{c.claim_id} has neither record lines nor an update source", line)
            if span and total and not 1 <= span.line_start <= span.line_end <= total:
                error(f"{c.claim_id} record lines {span.line_start}–{span.line_end} fall outside the record "
                      f"({total} lines)", line)  # fmt: skip
    return findings


def gate_hash(folder: Path, gate: str) -> str:
    digest = hashlib.sha256()
    for name in GATES[gate]:
        path = folder / name
        digest.update(name.encode() + b"\0" + (path.read_bytes() if path.is_file() else b"<missing>") + b"\0")
    return digest.hexdigest()


def approvals(folder: Path) -> dict[str, dict]:
    path = folder / APPROVALS_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def approved(folder: Path, gate: str) -> bool:
    """True if the gate was approved and none of the files it covers has changed since."""
    record = approvals(folder).get(gate)
    return bool(record) and record.get("hash") == gate_hash(folder, gate)


def approve(folder: Path, gate: str, now: datetime | None = None) -> dict:
    record = {"hash": gate_hash(folder, gate), "at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
              "files": [n for n in GATES[gate] if (folder / n).is_file()]}  # fmt: skip
    data = approvals(folder) | {gate: record}
    if gate == "plan":
        data.pop("final", None)  # a new plan means a new page
    (folder / APPROVALS_FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return record


STAGES = [  # file that ends the stage, stage name, what to do while it's missing
    ("jobspec.json", "job spec", "fill jobspec.json from work/jobspec.prompt.md"),
    ("match.json", "match", "fill match.json from work/match.prompt.md"),
    ("selection.json", "plan", "tailor run {name}"),
    ("plan", "gate 1", "review plan.md and gap_report.md, then tailor run {name} --approve"),
    ("draft.json", "write", "fill draft.json from work/write.prompt.md"),
    ("review.json", "review", "fill review.json from work/verify.prompt.md"),
    ("final", "gate 2", "review check_report.md and the PDF, then tailor run {name} --approve"),
]


@dataclass
class Status:
    folder: Path
    stage: str  # a stage name from STAGES, or "ready"
    next: str
    grade: str = ""
    ats_score: float | None = None


def status(folder: Path, name: str = "") -> Status:
    """Where an application stands: the first stage whose file or approval is missing, and what to do.
    `name` is how the next command should refer to the folder (a unique prefix reads better)."""
    pending = next(((stage, todo) for done, stage, todo in STAGES
                    if not (approved(folder, done) if done in GATES else (folder / done).is_file())), None)  # fmt: skip
    stage, todo = pending or ("ready", "send " + next((p.name for p in folder.glob("*.pdf")), "the PDF"))
    report = folder / "check_report.md"
    grade = re.search(r"screener grade: \*\*(\w)\*\*", report.read_text(encoding="utf-8")) if report.is_file() else None
    score = folder / "ats_score.json"
    ats_score = json.loads(score.read_text(encoding="utf-8")).get("overall_score") if score.is_file() else None
    return Status(folder, stage, todo.format(name=name or folder.name), grade.group(1) if grade else "", ats_score)


def short_names(folders: list[Path]) -> dict[Path, str]:
    """The fewest leading words of each folder name that no other folder name starts with."""
    out = {}
    for f in folders:
        words = f.name.split("-")
        prefixes = ["-".join(words[:n]) for n in range(1, len(words) + 1)]
        out[f] = next((x for x in prefixes if not any(g != f and g.name.startswith(x) for g in folders)), f.name)
    return out
