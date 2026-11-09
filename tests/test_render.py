from datetime import date

import pytest

from career_tailor import ats, bank, paths, render, selection, write
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


def test_bold_spans_stay_bold_and_the_rest_stays_literal():
    assert render._rich("Solved **70 of 400** tasks, *not* `x`") == "Solved **70 of 400** tasks, not x"
    assert render._rich("**Top-250** finish (a_b)") == "**Top-250** finish (a\\_b)"
    assert render._rich("plain [link](https://a.b)") == "plain link"
    assert render._rich("up **from 50**; every") == "up **from 50;** every"  # Typst drops a ";" after #strong[]
    assert render.bold_runs("Cut cost **40%** in a week") == [("Cut cost ", False), ("40%", True), (" in a week", False)]


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
    slash = layout((0, 700, 690, 36, "PyTorch/"), (0, 685, 675, 46, "Hugging Face models"))
    assert slash.text() == "PyTorch/Hugging Face models"


def test_plain_text_keeps_rows_and_pages():
    lay = layout((0, 700, 690, 36, "• Kept 98.9%"), (0, 720, 710, 36, "Jadavpur University"), (1, 750, 740, 36, "p2"),
                 pages=2)  # fmt: skip
    assert lay.plain() == "Jadavpur University\n- Kept 98.9%\n\np2\n"


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
        "contact detail not extractable from the PDF: 'ada@example.com'",
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


def test_fit_loosens_an_underfilled_page(mini_bank, sel, draft, tmp_path):
    fitted = render.fit(tmp_path, mini_bank, SPEC, MATCH, sel, draft, selection.PlanEdits(), today=FIXTURE_TODAY)
    assert fitted.layout.pages == 1 and not fitted.dropped
    assert fitted.design.font_size > render.Design().font_size or fitted.layout.fill(fitted.design.margin) >= 0.7
    assert fitted.steps[-1].startswith("fits: 1 page(s)") and fitted.measured_lines_per_page


def test_fit_tightens_then_drops_written_bullets_on_overflow(mini_bank, sel, draft, tmp_path):
    long = draft.model_copy(deep=True)
    for b in long.bullets:
        b.text = (b.text.rstrip(".") + "; ") * 4 + "done."
    fitted = render.fit(tmp_path, mini_bank, SPEC, MATCH, sel, long, selection.PlanEdits(), today=FIXTURE_TODAY)
    assert fitted.layout.pages == 1
    assert any("tightened" in s for s in fitted.steps) and fitted.design.font_size == render.FONT_MIN
    assert fitted.dropped and any("re-planned" in s for s in fitted.steps)
    kept = {c for p in fitted.selection.picks for c in p.claim_ids}
    assert {c for b in fitted.writing.bullets for c in b.claim_ids} == kept
    assert not kept & set(fitted.dropped)


def test_dense_fit_starts_small_and_never_calibrates(mini_bank, sel, draft, tmp_path):
    fitted = render.fit(tmp_path, mini_bank, SPEC, MATCH, sel, draft, selection.PlanEdits(dense=True),
                        today=FIXTURE_TODAY)  # fmt: skip
    assert render.DENSE_FONT[0] <= fitted.design.font_size <= render.DENSE_FONT[1]
    assert fitted.design.margin == render.DENSE.margin
    assert fitted.measured_lines_per_page is None  # calibration is for the default design only


def test_calibration_is_a_mean_over_jobs(tmp_path):
    path = tmp_path / render.CALIBRATION_FILE
    render.save_calibration(path, "letter", 46.0, "job:a")
    render.save_calibration(path, "letter", 50.0, "job:b")
    data = render.save_calibration(path, "letter", 48.0, "job:b")  # a re-check replaces the job's measurement
    assert (data["letter"], data["letter_runs"]) == (47.0, 2)
    assert data["measured"] == {"letter": {"job:a": 46.0, "job:b": 48.0}}
    assert render.load_calibration(path)["letter"] == 47.0
    assert render.load_calibration(tmp_path / "none.yaml") == {}


def test_solve_honours_measured_lines_and_written_claims(mini_bank, sel):
    fewer = selection.solve(mini_bank, SPEC, MATCH, lines_per_page=36)
    assert fewer.lines_budget == 36 and fewer.lines_used <= 36
    only = {"EXP-01.b1", "ENG-01.b1", "RES-01.l1", "RES-02.l1"}
    limited = selection.solve(mini_bank, SPEC, MATCH, only=only | {c for p in sel.picks for c in p.claim_ids
                                                                 if p.section == "Awards and selections"})  # fmt: skip
    assert {c for p in limited.picks if p.section != "Awards and selections" for c in p.claim_ids} <= only


def test_docx_carries_the_same_page(mini_bank, sel, draft, tmp_path):
    path = render.write_docx(tmp_path / "r.docx", mini_bank, sel, draft, render.Design())
    text = render.docx_text(path)
    assert render.text_findings(text, render.expected_lines(mini_bank, sel, draft), ["ada@example.com"], "DOCX") == []
    assert text.startswith("Ada Example\nML Engineer, Evaluation\n")
    from docx import Document

    doc = Document(str(path))
    assert round(doc.sections[0].page_width.inches, 2) == 8.27 and not doc.tables
    assert sum(p.style.name == "List Bullet" for p in doc.paragraphs) == len(draft.bullets) + 1  # + the grade line


def test_parse_check_turns_failed_checks_into_findings(monkeypatch, tmp_path):
    result = {"overall_score": 70, "checks": [
        {"id": "text_layer", "severity": "high", "status": "pass"},
        {"id": "garbled_glyphs", "severity": "high", "status": "fail", "params": {"count": 4}},
        {"id": "multi_column", "severity": "medium", "status": "warn", "params": {"pages": [1]}},
    ]}  # fmt: skip
    monkeypatch.setattr(ats, "call", lambda tool, args: result)
    _, found = ats.parse_check(tmp_path / "r.pdf")
    assert [(f.severity, f.message) for f in found] == [
        ("error", "PDF garbled_glyphs: fail (count=4)"),
        ("warning", "PDF multi_column: warn (pages=[1])"),
    ]


@pytest.mark.local
def test_resume_matcher_parse_checks_a_render(mini_bank, sel, draft, tmp_path):
    data = render.rendercv_input(mini_bank, SPEC, sel, draft, today=FIXTURE_TODAY)
    result, found = ats.parse_check(render.render_pdf(tmp_path, data, "Ada_Example_Resume.pdf"))
    assert result["extractability"] == "full" and not [f for f in found if f.severity == "error"]


SCORED = SPEC.model_copy(update={
    "requirements": [
        R1.model_copy(update={"keywords": ["reproducible evaluation", "Python", "Kubernetes", "evals"]}),
        Requirement(id="R2", text="JAX", quote="JAX", kind="preferred", keywords=["JAX", "Python"]),
        Requirement(id="R3", text="Own evals", quote="own evals", kind="responsibility"),
    ],
    "keywords": {"reproducible evaluation": [], "Python": [], "Kubernetes": [], "evals": ["evaluation"], "JAX": [],
                 "speech": ["audio"]},
})  # fmt: skip


def test_job_keywords_split_required_preferred_and_other():
    kw = ats.job_keywords(SCORED)
    assert kw["role"] == SCORED.title and kw["company"] == "Acme"
    assert kw["required_skills"] == ["reproducible evaluation", "Python", "Kubernetes", "evals"]
    assert kw["preferred_skills"] == ["JAX"]  # Python is already required
    assert kw["keywords"] == ["speech"]
    assert kw["key_responsibilities"] == ["Own evals"]


def test_resume_data_carries_the_page(mini_bank, sel, draft):
    data = ats.resume_data(mini_bank, sel, draft)
    assert data["personalInfo"]["title"] == draft.headline and data["personalInfo"]["email"] == "ada@example.com"
    job = next(x for x in data["workExperience"] if x["company"] == "Example University")
    assert job["description"][0].startswith("Built a reproducible evaluation")
    texts = [t for x in data["workExperience"] + data["personalProjects"] for t in x["description"]]
    awards = data["additional"]["awards"]
    assert sorted(texts + awards) == sorted(render.md_plain(b.text) for b in draft.bullets)
    assert data["additional"]["technicalSkills"] == [t for terms in draft.skills.values() for t in terms]
    assert data["education"][0]["degree"].startswith("B.Sc.")


def test_score_warns_on_required_terms_missing_from_the_page(monkeypatch, mini_bank, sel, draft):
    seen = {}
    monkeypatch.setattr(ats, "call", lambda tool, args: seen.update(tool=tool, args=args) or {"overall_score": 50})
    result, found = ats.score(SCORED, mini_bank, sel, draft)
    assert seen["tool"] == "score_resume" and set(seen["args"]) == {"resume_data", "keywords"}
    assert result["overall_score"] == 50 and result["keywords"]["required_skills"][0] == "reproducible evaluation"
    assert [f.message for f in found] == [
        "required posting term 'Kubernetes' isn't on the page",
        "required posting term 'evals' isn't on the page; the page says 'evaluation'",
    ]


@pytest.mark.local
def test_resume_matcher_scores_a_draft(mini_bank, sel, draft):
    result, _ = ats.score(SCORED, mini_bank, sel, draft)
    assert 0 <= result["overall_score"] <= 100 and "keyword_match" in result["sub_scores"]


def test_a_bare_year_is_not_shown_as_january():
    from types import SimpleNamespace as NS

    one_year = NS(dates=NS(start="2026", end="2026"))
    assert render._dates(one_year) == {"date": 2026}  # the string "2026" renders as "Jan 2026"
    span = NS(dates=NS(start="2025", end="2026-04"))
    assert render._dates(span) == {"start_date": 2025, "end_date": "2026-04"}


def test_em_dashes_become_hyphens_on_the_page():
    data = {"cv": {"name": "A — B", "sections": {"x": [{"name": "Sefai — Rust", "highlights": ["a — b"]}]}}}
    assert "—" not in str(render._hyphens(data))
    assert render._hyphens("Sefai — Rust") == "Sefai - Rust"


def test_header_location_is_city_and_country(mini_bank):
    mini_bank.profile.fields["Location"] = "Kolkata, West Bengal, India"
    assert render._header(mini_bank, "AI Engineer")["location"] == "Kolkata, India"


def test_polish_shows_header_links_and_spaces_skills():
    src = ('#connections(\n  [#link("https://a.b", icon: false, if-underline: false, if-color: false)[a.b]],\n)\n'
           '== Skills\n#strong[Languages:] Python\n')
    out = render._polish_typst(src, 0.75)
    assert "if-underline: true, if-color: true" in out and "if-underline: false" not in out
    assert "== Skills\n#set par(spacing: 0.75em)\n" in out
    assert render._polish_typst(src).count("set par") == 0  # no spacing asked, none added
