import pytest
import yaml

from career_tailor import bank, cli, paths
from career_tailor.annotate import Tagger, _variants
from career_tailor.models import SkillArea

AREAS = [
    SkillArea(area="Languages", skills=["Python", "C++", "Java", "Rust"]),
    SkillArea(area="Inference", skills=["llama.cpp/GGUF", "model/solver handoff inspection", "Node"]),
    SkillArea(area="Apps", skills=["Retrieval-augmented generation", "interruption/recovery"]),
]


def test_variants_expand_slashes_in_place():
    assert _variants("model/solver handoff inspection") == {
        "model/solver handoff inspection", "model handoff inspection", "solver handoff inspection"}  # fmt: skip
    assert _variants("llama.cpp/GGUF") == {"llama.cpp/GGUF", "llama.cpp", "GGUF"}
    assert _variants("interruption/recovery") == {"interruption/recovery"}  # generic halves dropped


@pytest.mark.parametrize(
    ("text", "skills"),
    [
        ("Built it in Python and C++ with llama.cpp", ["Python", "C++", "llama.cpp/GGUF"]),
        ("JavaScript code and a signed node exchange", []),
        ("Wrote Java and Rust services on Node", ["Java", "Rust", "Node"]),
        ("Added retrieval-augmented generation", ["Retrieval-augmented generation"]),
        ("A model and a solver", []),
    ],
)
def test_tagger_matches_whole_terms(text, skills):
    assert Tagger(AREAS).find(text) == skills


def test_build_tags_claims_and_entries(data_dir):
    built = bank.compile_bank(paths.load())
    assert built.claim("ENG-01.b2").skills == ["llama.cpp/GGUF"]
    assert built.entries["ENG-01"].skills == ["llama.cpp/GGUF", "TypeScript"]
    assert built.entries["RES-01"].skills == ["Python", "PyTorch"]


def write_annotation(directory, data):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{data['entry_id']}.yaml").write_text(yaml.safe_dump(data))


def test_annotations_override_and_survive_rebuild(data_dir):
    p = paths.load()
    write_annotation(p.annotations, {
        "entry_id": "ENG-02", "name": "Teamfold (team project)", "team": True,
        "team_note": "Co-led with Bob Sample; Ada owned perception.", "skills": ["Python", "OCR"],
        "claim_skills": {"ENG-02.b1": ["leadership"]}, "hide_claims": ["ENG-02.b2"],
    })  # fmt: skip
    write_annotation(p.annotations, {"entry_id": "LEAD-02", "dates_display": "Mar 2029 – Dec 2029"})
    for _ in range(2):  # rebuilding re-applies the overlay
        built = bank.compile_bank(p)
        bank.save(built, p.bank)
    e = bank.load(p.bank).entries["ENG-02"]
    assert (e.name, e.team, e.skills) == ("Teamfold (team project)", True, ["Python", "OCR"])
    assert e.notes["Team credit"].startswith("Co-led")
    assert e.claims[0].skills == ["leadership"]
    assert not e.claims[1].show_on_resume
    assert bank.load(p.bank).entries["LEAD-02"].dates.display == "Mar 2029 – Dec 2029"
    assert not [n for n in built.notes["problems"] if n.startswith("annotation")]


def test_annotation_problems_are_reported(data_dir):
    p = paths.load()
    write_annotation(p.annotations, {"entry_id": "ENG-77"})
    write_annotation(p.annotations, {"entry_id": "ENG-01", "hide_claims": ["ENG-01.b9"]})
    problems = bank.compile_bank(p).notes["problems"]
    assert "annotation for unknown entry ENG-77" in problems
    assert "annotation ENG-01: unknown claim ENG-01.b9" in problems


def test_bad_annotation_fails_validation(data_dir):
    p = paths.load()
    write_annotation(p.annotations, {"entry_id": "ENG-01", "confidence": 3})
    with pytest.raises(ValueError, match="confidence"):
        bank.compile_bank(p)


def test_cli_writes_annotation_packs(data_dir, capsys):
    with pytest.raises(SystemExit):
        cli.main(["bank"])
    with pytest.raises(SystemExit) as done:
        cli.main(["bank", "--annotate"])
    assert done.value.code == 0
    packs = sorted((data_dir / "work" / "annotate").glob("*.md"))
    assert len(packs) == 14  # every entry except selections
    text = (data_dir / "work" / "annotate" / "ENG-01.md").read_text()
    assert "# Annotate ENG-01" in text and str(data_dir / "annotations" / "ENG-01.yaml") in text
    assert "Arnesh Banerjee and Arnesh Gade are different people" in text
    assert '"hide_claims"' in text and "ML evaluation" in text


def test_extra_claims_must_be_verbatim_from_notes(data_dir):
    p = paths.load()
    write_annotation(p.annotations, {"entry_id": "LEAD-02", "extra_claims": [
        "Founded a reading circle with participants from two universities.",
        "Founded a reading circle with 500 members.",
    ]})  # fmt: skip
    built = bank.compile_bank(p)
    claims = built.entries["LEAD-02"].claims
    assert [c.id for c in claims] == ["LEAD-02.n1"]
    assert claims[0].source == built.entries["LEAD-02"].source
    assert any("not verbatim" in n for n in built.notes["problems"])
