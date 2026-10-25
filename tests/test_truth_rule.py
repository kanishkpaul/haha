"""Safeguards for the truth rule: every record entry is true, and proof never weighs a claim.

Proof (whether an artifact survives, its type or count, or a personal account) may only say whether there is
something to show on request. These tests fail if it reaches selection, a prompt pack or a stage's logic, or if
proof commentary creeps into the annotation overlays. If one fails, fix the code, not the test.
"""

import ast
import re
from pathlib import Path

import pytest
import yaml

import career_tailor
from career_tailor import annotate, bank, match, paths, selection, verify, write
from career_tailor.models import TRUTH_RULE, Coverage, JobSpec, Match, Requirement, Support

R1 = Requirement(
    id="R1", text="Reproducible evaluation", quote="build reproducible evaluations", kind="basic", weight=3
)
R2 = Requirement(id="R2", text="Browser agents", quote="experience building browser agents", kind="preferred")
SPEC = JobSpec(key="test:acme:1", company="Acme", title="ML Engineer, Evaluation", location="Berlin",
               requirements=[R1, R2], keywords={"evaluation": ["evals"]})  # fmt: skip
MATCH = Match(key=SPEC.key, track="ML evaluation", coverage=[
    Coverage(requirement_id="R1", verdict="strong",
             support=[Support(claim_id="EXP-01.b1", strength="strong", quote="Built a reproducible evaluation")]),
    Coverage(requirement_id="R2", verdict="partial", note="terminal only",
             support=[Support(claim_id="ENG-01.b1", strength="partial", quote="terminal browser agent with")]),
])  # fmt: skip

LABELS = ["collected", "accepted", "account", "none"]


@pytest.fixture
def mini_bank(data_dir):
    return bank.compile_bank(paths.load())


def relabel(b, pick):
    """Give every claim the proof label `pick(i)` and return the bank."""
    for i, claim in enumerate(c for e in b.entries.values() for c in e.claims):
        claim.proof = pick(i)
    return b


VARIANTS = {label: (lambda i, label=label: label) for label in LABELS}
VARIANTS["alternating"] = lambda i: LABELS[i % 4]
VARIANTS["reversed"] = lambda i: LABELS[3 - i % 4]


def packs(b, folder: Path) -> dict[str, str]:
    """Every prompt pack a model reads, built from bank `b`."""
    sel = selection.solve(b, SPEC, MATCH)
    w = write.source_writing(b, SPEC, sel)
    tracks = [t.target for t in b.tables.tracks]
    out = {
        "match": match.write_pack(folder / "m", b, SPEC, match.retrieve(b, SPEC)).read_text(),
        "write": write.write_pack(folder / "w", b, SPEC, MATCH, sel, "").read_text(),
        "verify": verify.write_pack(folder / "v", b, SPEC, sel, w).read_text(),
    }
    for p in annotate.write_packs(list(b.entries.values()), b.tables.skills, tracks, folder / "ann", folder / "a"):
        out[f"annotate {p.stem}"] = p.read_text()
    return {k: v.replace(str(folder), "<folder>") for k, v in out.items()}


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_proof_never_changes_the_plan(mini_bank, data_dir, variant):
    baseline = selection.solve(mini_bank, SPEC, MATCH)
    other = relabel(bank.compile_bank(paths.load()), VARIANTS[variant])
    assert selection.solve(other, SPEC, MATCH) == baseline


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_proof_never_reaches_a_prompt_pack(mini_bank, tmp_path, variant):
    baseline = packs(relabel(mini_bank, VARIANTS["collected"]), tmp_path / "a")
    other = packs(relabel(bank.compile_bank(paths.load()), VARIANTS[variant]), tmp_path / "b")
    assert other == baseline


def test_every_prompt_pack_states_the_rule(mini_bank, tmp_path):
    for name, text in packs(mini_bank, tmp_path).items():
        assert TRUTH_RULE in text, f"{name} pack is missing the truth rule"


def test_gap_report_has_no_proof_column_and_keeps_verdicts(mini_bank):
    collected = match.gap_report(SPEC, MATCH, relabel(mini_bank, VARIANTS["collected"]))
    account = match.gap_report(SPEC, MATCH, relabel(mini_bank, VARIANTS["account"]))
    assert "| Proof |" not in collected and "| Proof |" not in account
    assert "No artifact to show" not in collected and "No artifact to show" in account
    assert "work behind these verdicts is true" in account
    # Apart from the "No artifact to show" list, the reports are identical.
    assert account.split("## No artifact to show")[0] == collected.split("## Knockouts")[0].split("## Evidence")[0]


# ---------------------------------------------------------------- static guard

# Proof attributes, and the only functions allowed to read them. Every entry here is bookkeeping or display:
# building the cards, saving the bank, its report, the annotation overlay's labels, provenance, and the gap
# report's "No artifact to show" list. Adding a function here needs a reason that isn't weighting a claim.
PROOF_ATTRS = {"proof", "claim_proof", "accepted_basis", "verification_status"}
ALLOWED = {
    "models.py": {"strength"},
    "bank.py": {"read_proof_cards", "link_proof", "proof_summary", "compile_bank", "save", "load", "report"},
    "annotate.py": {"apply_proof_overrides", "_proof_text"},
    "assemble.py": {"claim_trace"},
    "match.py": {"best_proof", "gap_report"},
    "cli.py": {"cmd_bank"},
}


def _is_proof_read(node: ast.Attribute) -> bool:
    if node.attr in PROOF_ATTRS:
        return True
    # A card's `strength` is proof; a match Support's `strength` (strong/partial) is not.
    receiver = node.value.id if isinstance(node.value, ast.Name) else getattr(node.value, "attr", "")
    return node.attr == "strength" and "card" in receiver.lower()


def proof_reads(path: Path) -> list[tuple[str, int]]:
    """(enclosing function, line) for every read of a proof attribute in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def walk(node, func):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else func
            if isinstance(child, ast.Attribute) and _is_proof_read(child):
                found.append((name, child.lineno))
            walk(child, name)

    walk(tree, "<module>")
    return found


def test_no_stage_weighs_proof():
    src = Path(career_tailor.__file__).parent
    offenders = []
    for path in sorted(src.glob("*.py")):
        allowed = ALLOWED.get(path.name, set())
        offenders += [f"{path.name}:{line} in {func}" for func, line in proof_reads(path) if func not in allowed]
    assert not offenders, "proof read outside bookkeeping (truth rule):\n" + "\n".join(offenders)


def test_selection_has_no_proof_term():
    text = (Path(career_tailor.__file__).parent / "selection.py").read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""|#.*', "", text)
    assert not re.search(r"\.proof\b|ProofStrength|accepted_basis|card\.strength", code)


# ---------------------------------------------------------------- the real overlays

DOUBT = re.compile(
    r"\bProof:|rests? on (?:your|kanishk'?s?) (?:own )?account|personal account|your (?:report|statement)|"
    r"isn'?t established|not established|aren'?t established|need(?:s)? (?:their|its) own (?:record|mapping)|"
    r"not in the recovered|recovered sources|isn'?t linked|not verified|unverified|self-reported|"
    r"lead with b\d|module linkage open",
    re.I,
)


@pytest.mark.local
def test_real_overlays_carry_no_proof_commentary():
    p = paths.load()
    problems = []
    for f in sorted(p.annotations.glob("*.yaml")):
        a = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for field in ("note", "team_note"):
            if m := DOUBT.search(a.get(field) or ""):
                problems.append(f"{f.name} {field}: “{m.group(0)}”")
    assert not problems, "proof commentary in annotations (truth rule):\n" + "\n".join(problems)


@pytest.mark.local
def test_real_bank_hides_no_claim_for_its_proof():
    p = paths.load()
    b = bank.compile_bank(p)
    hidden_account = [
        c.id for e in b.entries.values() for c in e.claims if not c.show_on_resume and c.proof == "account"
    ]
    assert not hidden_account, f"claims hidden while marked personal account: {hidden_account}"
