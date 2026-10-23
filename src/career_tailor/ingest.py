"""Fetch a job posting from its applicant-tracking system's public API, or read it from a file.

Supported APIs (public, no key):
- Greenhouse: boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}
- Ashby: api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
- Lever: api.lever.co/v0/postings/{board}/{id}?mode=json
Workday and other hosts are detected from the URL but read from a local file.
"""

import json
import re
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .htmltext import html_to_text
from .models import Posting

FetchJson = Callable[[str], object]

_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})")),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")),
]


def detect(url: str) -> tuple[str, str, str]:
    """(ats, board, job id) for a posting URL; ats is "workday" or "other" when it can't be fetched."""
    for ats, pattern in _PATTERNS:
        if m := pattern.search(url):
            return ats, m.group(1), m.group(2)
    query = parse_qs(urlparse(url).query)
    if "gh_jid" in query:  # company career page embedding Greenhouse; the board token is unknown
        return "greenhouse", "", query["gh_jid"][0]
    if "myworkdayjobs.com" in url or "workday" in urlparse(url).netloc:
        return "workday", "", ""
    return "other", "", ""


def http_json(url: str) -> object:
    request = urllib.request.Request(
        url, headers={"User-Agent": "haha-career-tailor/0.1", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _greenhouse(board: str, job_id: str, fetch: FetchJson) -> tuple[Posting, object]:
    api = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
    raw = fetch(api)
    meta = {m.get("name"): m.get("value") for m in raw.get("metadata") or []}
    posting = Posting(
        key=f"greenhouse:{board}:{job_id}", ats="greenhouse", org=board,
        company=raw.get("company_name") or board, title=raw["title"].strip(),
        url=raw.get("absolute_url") or "", api_url=api,
        location=(raw.get("location") or {}).get("name", ""),
        workplace=str(meta.get("Location Type") or ""),
        department=", ".join(d["name"] for d in raw.get("departments") or []),
        published=raw.get("first_published") or "", fetched_at=_now(),
        text=html_to_text(raw.get("content") or ""),
    )  # fmt: skip
    return posting, raw


def _ashby(board: str, job_id: str, fetch: FetchJson) -> tuple[Posting, object]:
    api = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
    board_data = fetch(api)
    raw = next((j for j in board_data.get("jobs", []) if j.get("id") == job_id), None)
    if raw is None:
        raise LookupError(f"Ashby board {board} no longer lists job {job_id}; the posting may be closed")
    places = [raw.get("location") or ""] + [s.get("location", "") for s in raw.get("secondaryLocations") or []]
    comp = raw.get("compensation") or {}
    posting = Posting(
        key=f"ashby:{board}:{job_id}", ats="ashby", org=board, company=board, title=raw["title"].strip(),
        url=raw.get("jobUrl") or "", api_url=api,
        location="; ".join(p for p in places if p) + (" (remote)" if raw.get("isRemote") else ""),
        workplace=str(raw.get("workplaceType") or ""), employment_type=str(raw.get("employmentType") or ""),
        compensation=comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary") or "",
        department=raw.get("department") or "", published=raw.get("publishedAt") or "", fetched_at=_now(),
        text=html_to_text(raw["descriptionHtml"]) if raw.get("descriptionHtml") else raw.get("descriptionPlain", ""),
    )  # fmt: skip
    return posting, raw


def _lever(board: str, job_id: str, fetch: FetchJson) -> tuple[Posting, object]:
    api = f"https://api.lever.co/v0/postings/{board}/{job_id}?mode=json"
    raw = fetch(api)
    parts = [raw.get("description") or ""]
    parts += [f"<h3>{item.get('text', '')}</h3><ul>{item.get('content', '')}</ul>" for item in raw.get("lists") or []]
    parts.append(raw.get("additional") or "")
    cats = raw.get("categories") or {}
    salary = raw.get("salaryRange") or {}
    posting = Posting(
        key=f"lever:{board}:{job_id}", ats="lever", org=board, company=board, title=raw["text"].strip(),
        url=raw.get("hostedUrl") or "", api_url=api,
        location="; ".join(cats.get("allLocations") or [cats.get("location", "")]),
        workplace=str(raw.get("workplaceType") or ""), employment_type=cats.get("commitment", ""),
        compensation=(f"{salary.get('currency', '')} {salary.get('min', '')}–{salary.get('max', '')} "
                      f"{salary.get('interval', '')}".strip() if salary else ""),
        department=cats.get("team", ""), published=str(raw.get("createdAt") or ""), fetched_at=_now(),
        text=html_to_text("".join(parts)),
    )  # fmt: skip
    return posting, raw


_FETCHERS = {"greenhouse": _greenhouse, "ashby": _ashby, "lever": _lever}


def from_url(url: str, fetch: FetchJson = http_json) -> tuple[Posting, object]:
    ats, board, job_id = detect(url)
    if ats not in _FETCHERS or not board:
        raise ValueError(f"can't fetch {ats} postings from {url}; save the description to a .md file and pass that")
    return _FETCHERS[ats](board, job_id, fetch)


def from_file(path: Path, fetch: FetchJson = http_json) -> tuple[Posting, object]:
    """A job description file. If its first line is a fetchable posting URL, the API copy wins."""
    text = path.read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    url = first.strip() if first.strip().startswith("http") else ""
    if url and detect(url)[0] in _FETCHERS and detect(url)[1]:
        return from_url(url, fetch)
    body = rest if url else text
    heading = re.search(r"^#\s+(.+)$", body, re.M)
    company = path.stem.split("-")[0]
    posting = Posting(
        key=f"file:{path.stem}", ats=detect(url)[0] if url else "other", org=company, company=company,
        title=heading.group(1).strip() if heading else path.stem, url=url, fetched_at=_now(), text=body.strip() + "\n",
    )  # fmt: skip
    return posting, {"file": str(path)}


# ---------------------------------------------------------------- application folders


def slug(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:60].rstrip("-")


def find_application(applications: Path, key: str) -> Path | None:
    for posting_file in sorted(applications.glob("*/posting.json")):
        if json.loads(posting_file.read_text(encoding="utf-8")).get("key") == key:
            return posting_file.parent
    return None


def save_posting(applications: Path, posting: Posting, raw: object) -> Path:
    """Write posting.json, posting.md and raw.json to the posting's application folder (reused on refetch)."""
    folder = find_application(applications, posting.key)
    if folder is None:
        day = posting.fetched_at[:10]
        folder = applications / f"{slug(posting.company)}-{slug(posting.title)}-{day}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "posting.json").write_text(posting.model_dump_json(indent=2), encoding="utf-8")
    (folder / "raw.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    header = [f"# {posting.title}", "", f"**{posting.company}** · {posting.location or 'location not stated'}"]
    extras = [posting.workplace, posting.employment_type, posting.compensation, posting.department]
    if any(extras):
        header.append(" · ".join(x for x in extras if x))
    header += [f"Source: {posting.url or posting.key} ({posting.ats}), fetched {posting.fetched_at}", "", "---", ""]
    (folder / "posting.md").write_text("\n".join(header) + posting.text, encoding="utf-8")
    return folder
