import json

import pytest

from career_tailor import cli, ingest
from career_tailor.htmltext import html_to_text

GH = {
    "title": " Research Engineer, Evals ", "company_name": "Example Labs",
    "absolute_url": "https://job-boards.greenhouse.io/examplelabs/jobs/123", "location": {"name": "London"},
    "metadata": [{"name": "Location Type", "value": "Hybrid"}], "departments": [{"name": "Research"}],
    "first_published": "2030-01-01",
    "content": "&lt;h2&gt;About&lt;/h2&gt;&lt;p&gt;We build evals.&lt;/p&gt;"
    "&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;",
}  # fmt: skip
ASHBY = {"jobs": [
    {"id": "00000000-0000-0000-0000-000000000001", "title": "Other", "descriptionHtml": "<p>x</p>"},
    {"id": "11111111-2222-3333-4444-555555555555", "title": "Applied Research", "location": "SF",
     "secondaryLocations": [{"location": "Remote"}], "isRemote": True, "employmentType": "FullTime",
     "jobUrl": "https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555",
     "compensation": {"scrapeableCompensationSalarySummary": "$200K – $300K"},
     "descriptionHtml": "<h2>Role</h2><ul><li><p>Build environments.</p></li><li><p>Run evals.</p></li></ul>"},
]}  # fmt: skip
LEVER = {
    "text": "Research Scientist", "hostedUrl": "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "categories": {"commitment": "Full-time", "team": "Evals", "allLocations": ["London", "SF"]},
    "workplaceType": "onsite", "description": "<p>Intro.</p>",
    "lists": [{"text": "REQUIREMENTS", "content": "<li>Python</li><li>Writing</li>"}],
    "additional": "<p>Visa sponsorship available.</p>",
    "salaryRange": {"currency": "GBP", "min": 100000, "max": 200000, "interval": "per-year-salary"},
}  # fmt: skip


def fake_fetch(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        return next(v for k, v in responses.items() if k in url)

    fetch.calls = calls
    return fetch


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://job-boards.greenhouse.io/anthropic/jobs/5198255008", ("greenhouse", "anthropic", "5198255008")),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=1", ("other", "", "")),
        ("https://www.anthropic.com/careers/jobs/42?gh_jid=42", ("greenhouse", "", "42")),
        ("https://jobs.ashbyhq.com/primeintellect/a6448ceb-aa39-4661-a2c0-350ef365c913/application",
         ("ashby", "primeintellect", "a6448ceb-aa39-4661-a2c0-350ef365c913")),
        ("https://jobs.lever.co/apolloresearch/4a65c6e1-785a-4f88-8998-a97574afb7ee/apply",
         ("lever", "apolloresearch", "4a65c6e1-785a-4f88-8998-a97574afb7ee")),
        ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/123", ("workday", "", "")),
        ("https://sparai.org", ("other", "", "")),
    ],
)  # fmt: skip
def test_detect(url, expected):
    assert ingest.detect(url) == expected


def test_html_to_text_handles_escaped_html_lists_and_scripts():
    assert html_to_text(GH["content"]) == "## About\n\nWe build evals.\n\n- Python\n"
    assert html_to_text("<ul><li><p>One</p></li><li><p> Two </p></li></ul><script>x()</script>") == "- One\n- Two\n"


def test_greenhouse_posting():
    fetch = fake_fetch({"boards-api.greenhouse.io": GH})
    posting, raw = ingest.from_url("https://job-boards.greenhouse.io/examplelabs/jobs/123", fetch)
    assert fetch.calls == ["https://boards-api.greenhouse.io/v1/boards/examplelabs/jobs/123"]
    assert (posting.key, posting.company, posting.title) == (
        "greenhouse:examplelabs:123",
        "Example Labs",
        "Research Engineer, Evals",
    )
    assert (posting.location, posting.workplace, posting.department) == ("London", "Hybrid", "Research")
    assert "- Python" in posting.text and raw is GH


def test_ashby_posting_picks_the_job_from_the_board():
    url = "https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555/application"
    posting, _ = ingest.from_url(url, fake_fetch({"api.ashbyhq.com": ASHBY}))
    assert posting.title == "Applied Research" and posting.location == "SF; Remote (remote)"
    assert posting.compensation == "$200K – $300K"
    assert posting.text == "## Role\n\n- Build environments.\n- Run evals.\n"


def test_ashby_closed_posting_is_reported():
    with pytest.raises(LookupError, match="no longer lists"):
        ingest.from_url(
            "https://jobs.ashbyhq.com/acme/99999999-2222-3333-4444-555555555555", fake_fetch({"ashby": ASHBY})
        )


def test_lever_posting_includes_lists_and_additional():
    url = "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/apply"
    posting, _ = ingest.from_url(url, fake_fetch({"api.lever.co": LEVER}))
    assert posting.location == "London; SF" and posting.employment_type == "Full-time"
    assert posting.compensation == "GBP 100000–200000 per-year-salary"
    assert "### REQUIREMENTS" not in posting.text and "## REQUIREMENTS\n\n- Python\n- Writing" in posting.text
    assert posting.text.rstrip().endswith("Visa sponsorship available.")


def test_unfetchable_url_asks_for_a_file():
    with pytest.raises(ValueError, match="save the description"):
        ingest.from_url("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/123")


def test_file_with_fetchable_url_uses_the_api(tmp_path):
    jd = tmp_path / "acme-research.md"
    jd.write_text("https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n\n# Pasted copy\n")
    posting, _ = ingest.from_file(jd, fake_fetch({"api.lever.co": LEVER}))
    assert posting.ats == "lever" and posting.title == "Research Scientist"


def test_plain_file_posting(tmp_path):
    jd = tmp_path / "acme-ml-engineer.md"
    jd.write_text("https://acme.wd5.myworkdayjobs.com/job/1\n# ML Engineer\n\nRequirements: Python.\n")
    posting, _ = ingest.from_file(jd)
    assert (posting.key, posting.ats, posting.company, posting.title) == (
        "file:acme-ml-engineer",
        "workday",
        "acme",
        "ML Engineer",
    )
    assert posting.text.startswith("# ML Engineer")


def test_save_posting_reuses_the_folder_on_refetch(tmp_path):
    fetch = fake_fetch({"greenhouse": GH})
    posting, raw = ingest.from_url("https://job-boards.greenhouse.io/examplelabs/jobs/123", fetch)
    first = ingest.save_posting(tmp_path, posting, raw)
    assert first.name.startswith("example-labs-research-engineer-evals-")
    assert json.loads((first / "posting.json").read_text())["key"] == "greenhouse:examplelabs:123"
    assert (first / "posting.md").read_text().startswith("# Research Engineer, Evals\n\n**Example Labs** · London")
    posting.fetched_at = "2031-05-05T00:00:00+00:00"
    assert ingest.save_posting(tmp_path, posting, raw) == first


def test_cli_job_from_file(data_dir, tmp_path, capsys):
    jd = tmp_path / "acme-ml-engineer.md"
    jd.write_text("# ML Engineer\n\nPython.\n")
    with pytest.raises(SystemExit) as done:
        cli.main(["job", str(jd), "--company", "Acme Corp"])
    assert done.value.code == 0
    assert "ML Engineer at Acme Corp" in capsys.readouterr().out
    assert len(list((data_dir / "applications").glob("acme-corp-ml-engineer-*/posting.md"))) == 1


@pytest.mark.local
def test_live_greenhouse_fetch():
    posting, _ = ingest.from_url("https://job-boards.greenhouse.io/anthropic/jobs/5198255008")
    assert posting.company == "Anthropic" and "Python" in posting.text
