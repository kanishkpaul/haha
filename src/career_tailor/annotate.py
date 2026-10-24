"""Skills tagging and the reviewed annotation overlay.

Automatic tags come from the record's own skills bank (section 11): a skill is
tagged on a claim when its name appears in the claim text, and on an entry when
it appears in any claim or the entry's stack. The annotation overlay
(HAHA_DATA_DIR/annotations/<ID>.yaml) corrects what parsing cannot know: team
credit, display names, extra skills, tracks, and claims to keep off resumes. It
is applied after parsing on every build, so it survives record changes.
"""

import json
import re
from pathlib import Path

import yaml

from .models import Annotation, Claim, DateRange, Entry, ProofCard, SkillArea
from .textutil import md_plain

# Single words that are not skills on their own once a slash phrase is expanded.
_GENERIC = set(
    "act api apis app browser control desktop engineering feature honorific hub interruption jobs loops model "
    "observe optimization planning recovery register solver ui verify".split()
)


def _variants(skill: str) -> set[str]:
    """Expand slash alternatives in place: "model/solver handoff inspection" gives
    "model handoff inspection" and "solver handoff inspection"; "llama.cpp/GGUF" gives both names."""
    words = skill.split()
    out = {skill}
    for i, word in enumerate(words):
        if "/" in word and not word.startswith("/") and not word.endswith("/"):
            for alt in word.split("/"):
                phrase = " ".join([*words[:i], alt, *words[i + 1 :]])
                if " " in phrase or (len(phrase) >= 2 and phrase.lower() not in _GENERIC):
                    out.add(phrase)
    return out


def _pattern(variant: str) -> re.Pattern:
    sensitive = " " not in variant and any(ch.isupper() for ch in variant)
    return re.compile(rf"(?<![\w+#.-]){re.escape(variant)}(?![\w+#]|\.\w)", 0 if sensitive else re.IGNORECASE)


class Tagger:
    """Finds skills-bank terms in text. Canonical names are the skills bank's own spelling."""

    def __init__(self, areas: list[SkillArea]):
        self.patterns = [(skill, _pattern(v)) for area in areas for skill in area.skills for v in _variants(skill)]

    def find(self, text: str) -> list[str]:
        plain = md_plain(text)
        return list(dict.fromkeys(skill for skill, pat in self.patterns if pat.search(plain)))


def tag(entries: list[Entry], areas: list[SkillArea]) -> None:
    tagger = Tagger(areas)
    for entry in entries:
        for claim in entry.claims:
            claim.skills = tagger.find(claim.text)
        found = [s for c in entry.claims for s in c.skills] + tagger.find(", ".join(entry.stack))
        entry.skills = list(dict.fromkeys(found))


# ---------------------------------------------------------------- overlay


def load_annotations(directory: Path) -> list[Annotation]:
    if not directory.is_dir():
        return []
    return [Annotation.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
            for f in sorted(directory.glob("*.yaml"))]  # fmt: skip


def add_extra_claims(entries: list[Entry], annotations: list[Annotation]) -> list[str]:
    """Promote verbatim note or field sentences to claims. Runs before metrics, conditions,
    tagging and proof links, so promoted claims get all of them. Returns problems."""
    by_id = {e.id: e for e in entries}
    problems = []
    for a in annotations:
        entry = by_id.get(a.entry_id)
        if entry is None or not a.extra_claims:
            continue
        source_text = md_plain(" ".join([*entry.notes.values(), *entry.fields.values()]))
        for n, text in enumerate(a.extra_claims, start=1):
            if md_plain(text) not in source_text:
                problems.append(f"annotation {a.entry_id}: extra claim is not verbatim from its notes: {text[:60]}")
                continue
            entry.claims.append(Claim(id=f"{entry.id}.n{n}", entry_id=entry.id, kind="fact", text=text,
                                      label="from notes", source=entry.source))  # fmt: skip
    return problems


def apply_annotations(entries: list[Entry], annotations: list[Annotation]) -> list[str]:
    """Apply overlays in place. Returns problems (unknown entries or claims)."""
    by_id = {e.id: e for e in entries}
    problems = []
    for a in annotations:
        entry = by_id.get(a.entry_id)
        if entry is None:
            problems.append(f"annotation for unknown entry {a.entry_id}")
            continue
        claims = {c.id: c for c in entry.claims}
        if a.name:
            entry.name = a.name
        if a.subtitle:
            entry.subtitle = a.subtitle
        if a.dates_display:
            entry.dates = entry.dates or DateRange(raw=a.dates_display)
            entry.dates.display = a.dates_display
        if a.skills:
            entry.skills = list(a.skills)
        if a.tracks:
            entry.tracks = list(a.tracks)
        if a.team is not None:
            entry.team = a.team
        if a.team_note:
            entry.notes["Team credit"] = a.team_note
        if a.note:
            entry.notes["Annotation"] = a.note
        for cid, skills in a.claim_skills.items():
            if cid in claims:
                claims[cid].skills = list(skills)
            else:
                problems.append(f"annotation {a.entry_id}: unknown claim {cid}")
        if not a.skills:
            entry.skills = list(dict.fromkeys([*entry.skills, *(s for c in entry.claims for s in c.skills)]))
        for cid in a.hide_claims:
            if cid in claims:
                claims[cid].show_on_resume = False
            else:
                problems.append(f"annotation {a.entry_id}: unknown claim {cid}")
        for ref in a.same_work:
            target = by_id.get(ref.split(".")[0])
            if target is None or ("." in ref and ref not in {c.id for c in target.claims}):
                problems.append(f"annotation {a.entry_id}: same_work names unknown {ref}")
            elif ref not in entry.same_work:
                entry.same_work.append(ref)
    return problems


_ORDER = ["collected", "accepted", "account", "none"]


def apply_proof_overrides(entries: list[Entry], annotations: list[Annotation]) -> list[str]:
    """Lower a claim's proof strength where its card's evidence doesn't reach that specific claim.

    Runs after proof linking. Overrides can only weaken: an upgrade is refused and reported."""
    claims = {c.id: c for e in entries for c in e.claims}
    problems = []
    for a in annotations:
        for cid, strength in a.claim_proof.items():
            claim = claims.get(cid)
            if claim is None:
                problems.append(f"annotation {a.entry_id}: unknown claim {cid}")
            elif _ORDER.index(strength) < _ORDER.index(claim.proof):
                problems.append(f"annotation {a.entry_id}: can't raise {cid} from {claim.proof} to {strength}")
            else:
                claim.proof = strength
    return problems


# ---------------------------------------------------------------- prompt packs

_INSTRUCTIONS = """\
# Annotate {entry_id}

Review the parsed entry below and write `{target}`: YAML matching the schema at the end.
Every field is optional; write only what improves on the parse.

Rules (from the career record, sections 11, 12.3 and 15):
- Facts come only from the record. Do not add a skill the entry's claims, stack or notes do not show.
  A skill studied but not implemented (record section 11 note) is not a skill here.
- `skills`: the full skill list for the entry, using the skills-bank spelling where one exists.
  Replaces the automatic tags, so keep the good automatic ones.
- `claim_skills`: per-claim skill lists, only where the automatic tags are wrong or missing.
- `team`: true when results are shared with others (team projects, co-authored work, co-founded
  ventures). `team_note` says precisely who did what, as the record states it.
  Arnesh Banerjee and Arnesh Gade are different people.
- `name` / `subtitle`: how the entry should head a resume, e.g. organisation and role.
  For independent work, use the record's recommended heading; never invent an employer or title.
- `dates_display`: only if the parsed display is wrong or missing, in the form "Aug 2026 – Present".
- `hide_claims`: claim ids that must never appear on a resume (confidential, superseded, or a
  plan rather than a result). Leave out claims that are merely weak.
- `tracks`: role-track targets this entry should lead or support, from the tracks list below.
- `claim_proof`: per-claim proof strength where a proof card's evidence summary says that specific
  claim (a number, placement, duration, sole ownership) isn't supported by the collected sources:
  set it to "account". Never raise a claim above what its cards show.
- `extra_claims`: sentences copied word for word from this entry's notes or fields that state a
  result or contribution, for entries whose content sits only in notes. Never paraphrase.

## Parsed entry

```yaml
{entry_yaml}
```

## Proof cards

What the evidence folder says backs this entry: the contribution statement, what the collected
sources establish, and their limits. Team credit and guidance must agree with it; where the proof
narrows a claim, say so in `note` or hide the claim.

{proof}

## Existing annotation

```yaml
{existing}
```

## Skills bank

{skills}

## Role tracks

{tracks}

## Schema

```json
{schema}
```
"""


def _proof_text(entry: Entry, cards: dict[str, ProofCard]) -> str:
    ids = list(dict.fromkeys(c for claim in entry.claims for c in claim.evidence_ids))
    if not ids:
        return "No proof card maps to this entry."
    blocks = []
    for cid in ids:
        card = cards.get(cid)
        if card is None:
            continue
        claims = ", ".join(c.id for c in entry.claims if cid in c.evidence_ids)
        lines = [f"### {card.id} · {card.title} ({card.strength}; claims {claims})",
                 f"- Contribution: {card.contribution or 'not stated'}",
                 f"- What the sources establish: {card.evidence_summary or 'not stated'}"]  # fmt: skip
        if card.accepted_basis:
            lines.append(f"- Accepted closure: {card.accepted_basis}")
        lines += [f"- Source: {a.title} ({a.access}){' ' + a.url if a.public else ''}"
                  for a in card.artifacts if a.id != "ACCOUNT-MASTER"]  # fmt: skip
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def write_packs(entries: list[Entry], areas: list[SkillArea], tracks: list[str], annotations_dir: Path,
                out_dir: Path, cards: dict[str, ProofCard] | None = None) -> list[Path]:  # fmt: skip
    """One prompt pack per entry that can head a resume section. Returns the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    skills = "\n".join(f"- **{a.area}:** {', '.join(a.skills)}" for a in areas)
    track_list = "\n".join(f"- {t}" for t in tracks)
    schema = json.dumps(Annotation.model_json_schema(), indent=2)
    written = []
    for entry in entries:
        if entry.kind == "selection":
            continue
        target = annotations_dir / f"{entry.id}.yaml"
        existing = target.read_text(encoding="utf-8").strip() if target.is_file() else "# none yet"
        dumped = yaml.safe_dump(entry.model_dump(mode="json", exclude_defaults=True, exclude={"claims": {"__all__": {
            "evidence_ids", "source", "metrics"}}}), sort_keys=False, allow_unicode=True, width=10_000)  # fmt: skip
        pack = out_dir / f"{entry.id}.md"
        pack.write_text(_INSTRUCTIONS.format(entry_id=entry.id, target=target, entry_yaml=dumped.strip(),
                                             existing=existing, skills=skills, tracks=track_list, schema=schema,
                                             proof=_proof_text(entry, cards or {})),
                        encoding="utf-8")  # fmt: skip
        written.append(pack)
    return written
