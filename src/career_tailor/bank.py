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
from .annotate import ARCHIVE_SECTION, add_extra_claims, apply_annotations, apply_proof_overrides, load_annotations, tag
from .extract import extract, load_updates
from .models import BankManifest, Entry, MetricRow, Profile, ProofArtifact, ProofCard, Repo, SkillArea, Track
from .paths import Paths
from .tables import Tables, attach, attach_metrics, read_tables


@dataclass
class Bank:
    manifest: BankManifest
    profile: Profile
    entries: dict[str, Entry]
    tables: Tables
    notes: dict[str, list[str]] = field(default_factory=dict)
    proof: dict[str, ProofCard] = field(default_factory=dict)

    def claim(self, claim_id: str):
        return self.entries[claim_id.split(".")[0]].claim(claim_id)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- provenance


_STRENGTH_ORDER = ["collected", "accepted", "account", "none"]


def read_proof_cards(proof_dir: Path) -> dict[str, ProofCard]:
    """Proof cards with their artifacts, from entries.json and artifacts.json."""
    cards = json.loads((proof_dir / "entries.json").read_text(encoding="utf-8"))["entries"]
    registry_file = proof_dir / "artifacts.json"
    registry = json.loads(registry_file.read_text(encoding="utf-8"))["artifacts"] if registry_file.is_file() else []
    artifacts = {a["id"]: a for a in registry}
    fields = set(ProofArtifact.model_fields)
    out = {}
    for card in cards:
        ids = [card.get("primary_artifact_id"), *card.get("artifact_ids", []), *card.get("account_artifact_ids", [])]
        resolution = card.get("recovery_resolution") or {}
        out[card["id"]] = ProofCard(
            id=card["id"], title=card.get("title", ""), coverage=card.get("artifact_coverage", ""),
            collection_status=card.get("collection_status", ""),
            verification_status=card.get("verification_status", ""),
            contribution=card.get("contribution") or "", evidence_summary=card.get("evidence_summary") or "",
            primary_artifact=card.get("primary_artifact_id") or "",
            artifacts=[ProofArtifact(**{k: str(v) for k, v in artifacts[i].items() if k in fields and v is not None})
                       for i in dict.fromkeys(i for i in ids if i) if i in artifacts],
            accepted_basis=resolution.get("basis", "") if resolution.get("status") == "done" else "",
        )  # fmt: skip
    return out


def covers(spans: list[tuple[int, int]], a: int, b: int) -> bool:
    """Does a card with these record spans back the claim on lines a–b?

    Cards often cite a whole catalog card plus the one row they are about. A span counts only
    if the card cites nothing narrower inside it, or the narrower citation is the claim itself."""
    for s, e in spans:
        if not (s <= a and b <= e):
            continue
        nested = [(x, y) for x, y in spans if s <= x and y <= e and (x, y) != (s, e)]
        if not nested or any(x <= a and b <= y for x, y in nested):
            return True
    return False


def link_proof(
    entries: list[Entry], proof_dir: Path, record_sha: str
) -> tuple[dict[str, ProofCard], str | None, str | None]:
    """Attach proof cards to claims whose record lines they cover, with the strength of that backing.

    A link means the claim maps to a proof card; the cards' own independent claim audit is separate.
    Returns (cards, problem, coverage summary)."""
    entries_file = proof_dir / "entries.json"
    if not entries_file.is_file():
        return {}, f"no proof cards at {entries_file}; claims carry no evidence ids", None
    data = json.loads(entries_file.read_text(encoding="utf-8"))
    if data.get("metadata", {}).get("master_sha256") != record_sha:
        return {}, "proof cards were built against a different record version; evidence ids skipped", None
    cards = read_proof_cards(proof_dir)
    refs = {card["id"]: [(r["line_start"], r["line_end"]) for r in card["master_refs"]] for card in data["entries"]}
    for entry in entries:
        links: list[str] = []
        for claim in entry.claims:
            if claim.source is None:
                continue
            a, b = claim.source.line_start, claim.source.line_end
            claim.evidence_ids = [cid for cid, spans in refs.items() if covers(spans, a, b)]
            strengths = [cards[c].strength for c in claim.evidence_ids if c in cards] or ["none"]
            claim.proof = min(strengths, key=_STRENGTH_ORDER.index)
            claim.public_links = list(dict.fromkeys(
                art.url for c in claim.evidence_ids if c in cards for art in cards[c].artifacts if art.public
            ))  # fmt: skip
            links += claim.public_links
        entry.public_links = list(dict.fromkeys(links))
    return cards, None, proof_summary(entries, cards)


def proof_summary(entries: list[Entry], cards: dict[str, ProofCard]) -> str:
    sourced = [c for e in entries for c in e.claims if c.source is not None]
    counts = {s: sum(c.proof == s for c in sourced) for s in _STRENGTH_ORDER}
    audited = bool(cards) and all(card.verification_status != "claim audit incomplete" for card in cards.values())
    return (f"Proof backing for {len(sourced)} record claims: {counts['collected']} collected sources, "
            f"{counts['accepted']} user-accepted closures, {counts['account']} personal account only, "
            f"{counts['none']} not mapped to a card. Independent claim audit: "
            f"{'done' if audited else 'not yet done (source collection only)'}.")  # fmt: skip


# ---------------------------------------------------------------- build


def compile_bank(p: Paths) -> Bank:
    rec = record_mod.read(p.career_record)
    record_sha = sha256(p.career_record)
    profile, entries, notes = extract(rec, load_updates(p.record_updates))
    annotations = load_annotations(p.annotations)
    archive = rec.section(ARCHIVE_SECTION)
    archive_lines = [(no, rec.line(no)) for no in range(archive.line, archive.end + 1)] if archive else []
    notes["problems"] += add_extra_claims(entries, annotations, archive_lines)
    tables = read_tables(rec)
    attach_metrics(entries)
    notes["info"] = attach(tables, entries)
    notes["info"] += [f"Track “{t.target}”: no entry matched “{u}” by name; annotation tracks may cover it."
                      for t in tables.tracks for u in t.unresolved]  # fmt: skip
    tag(entries, tables.skills)
    notes["problems"] += apply_annotations(entries, annotations)
    proof, problem, coverage = link_proof(entries, p.proof_dir, record_sha)
    notes["problems"] += [problem] if problem else []
    if proof:
        notes["problems"] += apply_proof_overrides(entries, annotations)
        coverage = proof_summary(entries, proof)
    notes["info"] += [coverage] if coverage else []
    notes["skills"] = tables.skill_notes
    notes["metrics"] = tables.metric_notes
    manifest = BankManifest(
        record_path=str(p.career_record),
        record_sha256=record_sha,
        built_at=datetime.now(UTC).isoformat(timespec="microseconds"),  # finer than file mtimes
        entry_count=len(entries),
        claim_count=sum(len(e.claims) for e in entries),
        proof_linked=any(c.evidence_ids for e in entries for c in e.claims),
    )
    return Bank(manifest, profile, {e.id: e for e in entries}, tables, notes, proof)


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
    _dump(directory / "proof.yaml", _models(bank.proof.values()))
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
    proof_file = directory / "proof.yaml"
    proof = [ProofCard.model_validate(x) for x in (_load(proof_file) or [])] if proof_file.is_file() else []
    return Bank(
        proof={c.id: c for c in proof},
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
    inputs = [p.record_updates, p.proof_entries, p.proof_dir / "artifacts.json", *sorted(p.annotations.glob("*.yaml"))]
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
        "| ID | Name | Dates | Claims | Skills | Tracks | Team | Proof | Repos |",
        "|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for e in entries:
        dates = e.dates.display if e.dates and e.dates.display else "—"
        repos = ", ".join(f"{r.name.split('/')[-1]} ({r.visibility})" for r in e.repos)
        best = min((c.proof for c in e.claims), key=_STRENGTH_ORDER.index, default="none")
        lines.append(f"| {e.id} | {e.name} | {dates} | {len(e.claims)} | {len(e.skills)} | {len(e.tracks)} | "
                     f"{'yes' if e.team else ''} | {best} | {repos} |")  # fmt: skip
    superseded = [c for c in claims if c.status == "superseded"]
    if superseded:
        lines += ["", "## Superseded by record updates", ""]
        lines += [f"- {c.id} → {c.superseded_by}" for c in superseded]
    return "\n".join(lines) + "\n"
