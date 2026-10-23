import pytest

from career_tailor import paths, record
from career_tailor.textutil import md_links, md_plain


@pytest.fixture
def rec(mini_record):
    return record.read(mini_record)


def card(rec, prefix):
    return next(c for s in rec.sections for c in s.cards if c.heading.startswith(prefix))


def test_sections_are_numbered_and_bounded(rec):
    assert [s.number for s in rec.sections] == [1, 2, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 17]
    s4 = rec.section(4)
    assert s4.title == "Research, industry, and independent collaborations"
    assert rec.line(s4.line).startswith("## 4.")
    assert s4.end < rec.section(5).line


def test_cards_hold_typed_blocks(rec):
    exp1 = card(rec, "EXP-01")
    kinds = [b.kind for b in exp1.blocks]
    assert kinds == ["labelled", "subheading", "list", "labelled"]
    assert exp1.blocks[1].label == "Strong reusable bullets"
    item_line, item_text = exp1.blocks[2].items[1]
    assert rec.line(item_line.no) == "- " + item_text
    assert exp1.blocks[3].label == "Scope for discussion"


def test_tables_skip_the_rule_row(rec):
    res1 = card(rec, "RES-01")
    table = next(b for b in res1.blocks if b.kind == "table")
    assert table.header == ["Saved result", "Value and meaning"]
    assert [cells[0] for _, cells in table.rows] == ["Corpus", "Model A"]


def test_quotes_and_paragraphs(rec):
    res2 = card(rec, "RES-02")
    assert [b.kind for b in res2.blocks] == ["labelled", "quote", "paragraph"]
    assert res2.blocks[1].text == "Developed a research plan for testing sketch recognition."


def test_card_end_is_last_content_line(rec):
    exp1 = card(rec, "EXP-01")
    assert rec.line(exp1.end).startswith("**Scope for discussion:**")


def test_md_plain_and_links():
    md = "Fixed it ([widget/widget#42](https://github.com/widget/widget/pull/42)); **19 passing tests**, `pnpm`."
    assert md_plain(md) == "Fixed it (widget/widget#42); 19 passing tests, pnpm."
    assert md_links(md) == ["https://github.com/widget/widget/pull/42"]
    assert md_plain("a *quoted* word, 2 * 3") == "a quoted word, 2 * 3"


@pytest.mark.local
def test_real_record_has_expected_sections():
    rec = record.read(paths.load().career_record)
    numbers = [s.number for s in rec.sections]
    assert numbers[:16] == list(range(1, 17))
    cards = [c.heading.split(" · ")[0] for n in (4, 5, 6, 7) for c in rec.section(n).cards]
    assert len(cards) == 37
