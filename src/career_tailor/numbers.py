"""Find the numbers a claim states, so later stages can check none is invented or dropped.

Numbers glued to identifiers are not claims: `IndicTrans2`, `G0–G3`, `F0.5`,
`Gemma-4-E2B`, `Llama-3.2-1B`'s "3.2". A number after a word hyphen still is:
`top-250`, `sub-512 MiB`, `4-bit`.
"""

import re

from .models import Metric

_UNITS = r"%|×|x(?![a-z])|[KMB](?![a-z])|tok/s|MiB|KiB|GiB|MB|GB|TB|ms|μs|Hz|FPS|s(?![a-z])"
_NUMBER = re.compile(
    rf"""
    (?<![\w.])(?<![\w.]/)                  # not inside an identifier or path
    (?P<cur>[₹$€£])?
    (?P<num>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?:/(?P<den>\d{{1,3}}(?:,\d{{3}})+|\d+)(?![\w.]))?  # fraction: 54/55, 4,334/4,334
    (?P<unit>\s?(?:{_UNITS}))?
    (?![\w.]*\d)(?!-[A-Z0-9])(?![a-zA-Z])            # not followed by more identifier
    """,
    re.VERBOSE,
)


def _float(text: str) -> float:
    return float(text.replace(",", ""))


def extract_numbers(text: str) -> list[Metric]:
    """Numbers in `text`, in order. `text` should be plain (see textutil.md_plain)."""
    out = []
    for m in _NUMBER.finditer(text):
        num, den, unit = m.group("num"), m.group("den"), (m.group("unit") or "").strip()
        value = _float(num) / _float(den) if den and _float(den) else _float(num)
        unit = m.group("cur") or unit
        is_year = not den and not unit and re.fullmatch(r"(19|20)\d\d", num) is not None
        out.append(Metric(text=m.group(0).strip(), value=value, unit=unit, kind="year" if is_year else "number"))
    return out


def tokens(text: str, years: bool = False) -> set[str]:
    """Comparable number tokens: digits without separators, K/M expanded; fractions stay whole."""
    out = set()
    for metric in extract_numbers(text):
        if metric.kind == "year" and not years:
            continue
        core = re.sub(r"[^\d./]", "", metric.text.replace(",", ""))
        scale = {"K": 1_000, "M": 1_000_000}.get(metric.unit)
        if scale and "/" not in core:
            core = f"{float(core) * scale:g}"
        out.add(core)
    return out


def _significant(token: str) -> int:
    return len(token.replace(".", "").lstrip("0"))


def is_strong(token: str) -> bool:
    """Specific enough to identify a result within one entry: a fraction, a decimal, or 3+ digits."""
    return "/" in token or "." in token or _significant(token) >= 3


def is_unique(token: str) -> bool:
    """Specific enough to identify a result anywhere: a 3+ digit decimal or a 4+ digit integer."""
    if "/" in token:
        return False
    return _significant(token) >= (3 if "." in token else 4)
