"""Read the career record's Markdown into sections, cards and blocks, keeping line numbers.

This layer knows Markdown structure only. `bank.py` decides what the blocks mean.

- A section starts at `## N. Title` and ends at the next `## ` heading.
- A card starts at `### …` inside a section and ends at the next `###`/`##` heading.
- A block is a run of non-blank lines: a list, a quote, a table, a bold sub-heading,
  a labelled paragraph (`**Label:** text`) or a plain paragraph.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BlockKind = Literal["list", "quote", "table", "subheading", "labelled", "paragraph"]

_SECTION = re.compile(r"^## (\d+)\. (.+)$")
_LIST_ITEM = re.compile(r"^(?:- |\d+\. )(.*)$")
_SUBHEADING = re.compile(r"^\*\*([^*]+?)\*\*:?$")
_LABEL = re.compile(r"^\*\*([^*]+?):\*\*\s*(.*)$")
_TABLE_RULE = re.compile(r"^\|[\s:|-]+\|$")


@dataclass
class Line:
    no: int  # 1-based
    text: str


@dataclass
class Block:
    kind: BlockKind
    lines: list[Line]
    label: str = ""  # subheading text, or the label of a labelled paragraph
    text: str = ""  # quote / paragraph / labelled-paragraph body, joined with spaces
    header: list[str] = field(default_factory=list)  # table header cells
    rows: list[tuple[Line, list[str]]] = field(default_factory=list)  # table body rows
    items: list[tuple[Line, str]] = field(default_factory=list)  # list items

    @property
    def start(self) -> int:
        return self.lines[0].no

    @property
    def end(self) -> int:
        return self.lines[-1].no


@dataclass
class Card:
    heading: str  # text after "### "
    line: int
    end: int
    blocks: list[Block]


@dataclass
class Section:
    number: int
    title: str
    line: int
    end: int
    blocks: list[Block]  # before the first card
    cards: list[Card]


@dataclass
class Record:
    path: Path
    lines: list[str]
    sections: list[Section]

    def section(self, number: int) -> Section | None:
        return next((s for s in self.sections if s.number == number), None)

    def line(self, no: int) -> str:
        return self.lines[no - 1]


def table_cells(text: str) -> list[str]:
    return [c.strip() for c in text.strip().strip("|").split("|")]


def _classify(run: list[Line]) -> Block:
    first = run[0].text
    if all(_LIST_ITEM.match(ln.text) for ln in run):
        return Block("list", run, items=[(ln, _LIST_ITEM.match(ln.text).group(1)) for ln in run])
    if first.startswith(">"):
        body = " ".join(ln.text.lstrip(">").strip() for ln in run)
        return Block("quote", run, text=body)
    if first.startswith("|"):
        body = [ln for ln in run if not _TABLE_RULE.match(ln.text.strip())]
        header, rest = body[0], body[1:]
        return Block("table", run, header=table_cells(header.text), rows=[(ln, table_cells(ln.text)) for ln in rest])
    if len(run) == 1 and (m := _SUBHEADING.match(first)):
        return Block("subheading", run, label=m.group(1).strip())
    joined = " ".join(ln.text.strip() for ln in run)
    if m := _LABEL.match(joined):
        return Block("labelled", run, label=m.group(1).strip(), text=m.group(2).strip())
    return Block("paragraph", run, text=joined)


def blocks_of(lines: list[Line]) -> list[Block]:
    blocks, run = [], []
    for ln in lines:
        if ln.text.strip():
            run.append(ln)
        elif run:
            blocks.append(_classify(run))
            run = []
    if run:
        blocks.append(_classify(run))
    return blocks


def read(path: Path) -> Record:
    raw = path.read_text(encoding="utf-8").split("\n")
    lines = [Line(i + 1, t) for i, t in enumerate(raw)]
    starts = [(i, _SECTION.match(ln.text)) for i, ln in enumerate(lines)]
    starts = [(i, m) for i, m in starts if m]
    heading_idx = [i for i, ln in enumerate(lines) if ln.text.startswith("## ")] + [len(lines)]

    sections = []
    for i, m in starts:
        stop = next(j for j in heading_idx if j > i)
        body = lines[i + 1 : stop]
        card_idx = [k for k, ln in enumerate(body) if ln.text.startswith("### ")] + [len(body)]
        cards = []
        for a, b in zip(card_idx, card_idx[1:], strict=False):
            content = body[a + 1 : b]
            end = max((ln.no for ln in content if ln.text.strip()), default=body[a].no)
            cards.append(Card(body[a].text[4:].strip(), body[a].no, end, blocks_of(content)))
        end = max((ln.no for ln in body if ln.text.strip()), default=lines[i].no)
        sections.append(
            Section(int(m.group(1)), m.group(2).strip(), lines[i].no, end, blocks_of(body[: card_idx[0]]), cards)
        )
    return Record(path, raw, sections)
