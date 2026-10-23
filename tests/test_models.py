import pytest
import yaml
from pydantic import ValidationError

from career_tailor.models import CheckReport, Claim, Entry, Finding, Metric, SourceRef


def make_entry() -> Entry:
    claim = Claim(
        id="RES-09.b1",
        entry_id="RES-09",
        kind="bullet",
        text="Detected 54/55 events with two channels.",
        metrics=[Metric(text="54/55", value=54 / 55), Metric(text="two", value=2)],
        conditions=["Retrospective; convulsive events only"],
        source=SourceRef(line_start=10, line_end=10),
    )
    return Entry(
        id="RES-09",
        kind="research",
        heading="Probe — Example",
        name="Probe",
        subtitle="Example",
        claims=[claim],
        source=SourceRef(line_start=8, line_end=12),
    )


def test_entry_round_trips_through_yaml():
    entry = make_entry()
    loaded = Entry.model_validate(yaml.safe_load(yaml.safe_dump(entry.model_dump(mode="json"))))
    assert loaded == entry
    assert loaded.claim("RES-09.b1").metrics[0].text == "54/55"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Claim(id="x", entry_id="y", kind="bullet", text="t", confidence=0.9)


def test_check_report_ok_only_without_errors():
    report = CheckReport(findings=[Finding(check="style", severity="warning", message="long")])
    assert report.ok
    report.findings.append(Finding(check="numbers", severity="error", message="novel number"))
    assert not report.ok
