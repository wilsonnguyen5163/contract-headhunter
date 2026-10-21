"""HTML to text, mojibake repair, Unicode and whitespace cleanup."""

import re
import unicodedata

import ftfy
from bs4 import BeautifulSoup

_HTML_HINT = re.compile(
    r"<\s*/?\s*(?:p|div|br|li|ul|ol|h[1-6]|span|strong|b|em|i|table|tr|td|section|a)\b[^>]*>",
    re.IGNORECASE,
)
_BLOCK_TAGS = [
    "p", "div", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "tr", "section", "article", "header", "footer", "blockquote",
]  # fmt: skip
_INLINE_SPACES = re.compile(r"[^\S\n]+")
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")
# A line holding only a bullet marker, then (after any blank lines) the item's text:
# "<li><p>Text</p></li>" in HTML, or a list copied from a web page.
_LONE_BULLET = re.compile(r"(?m)^([•·▪◦●*–—-])\n(?:\n)*(?=\S)")


def looks_like_html(text: str) -> bool:
    return bool(_HTML_HINT.search(text))


def html_to_text(html: str) -> str:
    """Block elements become line breaks and list items become "• " bullets."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for item in soup.find_all("li"):
        item.insert_before("\n• ")
        item.insert_after("\n")
    for block in soup.find_all(_BLOCK_TAGS):
        block.insert_before("\n")
        block.insert_after("\n")
    return soup.get_text()


def clean_text(text: str) -> str:
    """Fix mojibake, NFKC-normalize, collapse spaces, keep at most one blank line."""
    text = ftfy.fix_text(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_INLINE_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    joined = _LONE_BULLET.sub(r"\1 ", "\n".join(lines))  # "•" alone + next line -> "• text"
    return _EXTRA_BLANK_LINES.sub("\n\n", joined).strip()


def to_plain_text(raw: str) -> str:
    """Plain, clean text from posting text that may be HTML."""
    return clean_text(html_to_text(raw) if looks_like_html(raw) else raw)
