"""Stage 6: render the resume and fit it to the page.

`rendercv_input` turns the bank's facts and the checked draft into RenderCV's
YAML (one column, standard headings, Letter for US/Canada jobs and A4
elsewhere, URLs as plain text); `render_pdf` runs RenderCV, which typesets with
Typst. `Design` holds the typographic knobs the fit loop adjusts.
"""

import re
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

import phonenumbers
import yaml

from . import selection as sel_mod
from .bank import Bank
from .models import JobSpec, Selection, Writing
from .textutil import md_links, md_plain
from .write import education_lines, entry_link

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
