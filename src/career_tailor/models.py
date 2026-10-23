"""Data shapes passed between stages.

Bank (stage 0): Entry groups Claims; a Claim is one atomic, citable statement
from the career record, with the numbers it contains and the conditions that
define what those numbers mean. Later stages refer to claims only by id.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- bank


class SourceRef(Model):
    """1-based, inclusive line range in the career record."""

    line_start: int
    line_end: int


class Metric(Model):
    """A number as written in a claim. `text` is the exact span, e.g. "98.9%" or "54/55"."""

    text: str
    value: float | None = None
    unit: str = ""
    kind: Literal["number", "year"] = "number"


class DateRange(Model):
    """Parsed work dates. `display` is what a resume shows, e.g. "Aug 2026 – Present"."""

    raw: str
    start: str | None = None  # "2026-08" or "2026"
    end: str | None = None  # "2026-10", "2026", or "present"
    display: str | None = None


class Repo(Model):
    name: str  # "owner/repo"
    url: str
    visibility: Literal["public", "private", "unknown"] = "unknown"
    relationship: str = ""
    summary: str = ""


ClaimKind = Literal["bullet", "lead", "row", "fact", "update"]
EntryKind = Literal["experience", "research", "engineering", "leadership", "award", "selection", "education"]


class Claim(Model):
    id: str  # "<entry id>.<kind letter><n>", e.g. "ENG-01.b3"
    entry_id: str
    kind: ClaimKind
    text: str
    label: str = ""  # table-row title or fact name
    details: str = ""  # extra table column, kept for context
    group: str = ""  # bold sub-heading the claim sat under, e.g. "Strong reusable bullets"
    metrics: list[Metric] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    status: Literal["current", "superseded"] = "current"
    superseded_by: str = ""
    show_on_resume: bool = True
    source: SourceRef | None = None  # None for record updates
    origin: str = ""  # record update source, when not from the record
    evidence_ids: list[str] = Field(default_factory=list)  # proof-card ids, e.g. CAREER-042


class Entry(Model):
    id: str  # "EXP-01", "AWD-03", "EDU-01"
    kind: EntryKind
    heading: str  # full heading text after the id
    name: str  # part before " — " (organisation or project)
    subtitle: str = ""  # part after " — " (role or tagline)
    dates: DateRange | None = None
    fields: dict[str, str] = Field(default_factory=dict)  # "**Label:** value" segments before the content
    notes: dict[str, str] = Field(default_factory=dict)  # labelled paragraphs after it; "body" for unlabelled
    stack: list[str] = Field(default_factory=list)
    repos: list[Repo] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tracks: list[str] = Field(default_factory=list)  # role-track targets this entry leads or supports
    team: bool = False
    claims: list[Claim] = Field(default_factory=list)
    source: SourceRef

    def claim(self, claim_id: str) -> Claim:
        return next(c for c in self.claims if c.id == claim_id)


class Annotation(Model):
    """Reviewed overlay for one entry. Lists replace the parsed value when non-empty."""

    entry_id: str
    name: str = ""
    subtitle: str = ""
    dates_display: str = ""
    skills: list[str] = Field(default_factory=list)
    tracks: list[str] = Field(default_factory=list)
    team: bool | None = None
    team_note: str = ""
    claim_skills: dict[str, list[str]] = Field(default_factory=dict)
    hide_claims: list[str] = Field(default_factory=list)
    extra_claims: list[str] = Field(default_factory=list)  # verbatim sentences from the entry's notes or fields
    note: str = ""


class RecordUpdate(Model):
    id: str
    date: date
    entry: str
    text: str
    source: str
    supersedes_text: str = ""
    show_on_resume: bool = True


class SkillArea(Model):
    area: str
    skills: list[str]
    examples: str = ""


class Track(Model):
    target: str
    lead: list[str]
    support: list[str]
    headline: str
    lead_entries: list[str] = Field(default_factory=list)
    support_entries: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class MetricRow(Model):
    """One row of the record's metric reference table, with the claims it qualifies."""

    work: str
    result: str
    conditions: str
    claim_ids: list[str] = Field(default_factory=list)


class Profile(Model):
    name: str
    fields: dict[str, str]  # identity table, verbatim
    headline: str = ""
    summaries: dict[str, str] = Field(default_factory=dict)


class BankManifest(Model):
    record_path: str
    record_sha256: str
    built_at: str
    entry_count: int
    claim_count: int
    proof_linked: bool = False


# ---------------------------------------------------------------- jobs onward


class Posting(Model):
    """A job posting as fetched: normalised fields plus the description as plain text."""

    key: str  # "greenhouse:anthropic:5198255008", "file:<stem>"
    ats: Literal["ashby", "greenhouse", "lever", "workday", "other"]
    org: str  # board token or file prefix
    company: str
    title: str
    url: str = ""
    api_url: str = ""
    location: str = ""
    workplace: str = ""
    employment_type: str = ""
    compensation: str = ""
    department: str = ""
    published: str = ""
    fetched_at: str
    text: str


class Requirement(Model):
    id: str
    text: str
    kind: Literal["basic", "preferred", "knockout"]
    weight: float = 1.0
    keywords: list[str] = Field(default_factory=list)


class JobSpec(Model):
    company: str
    title: str
    url: str = ""
    ats: Literal["ashby", "greenhouse", "lever", "workday", "other"] = "other"
    seniority: str = ""
    location: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    keywords: dict[str, list[str]] = Field(default_factory=dict)  # canonical term -> synonyms


class Support(Model):
    claim_id: str
    strength: Literal["strong", "partial"]
    quote: str


class Coverage(Model):
    requirement_id: str
    verdict: Literal["strong", "partial", "none"]
    support: list[Support] = Field(default_factory=list)


class Pick(Model):
    entry_id: str
    claim_ids: list[str]
    reason: str


class Selection(Model):
    job: str
    pages: int = 1
    picks: list[Pick] = Field(default_factory=list)
    excluded: dict[str, str] = Field(default_factory=dict)  # entry id -> why left out


class TailoredBullet(Model):
    entry_id: str
    text: str
    claim_ids: list[str]


class Finding(Model):
    check: str
    severity: Literal["error", "warning"]
    message: str
    bullet: str = ""


class CheckReport(Model):
    findings: list[Finding] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)
