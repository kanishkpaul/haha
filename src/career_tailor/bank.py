"""Stage 0: compile the career record into the evidence bank (HAHA_DATA_DIR/bank/).

The bank is generated: `tailor bank` rebuilds it from the record, record_updates.yaml
and the annotation overlays, so hand edits belong in those inputs, never in bank/.

    bank/manifest.yaml     record path and SHA-256, build time, counts
    bank/profile.yaml      identity fields, headline, summaries
    bank/entries/<ID>.yaml one entry with its claims
    bank/skills.yaml       skills bank (record section 11)
    bank/tracks.yaml       role tracks resolved to entries (12.1)
    bank/policy.yaml       selling rules (12.3) and default structure (12.2)
    bank/repos.yaml        repository inventory with visibility (14)
    bank/metrics.yaml      metric reference rows with the claims they qualify (15)
    bank/notes.yaml        section notes and build problems
"""

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import record as record_mod
from .annotate import add_extra_claims, apply_annotations, load_annotations, tag
from .extract import extract, load_updates
from .models import BankManifest, Entry, MetricRow, Profile, Repo, SkillArea, Track
from .paths import Paths
from .tables import Tables, attach, attach_metrics, read_tables


@dataclass
class Bank:
    manifest: BankManifest
    profile: Profile
    entries: dict[str, Entry]
    tables: Tables
    notes: dict[str, list[str]] = field(default_factory=dict)

    def claim(self, claim_id: str):
        return self.entries[claim_id.split(".")[0]].claim(claim_id)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- provenance


def link_proof(entries: list[Entry], proof_entries: Path, record_sha: str) -> tuple[str | None, str | None]:
    """Attach proof-card ids to claims whose record lines they cover.

    A link means the claim maps to a proof card, not that the card holds proof yet.
    Returns (problem, coverage summary)."""
    if not proof_entries.is_file():
        return f"no proof cards at {proof_entries}; claims carry no evidence ids", None
    data = json.loads(proof_entries.read_text(encoding="utf-8"))
    if data.get("metadata", {}).get("master_sha256") != record_sha:
        return "proof cards were built against a different record version; evidence ids skipped", None
    coverage = {card["id"]: card.get("artifact_coverage", "unknown") for card in data["entries"]}
    spans = [
        (card["id"], ref["line_start"], ref["line_end"]) for card in data["entries"] for ref in card["master_refs"]
    ]
    account_only = linked = 0
    for entry in entries:
        for claim in entry.claims:
            if claim.source is None:
                continue
            a, b = claim.source.line_start, claim.source.line_end
            claim.evidence_ids = list(dict.fromkeys(cid for cid, s, e in spans if s <= a and b <= e))
            if claim.evidence_ids:
                linked += 1
                account_only += all(coverage[c] == "personal account only" for c in claim.evidence_ids)
    summary = (f"{linked} claims map to proof cards; {account_only} of them map only to cards still backed by the "
               f"personal account alone. Add proof in {proof_entries.parent} and rerun `tailor bank`.")  # fmt: skip
    return None, summary


# ---------------------------------------------------------------- build


def compile_bank(p: Paths) -> Bank:
    rec = record_mod.read(p.career_record)
    record_sha = sha256(p.career_record)
    profile, entries, notes = extract(rec, load_updates(p.record_updates))
    annotations = load_annotations(p.annotations)
    notes["problems"] += add_extra_claims(entries, annotations)
    tables = read_tables(rec)
    attach_metrics(entries)
    notes["info"] = attach(tables, entries)
    notes["info"] += [f"Track “{t.target}”: no entry matched “{u}” by name; annotation tracks may cover it."
                      for t in tables.tracks for u in t.unresolved]  # fmt: skip
    tag(entries, tables.skills)
    notes["problems"] += apply_annotations(entries, annotations)
    problem, coverage = link_proof(entries, p.proof_entries, record_sha)
    notes["problems"] += [problem] if problem else []
    notes["info"] += [coverage] if coverage else []
    notes["skills"] = tables.skill_notes
    notes["metrics"] = tables.metric_notes
    manifest = BankManifest(
        record_path=str(p.career_record),
        record_sha256=record_sha,
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
        entry_count=len(entries),
        claim_count=sum(len(e.claims) for e in entries),
        proof_linked=any(c.evidence_ids for e in entries for c in e.claims),
    )
    return Bank(manifest, profile, {e.id: e for e in entries}, tables, notes)


def _dump(path: Path, data) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10_000), encoding="utf-8")


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _models(items) -> list[dict]:
    return [i.model_dump(mode="json", exclude_defaults=True) for i in items]


def save(bank: Bank, directory: Path) -> None:
    entries_dir = directory / "entries"
    if entries_dir.exists():
        shutil.rmtree(entries_dir)  # generated output; stale entries must not survive a rebuild
    entries_dir.mkdir(parents=True)
    for entry in bank.entries.values():
        _dump(entries_dir / f"{entry.id}.yaml", entry.model_dump(mode="json", exclude_defaults=True))
    t = bank.tables
    _dump(directory / "profile.yaml", bank.profile.model_dump(mode="json"))
    _dump(directory / "skills.yaml", _models(t.skills))
    _dump(directory / "tracks.yaml", _models(t.tracks))
    _dump(directory / "policy.yaml", {"rules": t.rules, "structure": t.structure})
    _dump(directory / "repos.yaml", _models(t.repos))
    _dump(directory / "metrics.yaml", _models(t.metrics))
    _dump(directory / "notes.yaml", bank.notes)
    _dump(directory / "manifest.yaml", bank.manifest.model_dump(mode="json"))  # last: marks a complete build


def load(directory: Path) -> Bank:
    if not (directory / "manifest.yaml").is_file():
        raise FileNotFoundError(f"no bank at {directory}; run `uv run tailor bank`")
    policy = _load(directory / "policy.yaml")
    notes = _load(directory / "notes.yaml") or {}
    tables = Tables(
        skills=[SkillArea.model_validate(x) for x in _load(directory / "skills.yaml")],
        skill_notes=notes.get("skills", []),
        tracks=[Track.model_validate(x) for x in _load(directory / "tracks.yaml")],
        rules=policy["rules"],
        structure=policy["structure"],
        repos=[Repo.model_validate(x) for x in _load(directory / "repos.yaml")],
        metrics=[MetricRow.model_validate(x) for x in _load(directory / "metrics.yaml")],
        metric_notes=notes.get("metrics", []),
    )
    entries = [Entry.model_validate(_load(f)) for f in sorted((directory / "entries").glob("*.yaml"))]
    order = {"EDU": 0, "EXP": 1, "RES": 2, "ENG": 3, "LEAD": 4, "AWD": 5, "SEL": 6}
    entries.sort(key=lambda e: (order.get(e.id.split("-")[0], 9), e.id))
    return Bank(
        manifest=BankManifest.model_validate(_load(directory / "manifest.yaml")),
        profile=Profile.model_validate(_load(directory / "profile.yaml")),
        entries={e.id: e for e in entries},
        tables=tables,
        notes=notes,
    )


def stale_reason(bank: Bank, p: Paths) -> str | None:
    """Why the bank no longer reflects its inputs, or None if it is current."""
    if not p.career_record.is_file():
        return f"career record missing at {p.career_record}"
    if sha256(p.career_record) != bank.manifest.record_sha256:
        return "the career record changed since the bank was built; run `uv run tailor bank`"
    built = datetime.fromisoformat(bank.manifest.built_at).timestamp()
    inputs = [p.record_updates, p.proof_entries, *sorted(p.annotations.glob("*.yaml"))]
    if any(f.is_file() and f.stat().st_mtime > built for f in inputs):
        return "record updates, proof cards or annotations changed since the bank was built; run `uv run tailor bank`"
    return None


# ---------------------------------------------------------------- report


def report(bank: Bank) -> str:
    entries = list(bank.entries.values())
    claims = [c for e in entries for c in e.claims]
    count = {k: sum(c.kind in k for c in claims) for k in (("bullet",), ("lead",), ("row",), ("fact", "update"))}
    m = bank.manifest
    lines = [
        "# Evidence bank report",
        "",
        f"Built {m.built_at} from `{m.record_path}` (SHA-256 `{m.record_sha256[:12]}…`).",
        f"**{len(entries)} entries, {len(claims)} claims** ({count['bullet',]} bullets, "
        f"{count['lead',]} lead statements, {count['row',]} table rows, {count['fact', 'update']} facts). "
        f"{sum(bool(c.evidence_ids) for c in claims)} claims map to a proof card (mapping, not verification); "
        f"{sum(bool(c.conditions) for c in claims)} carry metric conditions.",
        "",
        "## Needs attention",
        "",
    ]  # fmt: skip
    issues = []
    for e in entries:
        current = [c for c in e.claims if c.status == "current"]
        if not current:
            issues.append(f"- **{e.id}** has no claims; its content is in notes only ({', '.join(e.notes) or 'none'}).")
        has_content = any(c.kind in ("bullet", "lead") for c in current)
        if e.dates is not None and e.dates.display is None:
            issues.append(f"- **{e.id}** dates not parsed: {e.dates.raw}")
        elif e.dates is None and has_content and e.kind in ("experience", "research", "engineering"):
            issues.append(f"- **{e.id}** has no dates.")
        if not e.skills and e.kind in ("experience", "research", "engineering"):
            issues.append(f"- **{e.id}** has no skills yet (annotation pass).")
    issues += [f"- {p}" for p in bank.notes.get("problems", [])]
    lines += issues or ["Nothing."]
    if info := bank.notes.get("info", []):
        lines += ["", "## For information", ""] + [f"- {i}" for i in info]
    lines += [
        "",
        "## Entries",
        "",
        "| ID | Name | Dates | Claims | Skills | Tracks | Team | Repos |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    for e in entries:
        dates = e.dates.display if e.dates and e.dates.display else "—"
        repos = ", ".join(f"{r.name.split('/')[-1]} ({r.visibility})" for r in e.repos)
        lines.append(f"| {e.id} | {e.name} | {dates} | {len(e.claims)} | {len(e.skills)} | {len(e.tracks)} | "
                     f"{'yes' if e.team else ''} | {repos} |")  # fmt: skip
    superseded = [c for c in claims if c.status == "superseded"]
    if superseded:
        lines += ["", "## Superseded by record updates", ""]
        lines += [f"- {c.id} → {c.superseded_by}" for c in superseded]
    return "\n".join(lines) + "\n"
