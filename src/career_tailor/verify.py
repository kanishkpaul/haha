"""Stage 5: check the tailored resume against the evidence bank.

Deterministic checks first: every number, name, tool and date in a bullet must
appear in its cited claims, and a metric that keeps its number keeps its
condition (record section 15). Policy lint (section 12.3), style lint and a
per-bullet entailment review follow.
"""

import re

from . import numbers
from .bank import Bank
from .match import tokens
from .models import Finding, Selection, TailoredBullet, Writing
from .textutil import md_plain
from .write import sources

# A name or tool: has a capital after the first letter (ScreenCaptureKit, CNN/GRU), a digit (Qwen3, Base64),
# an inner dot (llama.cpp), or a leading capital mid-sentence (Unify, Munich). Hyphenated compounds are
# checked part by part, so "direct-vs-English-pivot" asks only about "English".
_WORD = re.compile(r"(?<![\w.+#/'’-])[A-Za-z][\w.+#/'’-]*")
_SENTENCE_START = re.compile(r"(?:^|[:;(]\s*|[.!?]\s+)$")


def _clean(word: str) -> str:
    word = re.sub(r"['’]s$", "", word)
    return word.rstrip(".,;:)-/'’")


def _proper(part: str, opens_clause: bool) -> bool:
    inner_caps = any(ch.isupper() for ch in part[1:])
    technical = any(ch.isdigit() for ch in part) or re.search(r"\w\.\w", part)
    return bool(inner_caps or technical or (part[0].isupper() and not opens_clause))


def names(text: str) -> list[str]:
    """Names, tools and other proper terms in a bullet, skipping words that open a sentence or clause."""
    out = []
    for m in _WORD.finditer(text):
        word = _clean(m.group())
        opens = bool(_SENTENCE_START.search(text[: m.start()]))
        for i, part in enumerate(p for p in word.split("-") if p):
            if part[0].isalpha() and _proper(part, opens and i == 0):
                out.append(part)
    return list(dict.fromkeys(out))


def _label(b: TailoredBullet) -> str:
    return "+".join(b.claim_ids)


def _finding(check: str, b: TailoredBullet, message: str, severity: str = "error") -> Finding:
    return Finding(check=check, severity=severity, message=f"{_label(b)}: {message}", bullet=md_plain(b.text)[:80])


def facts(w: Writing, bank: Bank, sel: Selection) -> list[Finding]:
    findings: list[Finding] = []
    picked = {p.entry_id for p in sel.picks}
    for b in w.bullets:
        if b.entry_id not in picked:
            continue  # the draft guard reports bullets outside the plan
        text = md_plain(b.text)
        entry = bank.entries[b.entry_id]
        claims = [bank.claim(c) for c in b.claim_ids]
        source = sources(bank, entry, claims)
        lowered = source.lower()

        if extra := numbers.tokens(text, years=True) - numbers.tokens(source, years=True):
            findings.append(_finding("numbers", b, f"states {', '.join(sorted(extra))}, which its claims don't"))
        if unknown := [n for n in names(text) if n.lower() not in lowered]:
            findings.append(_finding("names", b, f"names {', '.join(unknown)}, which its claims don't"))
        said = set(tokens(text)) | numbers.tokens(text, years=True)
        for claim in claims:
            for condition in claim.conditions:
                metric, _, qualifier = md_plain(condition).partition(" — ")
                if not qualifier or not numbers.tokens(text, years=True) & numbers.tokens(metric, years=True):
                    continue
                if not (set(tokens(qualifier)) | numbers.tokens(qualifier, years=True)) & said:
                    findings.append(_finding("conditions", b, f"keeps {metric} but drops its condition: {qualifier}"))
    return findings
