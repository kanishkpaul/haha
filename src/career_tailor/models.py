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


ProofStrength = Literal["collected", "accepted", "account", "none"]  # best first


class ProofArtifact(Model):
    id: str
    title: str = ""
    type: str = ""
    url: str = ""
    access: str = ""  # as the registry words it, e.g. "public source", "private correspondence; review …"
    support: str = ""
    limits: str = ""

    @property
    def public(self) -> bool:
        return self.access.lower().startswith("public") and bool(self.url)


class ProofCard(Model):
    """A proof card from the evidence folder: what backs one career entry, and how strongly."""

    id: str  # CAREER-043
    title: str
    coverage: str = ""  # "partial" (collected sources) or "personal account only"
    collection_status: str = ""
    verification_status: str = ""  # independent claim audit, separate from source collection
    contribution: str = ""
    evidence_summary: str = ""
    primary_artifact: str = ""
    artifacts: list[ProofArtifact] = Field(default_factory=list)
    accepted_basis: str = ""  # the user's accepted closure, when no original was collected

    @property
    def strength(self) -> ProofStrength:
        if any(a.id != "ACCOUNT-MASTER" for a in self.artifacts) and self.coverage != "personal account only":
            return "collected"
        if self.accepted_basis:
            return "accepted"
        return "account"


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
    proof: ProofStrength = "none"  # strongest backing among its proof cards
    public_links: list[str] = Field(default_factory=list)  # public artifact URLs from its own proof cards


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
    public_links: list[str] = Field(default_factory=list)  # public artifact URLs from its proof cards
    same_work: list[str] = Field(default_factory=list)  # entry or row-claim ids describing the same work
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
    archive_claims: list[str] = Field(default_factory=list)  # verbatim sentences from record section 17 (megadoc)
    claim_proof: dict[str, ProofStrength] = Field(default_factory=dict)  # downgrade where a card narrows a claim
    same_work: list[str] = Field(default_factory=list)  # entries or row claims that must not appear alongside it
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


RequirementKind = Literal["basic", "preferred", "knockout", "responsibility"]
RequirementCategory = Literal[
    "skill", "experience", "education", "authorization", "location", "availability", "domain", "trait", "other"
]


class Requirement(Model):
    """One requirement, quoting the posting verbatim so it can be checked against the source."""

    id: str  # R1, R2, ...
    text: str  # the requirement in a few words, e.g. "Strong Python"
    quote: str  # exact span copied from the posting text
    kind: RequirementKind
    category: RequirementCategory = "skill"
    weight: int = Field(default=2, ge=1, le=3)  # 3 core, 2 standard, 1 minor
    keywords: list[str] = Field(default_factory=list)


class JobSpec(Model):
    key: str  # the posting's key
    company: str
    title: str  # exactly as posted
    url: str = ""
    ats: Literal["ashby", "greenhouse", "lever", "workday", "other"] = "other"
    seniority: str = ""  # e.g. "new grad", "mid", "senior", "not stated"
    location: str = ""
    workplace: str = ""  # onsite / hybrid / remote, as posted
    pages: int = Field(default=1, ge=1, le=3)  # resume length the role calls for
    requirements: list[Requirement] = Field(default_factory=list)
    keywords: dict[str, list[str]] = Field(default_factory=dict)  # canonical term -> synonyms and acronym forms
    notes: str = ""


class Candidate(Model):
    """Eligibility facts the career record doesn't hold (HAHA_DATA_DIR/candidate.yaml)."""

    location: str
    country: str
    authorized_to_work_in: list[str] = Field(default_factory=list)
    degree_level: Literal["none", "bachelor", "master", "phd"] = "bachelor"
    degree_status: Literal["in progress", "completed"] = "in progress"
    degree_expected: str = ""  # "2027-06"
    full_time_experience_years: float = 0
    earliest_full_time_start: str = ""  # "2027-07"
    open_to_relocation: bool | None = None  # None: not decided
    remote_ok: bool = True
    assumptions: list[str] = Field(default_factory=list)  # facts here that were inferred, not stated


class Knockout(Model):
    requirement_id: str
    quote: str
    status: Literal["pass", "condition", "blocker", "check"]
    reason: str


class OpportunityMatch(Model):
    """The opportunity database's record and gate decision for this posting."""

    id: str
    name: str
    organization: str
    gate: str = ""
    cls: str = ""
    score: str = ""
    condition: str = ""
    top_risk: str = ""
    mobility: str = ""
    lead_items: str = ""
    deadline: str = ""


class Hit(Model):
    """A claim retrieved for one requirement, before judgement."""

    claim_id: str
    score: float
    skills: list[str] = Field(default_factory=list)  # skills-bank terms shared with the requirement
    track: str = ""  # "lead" or "support" when the claim's entry is on the job's track


class Retrieval(Model):
    key: str
    tracks: list[str]  # role tracks (record section 12.1), best fit first
    hits: dict[str, list[Hit]]  # requirement id -> retrieved claims, best first


class Support(Model):
    claim_id: str
    strength: Literal["strong", "partial"]
    quote: str  # verbatim from the claim


class Coverage(Model):
    requirement_id: str
    verdict: Literal["strong", "partial", "none"]
    support: list[Support] = Field(default_factory=list)
    note: str = ""  # what is missing, for partial and none


class Match(Model):
    key: str
    track: str  # the role track (record section 12.1) the resume leads with
    coverage: list[Coverage] = Field(default_factory=list)


class PlanEdits(Model):
    """Your changes to a plan (plan.edits.yaml): entry or claim ids to force in or keep out."""

    pin: list[str] = Field(default_factory=list)
    ban: list[str] = Field(default_factory=list)
    pages: int | None = Field(default=None, ge=1, le=3)


class Pick(Model):
    entry_id: str
    section: str
    claim_ids: list[str]
    lines: dict[str, int] = Field(default_factory=dict)  # claim id -> line budget for its bullet
    covers: list[str] = Field(default_factory=list)  # requirement ids its claims support


class Selection(Model):
    job: str
    track: str = ""
    paper: Literal["letter", "a4"] = "letter"
    pages: int = 1
    lines_budget: float = 0
    lines_used: float = 0
    picks: list[Pick] = Field(default_factory=list)
    covered: dict[str, Literal["strong", "partial", "none"]] = Field(default_factory=dict)  # after selection
    excluded: dict[str, str] = Field(default_factory=dict)  # entry id -> why left out
    edits: PlanEdits = Field(default_factory=PlanEdits)


class TailoredBullet(Model):
    entry_id: str
    text: str
    claim_ids: list[str]


class Writing(Model):
    """The written parts of one resume (draft.json). Headings, dates, contact and education come from the bank."""

    job: str
    headline: str
    summary: str = ""
    bullets: list[TailoredBullet] = Field(default_factory=list)  # award lines included, one bullet each
    skills: dict[str, list[str]] = Field(default_factory=dict)  # group label -> skills-bank terms


class BulletReview(Model):
    claim_ids: list[str]  # identifies the bullet
    entailed: bool
    problem: str = ""  # what the bullet says that its sources don't, when not entailed


class ScreenerVerdict(Model):
    requirement_id: str
    verdict: Literal["meets", "unknown", "does not meet"]
    quote: str = ""  # the resume line that shows it, verbatim; required for "meets"


class Review(Model):
    """The entailment review and screener simulation for one draft (review.json)."""

    job: str
    bullets: list[BulletReview] = Field(default_factory=list)
    screener: list[ScreenerVerdict] = Field(default_factory=list)


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


# ---------------------------------------------------------------- assembly


class ProofTrace(Model):
    card: str  # CAREER-043
    title: str
    strength: ProofStrength
    verification: str = ""
    artifacts: list[ProofArtifact] = Field(default_factory=list)


class ClaimTrace(Model):
    claim_id: str
    text: str  # as the record words it
    record_lines: SourceRef | None = None  # None for record updates
    origin: str = ""  # the record update's source, when not from the record
    proof: ProofStrength = "none"
    cards: list[ProofTrace] = Field(default_factory=list)


class LineTrace(Model):
    """One line of the page and what it rests on."""

    section: str  # "Headline", "Education", "Experience", ..., "Skills"
    entry_id: str = ""
    text: str
    source: str = ""  # for lines not written from claims: where the wording comes from
    claims: list[ClaimTrace] = Field(default_factory=list)


class Provenance(Model):
    """Every line of one application's page traced to the record and the proof behind it (provenance.json)."""

    job: str
    record_path: str
    record_sha256: str
    bank_built_at: str
    lines: list[LineTrace] = Field(default_factory=list)
