import json
import os
import time
from datetime import datetime

import pytest

from career_tailor import bank, cli, paths


def proof_cards(path, record_sha, refs, artifacts=()):
    """Write entries.json (and artifacts.json) for cards given as (id, start, end, coverage[, extra fields])."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cards = [{"id": r[0], "artifact_coverage": r[3], "master_refs": [{"line_start": r[1], "line_end": r[2]}],
              **(r[4] if len(r) > 4 else {})} for r in refs]  # fmt: skip
    path.write_text(json.dumps({"metadata": {"master_sha256": record_sha}, "entries": cards}))
    (path.parent / "artifacts.json").write_text(json.dumps({"artifacts": list(artifacts)}))


def test_build_save_load_round_trip(data_dir):
    p = paths.load()
    built = bank.compile_bank(p)
    bank.save(built, p.bank)
    loaded = bank.load(p.bank)
    assert loaded.entries == built.entries
    assert loaded.profile == built.profile
    assert loaded.tables.tracks == built.tables.tracks
    assert loaded.manifest.record_sha256 == bank.sha256(p.career_record)
    assert loaded.claim("ENG-01.b2").text.startswith("Ran Qwen3-VL 8B")
    assert sorted(f.name for f in (p.bank / "entries").iterdir())[:2] == ["AWD-01.yaml", "AWD-02.yaml"]


def test_rebuild_removes_stale_entry_files(data_dir):
    p = paths.load()
    bank.save(bank.compile_bank(p), p.bank)
    (p.bank / "entries" / "ENG-99.yaml").write_text("id: ENG-99\n")
    bank.save(bank.compile_bank(p), p.bank)
    assert not (p.bank / "entries" / "ENG-99.yaml").exists()


def test_proof_cards_link_by_line_range(data_dir, mini_record):
    p = paths.load()
    lines = mini_record.read_text().split("\n")
    exp1 = lines.index("### EXP-01 · Example University — Research Collaborator, Speech") + 1
    eng1 = lines.index("### ENG-01 · Clawbot — Local Browser Agent") + 1
    lead1 = lines.index("### LEAD-01 · OpenThing — Co-Founder & CEO") + 1
    artifacts = [
        {"id": "REPO-claw", "title": "clawbot", "url": "https://github.com/ada-example/clawbot", "access": "public repository"},
        {"id": "MAIL-claw", "title": "thread", "url": "https://mail.example/1", "access": "private correspondence"},
    ]  # fmt: skip
    proof_cards(p.proof_entries, bank.sha256(mini_record), [
        ("CAREER-001", exp1, exp1 + 12, "personal account only"),
        ("CAREER-002", eng1, eng1 + 12, "partial", {"artifact_ids": ["REPO-claw", "MAIL-claw"],
                                                  "contribution": "Built it alone.", "verification_status": "claim audit incomplete"}),
        ("CAREER-003", lead1, lead1 + 6, "personal account only",
         {"collection_status": "done (user accepted)", "recovery_resolution": {"status": "done", "basis": "Co-founders can confirm."}}),
    ], artifacts)  # fmt: skip
    built = bank.compile_bank(p)
    assert built.claim("EXP-01.b1").evidence_ids == ["CAREER-001"]
    assert (built.claim("EXP-01.b1").proof, built.claim("ENG-01.b1").proof) == ("account", "collected")
    assert built.claim("LEAD-01.b1").proof == "accepted" and built.claim("RES-01.l1").proof == "none"
    assert built.entries["ENG-01"].public_links == ["https://github.com/ada-example/clawbot"]  # private mail left out
    assert built.claim("ENG-01.b1").public_links == ["https://github.com/ada-example/clawbot"]
    assert built.claim("EXP-01.b1").public_links == []
    assert built.proof["CAREER-002"].contribution == "Built it alone."
    assert built.manifest.proof_linked
    summary = next(n for n in built.notes["info"] if n.startswith("Proof backing"))
    assert "2 collected sources, 1 user-accepted closures, 2 personal account only" in summary
    assert "Independent claim audit: not yet done" in summary
    bank.save(built, p.bank)
    loaded = bank.load(p.bank)
    assert loaded.proof == built.proof and loaded.entries == built.entries
    row = next(ln for ln in bank.report(loaded).splitlines() if ln.startswith("| ENG-01 |"))
    assert row.split(" | ")[-2] == "collected"


@pytest.mark.parametrize(
    ("spans", "a", "b", "expected"),
    [
        ([(10, 30)], 12, 12, True),  # whole card, nothing narrower: covers every line
        ([(10, 30), (20, 20)], 20, 20, True),  # the row it is about
        ([(10, 30), (20, 20)], 12, 12, False),  # another row in the same catalog card
        ([(10, 30), (40, 40)], 12, 12, True),  # a narrower citation elsewhere doesn't matter
        ([(50, 60)], 12, 12, False),
    ],
)
def test_covers_prefers_the_cards_own_row(spans, a, b, expected):
    assert bank.covers(spans, a, b) is expected


def test_proof_cards_for_another_record_version_are_skipped(data_dir):
    p = paths.load()
    proof_cards(p.proof_entries, "0" * 64, [("CAREER-001", 1, 999, "partial")])
    built = bank.compile_bank(p)
    assert not built.manifest.proof_linked
    assert any("different record version" in n for n in built.notes["problems"])
    assert any("Ghost result" in n for n in built.notes["info"])


def test_staleness(data_dir, tmp_path, monkeypatch, mini_record):
    record = tmp_path / "record.md"
    record.write_text(mini_record.read_text())
    monkeypatch.setenv("HAHA_CAREER_RECORD", str(record))
    p = paths.load()
    bank.save(bank.compile_bank(p), p.bank)
    b = bank.load(p.bank)
    assert bank.stale_reason(b, p) is None
    p.annotations.mkdir(parents=True)
    note = p.annotations / "EXP-01.yaml"
    note.write_text("entry_id: EXP-01\n")
    future = time.time() + 5
    os.utime(note, (future, future))
    assert "annotations changed" in bank.stale_reason(b, p)
    edited_same_second = datetime.fromisoformat(b.manifest.built_at).timestamp() - 0.001
    os.utime(note, (edited_same_second, edited_same_second))  # saved just before the build read it
    assert bank.stale_reason(b, p) is None
    note.unlink()
    record.write_text(record.read_text() + "\nedited\n")
    assert "career record changed" in bank.stale_reason(b, p)


def test_report_flags_gaps(data_dir):
    p = paths.load()
    text = bank.report(bank.compile_bank(p))
    assert "**LEAD-02** has no claims" in text
    assert "no entry matched “Holographic engine”" in text
    assert text.index("## For information") > text.index("## Needs attention")
    assert "| ENG-01 | Clawbot | May 2030 – Oct 2030 |" in text


def test_cli_bank_builds_and_reports(data_dir, capsys):
    with pytest.raises(SystemExit) as done:
        cli.main(["bank"])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "15 entries" in out and (data_dir / "bank" / "REPORT.md").is_file()
    with pytest.raises(SystemExit):
        cli.main(["bank", "--report"])


def test_cli_load_without_bank_fails_clearly(data_dir):
    with pytest.raises(FileNotFoundError, match="run `uv run tailor bank`"):
        cli.main(["bank", "--report"])


@pytest.mark.local
def test_real_bank_builds_with_proof_links(tmp_path):
    built = bank.compile_bank(paths.load())
    assert built.manifest.proof_linked
    assert built.claim("EXP-01.b1").evidence_ids  # Jadavpur bullet maps to its proof card
    bank.save(built, tmp_path / "bank")
    assert bank.load(tmp_path / "bank").entries == built.entries
