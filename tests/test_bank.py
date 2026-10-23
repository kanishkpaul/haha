import json
import os
import time

import pytest

from career_tailor import bank, cli, paths


def proof_cards(path, record_sha, refs):
    path.parent.mkdir(parents=True, exist_ok=True)
    cards = [{"id": cid, "master_refs": [{"line_start": a, "line_end": b}]} for cid, a, b in refs]
    path.write_text(json.dumps({"metadata": {"master_sha256": record_sha}, "entries": cards}))


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
    start = lines.index("### EXP-01 · Example University — Research Collaborator, Speech") + 1
    proof_cards(p.proof_entries, bank.sha256(mini_record), [("CAREER-001", start, start + 12)])
    built = bank.compile_bank(p)
    assert built.claim("EXP-01.b1").evidence_ids == ["CAREER-001"]
    assert built.claim("ENG-01.b1").evidence_ids == []
    assert built.manifest.proof_linked


def test_proof_cards_for_another_record_version_are_skipped(data_dir):
    p = paths.load()
    proof_cards(p.proof_entries, "0" * 64, [("CAREER-001", 1, 999)])
    built = bank.compile_bank(p)
    assert not built.manifest.proof_linked
    assert any("different record version" in n for n in built.notes["problems"])


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
    note.unlink()
    record.write_text(record.read_text() + "\nedited\n")
    assert "career record changed" in bank.stale_reason(b, p)


def test_report_flags_gaps(data_dir):
    p = paths.load()
    text = bank.report(bank.compile_bank(p))
    assert "**LEAD-02** has no claims" in text
    assert "unresolved item “Holographic engine”" in text
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
