"""Stage 6: render the resume and fit it to the page.

`rendercv_input` turns the bank's facts and the checked draft into RenderCV's
YAML (one column, standard headings, Letter for US/Canada jobs and A4
elsewhere, URLs as plain text); `render_pdf` runs RenderCV, which typesets with
Typst. `Design` holds the typographic knobs the fit loop adjusts.

`read_pdf` reads the result back with pdfminer, as an ATS would, and
`extraction_findings` checks that every line of the draft survives intact, in
order, with no ligature or (cid:) garbage.

`fit` renders until the page is full and not over: it tightens or loosens the
design within bounds, and when type alone can't make room it re-solves the plan
on a measured line budget, choosing only among bullets already written and
checked. The measured lines per page are saved as calibration for later plans.
"""

import re
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

import phonenumbers
import yaml
from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.shared import Inches, Pt
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTTextContainer, LTTextLine

from . import selection as sel_mod
from .bank import Bank
from .jobspec import norm
from .models import Finding, JobSpec, Match, PlanEdits, Selection, Writing
from .textutil import md_links, md_plain
from .write import contact_line, education_lines, entry_link

RENDER_DIR = "render"
YAML_FILE = "resume.yaml"
HEADING_CHARS = 88  # an entry heading longer than this at 10 pt wraps beside the date column; the link is dropped
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass(frozen=True)
class Design:
    """Typography the fit loop may adjust, within the bounds in `fit.py`."""

    font_size: float = 10.0  # pt, body text
    line_spacing: float = 0.6  # em between lines of a paragraph
    margin: float = 0.5  # in, all four sides
    section_space: float = 0.35  # cm above each section title
    entry_space: float = 0.25  # cm between entries
    bullet_space: float = 0.06  # cm between bullets

    def step(self, direction: int) -> "Design":
        """One notch tighter (-1) or looser (+1): type, leading and the space around sections and entries."""

        def nudge(value: float, by: float) -> float:
            return round(value + direction * by, 2)

        return replace(
            self,
            font_size=nudge(self.font_size, 0.25),
            line_spacing=nudge(self.line_spacing, 0.05),
            section_space=nudge(self.section_space, 0.05),
            entry_space=nudge(self.entry_space, 0.05),
        )


def _escape(text: str) -> str:
    """Plain text for RenderCV's Markdown: keep characters that would turn into formatting literal."""
    return re.sub(r"([*_`\[\]#<>])", r"\\\1", md_plain(text))


def _date(value: str | None) -> str | None:
    if not value:
        return None
    return value if value == "present" or re.fullmatch(r"\d{4}(-\d{2})?", value) else None


def _dates(entry) -> dict:
    if not entry.dates:
        return {}
    start, end = _date(entry.dates.start), _date(entry.dates.end)
    if end == "present" and start and start > date.today().strftime("%Y-%m"):
        end = None  # RenderCV checks "present" against the real date and refuses a later start; show the start
    if start and end and start != end:
        return {"start_date": start, "end_date": end}
    return {"date": start or end} if (start or end) else {}


def _username(field: str, host: str) -> str | None:
    links = md_links(field) or [field]
    found = re.search(rf"{host}/(?:in/)?([\w.-]+)", links[0])
    return found.group(1) if found else None


def _valid_phone(number: str) -> bool:
    """RenderCV rejects numbers it can't parse; leave those off rather than fail the render."""
    try:
        return phonenumbers.is_valid_number(phonenumbers.parse(number))
    except phonenumbers.NumberParseException:
        return False


def contact_details(bank: Bank) -> list[str]:
    """The email and phone as they should read in the PDF, for the extraction check."""
    fields = bank.profile.fields
    out = [md_plain(fields["Primary email"])] if fields.get("Primary email") else []
    phone = md_plain(fields.get("Phone", ""))
    if phone and _valid_phone(phone):
        out.append(phonenumbers.format_number(phonenumbers.parse(phone), phonenumbers.PhoneNumberFormat.INTERNATIONAL))
    return out


def _header(bank: Bank, headline: str) -> dict:
    fields = bank.profile.fields
    cv: dict = {"name": bank.profile.name, "headline": _escape(headline)}
    if fields.get("Location"):
        cv["location"] = md_plain(fields["Location"])
    if fields.get("Primary email"):
        cv["email"] = md_plain(fields["Primary email"])
    if fields.get("Phone") and _valid_phone(md_plain(fields["Phone"])):
        cv["phone"] = md_plain(fields["Phone"]).replace(" ", "")
    if fields.get("Website"):
        site = (md_links(fields["Website"]) or [md_plain(fields["Website"])])[0]
        cv["website"] = site if site.startswith("http") else f"https://{site}"
    networks = []
    for network, host, key in (("GitHub", "github.com", "GitHub"), ("LinkedIn", "linkedin.com", "LinkedIn")):
        if fields.get(key) and (user := _username(fields[key], host)):
            networks.append({"network": network, "username": user})
    if networks:
        cv["social_networks"] = networks
    return cv


def _education(bank: Bank) -> list[dict]:
    edu = bank.entries.get("EDU-01")
    if edu is None:
        return []
    degree, _, area = edu.subtitle.split(" — ")[0].partition(", ")
    institution_field = md_plain(bank.profile.fields.get("Institution", ""))
    item = {"institution": edu.name, "degree": degree, "area": area or degree, **_dates(edu)}
    if ", " in institution_field:
        item["location"] = institution_field.rsplit(", ", 1)[1]
    second = education_lines(bank)[1:]
    if second:
        item["highlights"] = [_escape(second[0])]
    return [item]


def rendercv_input(
    bank: Bank, spec: JobSpec, sel: Selection, w: Writing, design: Design | None = None, today: date | None = None
) -> dict:
    """RenderCV's input. `today` dates "Present" (RenderCV refuses a start date after it)."""
    design = design or Design()
    bullets: dict[str, list[str]] = {}
    for b in w.bullets:
        bullets.setdefault(b.entry_id, []).append(_escape(b.text))
    today = today or date.today()
    sections: dict[str, list] = {"Education": _education(bank)}
    for pick in sel.picks:
        entry = bank.entries[pick.entry_id]
        items = sections.setdefault(pick.section, [])
        if entry.kind in sel_mod.ONE_LINE_KINDS:
            items += [{"bullet": text} for text in bullets.get(entry.id, [])]
        elif entry.kind == "experience":
            items.append({"company": _escape(entry.name), "position": _escape(entry.subtitle), **_dates(entry),
                          "highlights": bullets.get(entry.id, [])})  # fmt: skip
        else:
            link = entry_link(bank, entry)
            if link and len(f"{entry.name} — {entry.subtitle} · {link}") > HEADING_CHARS:
                link = ""  # a wrapped heading costs a line; the project name and role matter more than the URL
            summary = " · ".join(x for x in (_escape(entry.subtitle), link) if x)
            items.append({"name": _escape(entry.name), "summary": summary or None, **_dates(entry),
                          "highlights": bullets.get(entry.id, [])})  # fmt: skip
    if w.skills:
        sections["Skills"] = [{"label": _escape(g), "details": ", ".join(_escape(t) for t in terms)}
                              for g, terms in w.skills.items()]  # fmt: skip
    cv = _header(bank, w.headline) | {"sections": sections}
    locale = {"language": "english", "present": "Present", "month_abbreviations": MONTHS}
    settings = {"current_date": today}
    return {"cv": cv, "design": _design(sel.paper, design), "locale": locale, "settings": settings}


def _design(paper: str, d: Design) -> dict:
    return {
        "theme": "engineeringresumes",
        "page": {
            "size": "us-letter" if paper == "letter" else "a4",
            "top_margin": f"{d.margin}in",
            "bottom_margin": f"{d.margin}in",
            "left_margin": f"{d.margin}in",
            "right_margin": f"{d.margin}in",
            "show_footer": False,
            "show_top_note": False,
        },
        "typography": {
            "line_spacing": f"{d.line_spacing}em",
            "alignment": "left",
            "font_size": {
                "body": f"{d.font_size}pt",
                "headline": f"{d.font_size}pt",
                "connections": f"{d.font_size}pt",
            },
        },
        "links": {"underline": False},
        "header": {
            "connections": {
                "phone_number_format": "international",
                "hyperlink": True,
                "show_icons": False,
                "display_urls_instead_of_usernames": True,
                "separator": "|",
            },
            "space_below_name": "0.3cm",
            "space_below_headline": "0.25cm",
            "space_below_connections": "0.3cm",
        },
        "section_titles": {"type": "with_full_line", "space_above": f"{d.section_space}cm", "space_below": "0.15cm"},
        "sections": {
            "space_between_regular_entries": f"{d.entry_space}cm",
            "space_between_text_based_entries": f"{d.bullet_space}cm",
        },
        "entries": {"highlights": {"space_above": f"{d.bullet_space}cm", "space_between_items": f"{d.bullet_space}cm"}},
        "templates": {
            "experience_entry": {
                "main_column": "**COMPANY** — POSITION\nHIGHLIGHTS",
                "date_and_location_column": "DATE",
            },
            "normal_entry": {"main_column": "**NAME** — SUMMARY\nHIGHLIGHTS", "date_and_location_column": "DATE"},
            "education_entry": {
                "main_column": "**INSTITUTION** — DEGREE_WITH_AREA\nHIGHLIGHTS",
                "degree_column": None,
                "date_and_location_column": "DATE",
            },
            "one_line_entry": {"main_column": "**LABEL:** DETAILS"},
        },
    }


def pdf_name(bank: Bank) -> str:
    return re.sub(r"\W+", "_", bank.profile.name).strip("_") + "_Resume.pdf"


def render_pdf(folder: Path, data: dict, name: str) -> Path:
    """Write the RenderCV input and typeset it. Returns the PDF path."""
    out = folder / RENDER_DIR
    out.mkdir(parents=True, exist_ok=True)
    source = out / YAML_FILE
    source.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    pdf = out / name
    cmd = [sys.executable, "-m", "rendercv", "render", str(source), "--pdf-path", str(pdf),
           "--typst-path", str(out / "resume.typ"), "-nomd", "-nohtml", "-nopng"]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=out)
    if done.returncode != 0 or not pdf.is_file():
        raise RuntimeError(f"RenderCV failed: {(done.stderr or done.stdout).strip()[-1500:]}")
    return pdf


def design_dict(d: Design) -> dict:
    return asdict(d)


# ---------------------------------------------------------------- reading the PDF back


@dataclass(frozen=True)
class Line:
    page: int
    top: float  # points from the page bottom
    bottom: float
    left: float
    text: str


@dataclass(frozen=True)
class PdfLayout:
    pages: int
    width: float
    height: float
    lines: list[Line]

    def text(self) -> str:
        """Text in reading order: pages, then top to bottom, then left to right. Typst breaks lines after a
        hyphen or slash, so a line ending in one joins the next without a space ("less-" + "deferential",
        "PyTorch/" + "Hugging Face")."""
        out = ""
        for line in sorted(self.lines, key=lambda x: (x.page, -round(x.top), x.left)):
            piece = line.text.removeprefix("•").strip()
            out += piece if out.endswith(("-", "/")) else (" " + piece if out else piece)
        return out

    def plain(self) -> str:
        """The page as plain text, one PDF line per row and a blank row between pages, for the .txt copy."""
        rows, page = [], 0
        for line in sorted(self.lines, key=lambda x: (x.page, -round(x.top), x.left)):
            if line.page != page:
                rows.append("")
                page = line.page
            text = line.text.strip()
            rows.append("- " + text[1:].strip() if text.startswith("•") else text)
        return "\n".join(rows) + "\n"

    def fill(self, margin_in: float) -> float:
        """How much of the last page's text area is used, from 0 to 1."""
        last = [x for x in self.lines if x.page == self.pages - 1]
        if not last:
            return 0.0
        top = self.height - margin_in * 72
        return min(1.0, (top - min(x.bottom for x in last)) / (self.height - 2 * margin_in * 72))


def read_pdf(pdf: Path) -> PdfLayout:
    lines, width, height, pages = [], 0.0, 0.0, 0
    for number, page in enumerate(extract_pages(str(pdf), laparams=LAParams())):
        pages, width, height = number + 1, page.width, page.height
        for element in page:
            if isinstance(element, LTTextContainer):
                for line in element:
                    if isinstance(line, LTTextLine) and line.get_text().strip():
                        lines.append(Line(number, line.y1, line.y0, line.x0, line.get_text().strip()))
    return PdfLayout(pages=pages, width=width, height=height, lines=lines)


_GARBAGE = re.compile(r"\(cid:\d+\)|[\ufb00-\ufb06\ufffd]")


def expected_lines(bank: Bank, sel: Selection, w: Writing) -> list[str]:
    """What must come out of the PDF, in page order: headline, entry names, bullets, skills."""
    bullets: dict[str, list[str]] = {}
    for b in w.bullets:
        bullets.setdefault(b.entry_id, []).append(md_plain(b.text))
    out = [w.headline]
    for pick in sel.picks:
        entry = bank.entries[pick.entry_id]
        if entry.kind not in sel_mod.ONE_LINE_KINDS:
            out.append(md_plain(entry.name))
        out += bullets.get(entry.id, [])
    out += [f"{group}: {', '.join(terms)}" for group, terms in w.skills.items()]
    return out


def text_findings(raw: str, expected: list[str], contact: list[str], what: str = "PDF") -> list[Finding]:
    """Every expected line present and in order in a file's extracted text, with no garbage glyphs."""
    findings: list[Finding] = []

    def add(message: str) -> None:
        findings.append(Finding(check="extraction", severity="error", message=message))

    if found := sorted(set(_GARBAGE.findall(raw))):
        add(f"the {what} text has ligature or unmapped glyphs: {' '.join(found)}")
    text = norm(raw)
    position = 0
    for line in expected:
        at = text.find(norm(line), position)
        if at < 0:
            where = "out of reading order in" if norm(line) in text else "missing from"
            add(f"{where} the {what} text: {line[:80]!r}")
            continue
        position = at + len(norm(line))
    for item in contact:
        if norm(item) not in text:
            add(f"contact detail not extractable from the {what}: {item!r}")
    return findings


def extraction_findings(layout: PdfLayout, expected: list[str], contact: list[str], pages: int) -> list[Finding]:
    findings = text_findings(layout.text(), expected, contact)
    if layout.pages > pages:
        findings.append(Finding(check="pages", severity="error",
                                message=f"the PDF has {layout.pages} pages; the plan allows {pages}"))  # fmt: skip
    return findings


# ---------------------------------------------------------------- fit loop

FONT_MIN, FONT_MAX = 9.5, 10.5
FILL_MIN = 0.7  # the last page should be at least this full
CALIBRATION_FILE = "calibration.yaml"


@dataclass
class Fit:
    pdf: Path
    design: Design
    selection: Selection
    writing: Writing
    layout: PdfLayout
    steps: list[str]
    dropped: list[str]  # claim ids the fit loop took off the page
    measured_lines_per_page: float | None = None  # at the default design, for calibration


def _content_pages(layout: PdfLayout, design: Design) -> float:
    return layout.pages - 1 + layout.fill(design.margin)


def _trim(w: Writing, sel: Selection) -> Writing:
    """The draft limited to bullets whose claims are all still on the page."""
    picked = {c for p in sel.picks for c in p.claim_ids}
    return w.model_copy(update={"bullets": [b for b in w.bullets if set(b.claim_ids) <= picked]})


def fit(folder: Path, bank: Bank, spec: JobSpec, m: Match, sel: Selection, w: Writing, edits: PlanEdits,
        today: date | None = None, max_renders: int = 12) -> Fit:  # fmt: skip
    design, steps, dropped, measured = Design(), [], [], None
    written = {c for b in w.bullets for c in b.claim_ids}
    name = pdf_name(bank)
    direction = 0  # the way the design last moved, so it never swings back and forth
    for _ in range(max_renders):
        pdf = render_pdf(folder, rendercv_input(bank, spec, sel, w, design, today), name)
        layout = read_pdf(pdf)
        used = _content_pages(layout, design)
        if design == Design() and measured is None:
            measured = sel.lines_used / used if used else None
        if layout.pages > sel.pages:
            if design.font_size > FONT_MIN:
                design, direction = design.step(-1), -1
                steps.append(f"{layout.pages} pages: tightened to {design.font_size:g} pt")
                continue
            # The solver counts estimated lines; this many of them fill one real page at this design.
            lines_per_page = sel.lines_used / used * 0.98
            before = {c for p in sel.picks for c in p.claim_ids}
            sel = sel_mod.solve(bank, spec, m, edits, lines_per_page=lines_per_page, only=written & before)
            gone = sorted(before - {c for p in sel.picks for c in p.claim_ids})
            if not gone:
                steps.append(f"{layout.pages} pages at the tightest design and nothing left to drop")
                break
            dropped += gone
            w = _trim(w, sel)
            steps.append(f"{layout.pages} pages at {design.font_size:g} pt: re-planned at {lines_per_page:.1f} lines "
                         f"per page, dropping {', '.join(gone)}")  # fmt: skip
            continue
        fill = layout.fill(design.margin)
        if layout.pages == sel.pages and fill < FILL_MIN and design.font_size < FONT_MAX and direction >= 0:
            design, direction = design.step(+1), +1
            steps.append(f"last page {fill:.0%} full: loosened to {design.font_size:g} pt")
            continue
        if direction > 0 and layout.pages > sel.pages:
            continue
        steps.append(f"fits: {layout.pages} page(s), last page {fill:.0%} full, {design.font_size:g} pt")
        break
    return Fit(pdf, design, sel, w, layout, steps, dropped, measured)


def load_calibration(path: Path) -> dict[str, float]:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}


def save_calibration(path: Path, paper: str, lines_per_page: float) -> dict[str, float]:
    """A running mean of measured lines per page for each paper size."""
    data = load_calibration(path)
    runs = data.get(f"{paper}_runs", 0)
    old = data.get(paper, lines_per_page)
    data[paper] = round((old * runs + lines_per_page) / (runs + 1), 2)
    data[f"{paper}_runs"] = runs + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return data


# ---------------------------------------------------------------- DOCX copy

PAGE_INCHES = {"letter": (8.5, 11.0), "a4": (8.27, 11.69)}


def write_docx(path: Path, bank: Bank, sel: Selection, w: Writing, design: Design) -> Path:
    """The same page as a Word file, for portals that want one: same sections, entries and bullets, plain
    styles an ATS reads (headings, bullets, a right tab for dates), no tables or text boxes."""
    doc = Document()
    section = doc.sections[0]
    width, height = PAGE_INCHES[sel.paper]
    section.page_width, section.page_height = Inches(width), Inches(height)
    for side in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
        setattr(section, side, Inches(design.margin))
    normal = doc.styles["Normal"]
    normal.font.size = Pt(design.font_size)
    normal.paragraph_format.space_after = Pt(1)
    right_tab = Inches(width - 2 * design.margin)

    def heading(text: str) -> None:
        p = doc.add_heading(text, level=2)
        p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(6), Pt(2)

    def line(left: str, right: str = "", bold: str = "") -> None:
        p = doc.add_paragraph()
        p.paragraph_format.tab_stops.add_tab_stop(right_tab, WD_TAB_ALIGNMENT.RIGHT)
        if bold:
            p.add_run(bold).bold = True
        p.add_run(left)
        if right:
            p.add_run(f"\t{right}")

    def bullet(text: str) -> None:
        doc.add_paragraph(md_plain(text), style="List Bullet")

    title = doc.add_heading(bank.profile.name, level=1)
    title.paragraph_format.space_after = Pt(0)
    doc.add_paragraph(md_plain(w.headline))
    doc.add_paragraph(contact_line(bank))
    heading("Education")
    edu = bank.entries.get("EDU-01")
    if edu:
        dates = edu.dates.display if edu.dates and edu.dates.display else ""
        line(f" — {edu.subtitle.split(' — ')[0]}", dates, bold=edu.name)
        for extra in education_lines(bank)[1:]:
            bullet(extra)
    bullets: dict[str, list[str]] = {}
    for b in w.bullets:
        bullets.setdefault(b.entry_id, []).append(b.text)
    current = None
    for pick in sel.picks:
        entry = bank.entries[pick.entry_id]
        if pick.section != current:
            current = pick.section
            heading(current)
        if entry.kind not in sel_mod.ONE_LINE_KINDS:
            dates = entry.dates.display if entry.dates and entry.dates.display else ""
            link = entry_link(bank, entry)
            role = " — ".join(x for x in (entry.subtitle, link) if x)
            line(f" — {role}" if role else "", dates, bold=md_plain(entry.name))
        for text in bullets.get(entry.id, []):
            bullet(text)
    if w.skills:
        heading("Skills")
        for group, terms in w.skills.items():
            line(", ".join(terms), bold=f"{group}: ")
    doc.save(path)
    return path


def docx_text(path: Path) -> str:
    return "\n".join(p.text for p in Document(str(path)).paragraphs)
