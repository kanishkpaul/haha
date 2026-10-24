from datetime import date

import pytest

from career_tailor import bank, paths, render, selection, write
from career_tailor.models import Coverage, JobSpec, Match, Requirement, Support

R1 = Requirement(
    id="R1", text="Reproducible evaluation", quote="build reproducible evaluations", kind="basic", weight=3
)
SPEC = JobSpec(key="test:acme:1", company="Acme", title="ML Engineer, Evaluation", location="Berlin", requirements=[R1])
MATCH = Match(key=SPEC.key, track="ML evaluation", coverage=[
    Coverage(requirement_id="R1", verdict="strong",
             support=[Support(claim_id="EXP-01.b1", strength="strong", quote="Built a reproducible evaluation")]),
])  # fmt: skip


FIXTURE_TODAY = date(2031, 1, 15)  # the synthetic record's dates run to 2031


@pytest.fixture
def mini_bank(data_dir):
    return bank.compile_bank(paths.load())


@pytest.fixture
def sel(mini_bank):
    return selection.solve(mini_bank, SPEC, MATCH)


@pytest.fixture
def draft(mini_bank, sel):
    return write.source_writing(mini_bank, SPEC, sel)


def test_header_uses_profile_fields(mini_bank, sel, draft):
    cv = render.rendercv_input(mini_bank, SPEC, sel, draft)["cv"]
    assert cv["name"] == "Ada Example" and cv["headline"] == "ML Engineer, Evaluation"
    assert cv["email"] == "ada@example.com" and cv["location"] == "Springfield, Example State"
    assert "phone" not in cv  # the fixture's number isn't a valid one
    assert cv["social_networks"] == [{"network": "GitHub", "username": "ada-example"}]


def test_sections_follow_the_plan(mini_bank, sel, draft):
    cv = render.rendercv_input(mini_bank, SPEC, sel, draft)["cv"]
    names = list(cv["sections"])
    assert names[0] == "Education" and names[-1] == "Skills"
    assert names[1:-1] == list(dict.fromkeys(p.section for p in sel.picks))
    edu = cv["sections"]["Education"][0]
    assert (edu["institution"], edu["degree"], edu["area"]) == ("Example Institute of Technology, Springfield", "B.Sc.",
                                                               "Computer Science")  # fmt: skip
    assert (edu["start_date"], edu["end_date"]) == ("2027-08", "2031-06")
    job = next(x for x in cv["sections"]["Experience"] if x["company"] == "Example University")
    assert job["position"] == "Research Collaborator, Speech"
    assert job["date"] == "2030-09" and "end_date" not in job  # "present" after a future start: start alone
    assert job["highlights"][0].startswith("Built a reproducible evaluation")
    awards = cv["sections"]["Awards and selections"]
    assert all(set(a) == {"bullet"} for a in awards)


def test_markdown_characters_stay_literal():
    assert render._escape("a *b* [c](d) under_score") == "a b c under\\_score"
    assert render._escape("2 * 3 = 6") == "2 \\* 3 = 6"


def test_page_and_typography(mini_bank, sel, draft):
    data = render.rendercv_input(mini_bank, SPEC, sel, draft, render.Design(font_size=9.5, margin=0.4))
    assert data["design"]["page"]["size"] == "a4" and data["design"]["page"]["top_margin"] == "0.4in"
    assert data["design"]["typography"]["font_size"]["body"] == "9.5pt"
    assert data["locale"]["present"] == "Present" and data["settings"]["current_date"] == date.today()
    tighter = render.Design().step(-1)
    assert tighter.font_size == 9.75 and tighter.entry_space < render.Design().entry_space


def test_render_produces_a_pdf(mini_bank, sel, draft, tmp_path):
    data = render.rendercv_input(mini_bank, SPEC, sel, draft, today=FIXTURE_TODAY)
    pdf = render.render_pdf(tmp_path, data, "Ada_Example_Resume.pdf")
    assert pdf.is_file() and pdf.read_bytes()[:4] == b"%PDF"
    assert (tmp_path / render.RENDER_DIR / render.YAML_FILE).is_file()


def layout(*lines, pages=1):
    return render.PdfLayout(pages=pages, width=612, height=792, lines=[render.Line(*x) for x in lines])


def test_pdf_text_reads_in_order_and_joins_hyphenated_breaks():
    lay = layout((0, 700, 690, 36, "• Kept 98.9% of deferential vs less-"), (0, 685, 675, 46, "deferential ones."),
                 (0, 720, 710, 400, "Aug 2026"), (0, 720, 710, 36, "Jadavpur University"))  # fmt: skip
    assert lay.text() == "Jadavpur University Aug 2026 Kept 98.9% of deferential vs less-deferential ones."


def test_fill_measures_the_last_page():
    lay = layout((0, 750, 740, 36, "a"), (1, 750, 400, 36, "b"), pages=2)
    assert lay.fill(0.5) == pytest.approx((792 - 36 - 400) / (792 - 72))


def test_extraction_findings():
    lay = layout((0, 750, 740, 36, "Second line"), (0, 760, 752, 36, "First line"), (0, 730, 720, 36, "of(cid:3)ce"),
                 pages=2)  # fmt: skip
    found = [f.message for f in render.extraction_findings(lay, ["First line", "Second line", "Third line"],
                                                           ["ada@example.com"], 1)]  # fmt: skip
    assert found == [
        "the PDF text has ligature or unmapped glyphs: (cid:3)",
        "missing from the PDF text: 'Third line'",
        "contact detail not extractable: 'ada@example.com'",
        "the PDF has 2 pages; the plan allows 1",
    ]
    order = render.extraction_findings(layout((0, 760, 750, 36, "B"), (0, 740, 730, 36, "A")), ["A", "B"], [], 1)
    assert [f.message for f in order] == ["out of reading order in the PDF text: 'B'"]


def test_rendered_fixture_extracts_cleanly(mini_bank, sel, draft, tmp_path):
    data = render.rendercv_input(mini_bank, SPEC, sel, draft, today=FIXTURE_TODAY)
    lay = render.read_pdf(render.render_pdf(tmp_path, data, "Ada_Example_Resume.pdf"))
    expected = render.expected_lines(mini_bank, sel, draft)
    assert render.extraction_findings(lay, expected, ["ada@example.com"], 1) == []
    assert lay.pages == 1 and 0 < lay.fill(0.5) <= 1
