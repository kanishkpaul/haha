"""Small text helpers shared across stages."""

import re

_LINK = re.compile(r"\[([^\]]*)\]\((?:<[^>]*>|[^)]*)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_CODE = re.compile(r"`([^`]*)`")
_URL = re.compile(r"\((https?://[^)\s]+)\)|<(https?://[^>\s]+)>")


def md_plain(text: str) -> str:
    """Strip inline Markdown (links, bold, italics, code) and collapse whitespace."""
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _CODE.sub(r"\1", text)
    return " ".join(text.split())


def md_links(text: str) -> list[str]:
    """URLs of Markdown links in `text`, in order."""
    return [a or b for a, b in _URL.findall(text)]
