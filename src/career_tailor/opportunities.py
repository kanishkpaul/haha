"""Look a posting up in the opportunity database and its latest apply-now gate.

Reads the newest `opportunity_refresh_*/opportunities.json` and `apply_now_*/` CSVs
under HAHA_OUTPUTS_DIR. Read-only.
"""

import csv
import json
import re
from pathlib import Path

from .models import OpportunityMatch

_URL_FIELDS = ("Application URL", "Official URL", "Careers URL")


def canonical_url(url: str) -> str:
    """Comparable form: no scheme, query, trailing slash, or /apply and /application suffix."""
    url = re.sub(r"^https?://(www\.)?", "", url.strip().lower()).split("?")[0].split("#")[0].rstrip("/")
    return re.sub(r"/(apply|application)$", "", url)


def _latest(outputs: Path, pattern: str) -> Path | None:
    found = sorted(outputs.glob(pattern))
    return found[-1] if found else None


def find(outputs: Path, urls: list[str]) -> OpportunityMatch | None:
    """The opportunity whose URLs match any of `urls`, with its gate decision and apply-now ranking."""
    db = _latest(outputs, "opportunity_refresh_*/opportunities.json")
    if db is None:
        return None
    wanted = {canonical_url(u) for u in urls if u}
    record = next(
        (r for r in json.loads(db.read_text(encoding="utf-8"))
         if wanted & {canonical_url(str(r["fields"].get(f) or "")) for f in _URL_FIELDS}),
        None,
    )  # fmt: skip
    if record is None:
        return None
    fields = record["fields"]
    match = OpportunityMatch(
        id=record["id"],
        name=str(fields.get("Canonical opportunity name") or record["id"]),
        organization=str(fields.get("Organization") or ""),
        deadline=str(fields.get("Deadline") or ""),
    )
    apply_now = _latest(outputs, "apply_now_*")
    if apply_now is None:
        return match
    for row in csv.DictReader((apply_now / "gate_decisions.csv").open(encoding="utf-8")):
        if row["id"] == match.id:
            match.gate, match.cls = row["gate"], row["cls"]
            match.condition = row["condition"] or (row["reason"] if row["gate"] != "KEPT" else "")
    for row in csv.DictReader((apply_now / "apply_now_ranked.csv").open(encoding="utf-8")):
        if row["id"] == match.id:
            match.cls = row["class"]
            match.score = f"{row['score']}/100 (rank {row['rank']})"
            match.condition = row["condition"] or match.condition
            match.top_risk, match.mobility, match.lead_items = (
                row["top_risk"],
                row["mobility_category"],
                row["lead_items"],
            )
    return match
