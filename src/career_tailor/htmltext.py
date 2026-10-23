"""Turn job-posting HTML into readable Markdown-ish text: headings, bullets, paragraphs."""

import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "section", "article", "ul", "ol", "table", "tr", "br", "hr"}
_HEADING = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def _newline(self, n: int = 1) -> None:
        if self.out and self.out[-1] == "- ":
            return  # a paragraph inside a list item stays on the bullet's line
        self.out.append("\n" * n)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in _HEADING:
            self._newline(2)
            self.out.append("## ")
        elif tag == "li":
            self._newline()
            self.out.append("- ")
        elif tag in _BLOCK:
            self._newline(2 if tag != "br" else 1)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        elif tag in _HEADING or tag in _BLOCK or tag == "li":
            self._newline()

    def handle_data(self, data):
        if self.skip:
            return
        text = re.sub(r"\s+", " ", data)
        if self.out and self.out[-1] == "- ":
            text = text.lstrip()
        if text:
            self.out.append(text)


def html_to_text(markup: str) -> str:
    """Plain text from HTML. Handles double-escaped HTML (Greenhouse sends `&lt;p&gt;`)."""
    if "&lt;" in markup and "<" not in markup.replace("&lt;", ""):
        markup = html.unescape(markup)
    parser = _Text()
    parser.feed(markup)
    lines = [ln.strip() for ln in "".join(parser.out).split("\n")]
    lines = [re.sub(r"^- ?-\s*", "- ", ln) for ln in lines]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^## \s*$\n?", "", text, flags=re.M)
    text = re.sub(r"(?m)^(- .*)\n\n(?=- )", r"\1\n", text)  # keep list items together
    return text.strip() + "\n"
