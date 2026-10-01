"""Chuyển HTML nội dung chương thành text sạch."""

from __future__ import annotations

import re
import unicodedata

from bs4 import BeautifulSoup, NavigableString

# Thẻ không bao giờ là nội dung truyện.
JUNK_TAGS = ["script", "style", "noscript", "iframe", "ins", "svg", "form", "button", "audio", "video"]
# Thẻ tạo khối -> cần xuống dòng khi ép về text.
BLOCK_TAGS = ["p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote", "section"]

_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿]")
_MULTI_BLANK = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t ]{2,}")

# Rác gặp ở hầu hết site truyện, áp dụng cho mọi config.
DEFAULT_STRIP_LINES = [
    r"(?i)^\s*(nguồn|source)\s*[:：]",
    r"(?i)^\s*(quảng cáo|advertisement)\s*$",
    r"(?i)^\s*(bạn đang đọc|truyện được (đăng|copy)|đọc truyện (hay|online) tại)\b",
    r"(?i)^\s*(converter|convert(er)? by|edit(or)? by|beta by)\s*[:：]?",
    r"(?i)^\s*(chương (trước|sau)|trang chủ|báo lỗi chương)\s*$",
]


def make_soup(html: str) -> BeautifulSoup:
    """Dùng lxml nếu có (nhanh hơn), không thì parser built-in."""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def strip_junk(soup: BeautifulSoup, remove_selectors: list[str]) -> None:
    for tag in soup.find_all(JUNK_TAGS):
        tag.decompose()
    for selector in remove_selectors:
        try:
            for node in soup.select(selector):
                node.decompose()
        except Exception:
            # Selector sai trong config không được làm gãy cả lượt crawl.
            continue


def to_text(node) -> str:
    """Ép một node HTML về text, giữ ngắt dòng theo cấu trúc khối."""
    for br in node.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for tag in node.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString("\n"))
        tag.insert_after(NavigableString("\n"))
    return node.get_text()


def clean_text(
    text: str,
    strip_lines: list[str] | None = None,
    drop_titles: list[str] | None = None,
    use_defaults: bool = True,
) -> str:
    """Chuẩn hoá unicode, xoá dòng rác, gom dòng trống."""
    patterns: list[re.Pattern[str]] = []
    for raw in (DEFAULT_STRIP_LINES if use_defaults else []) + list(strip_lines or []):
        try:
            patterns.append(re.compile(raw))
        except re.error:
            continue

    # Tiêu đề chương thường bị lặp ở dòng đầu nội dung -> bỏ để khỏi trùng.
    titles = {_norm_cmp(t) for t in (drop_titles or []) if t and t.strip()}

    text = unicodedata.normalize("NFC", text)
    text = _ZERO_WIDTH.sub("", text).replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")

    kept: list[str] = []
    for line in text.split("\n"):
        line = _MULTI_SPACE.sub(" ", line).strip()
        if any(p.search(line) for p in patterns):
            continue
        if titles and not any(k.strip() for k in kept) and _norm_cmp(line) in titles:
            continue
        kept.append(line)

    out = _MULTI_BLANK.sub("\n\n", "\n".join(kept)).strip()
    return out


def _norm_cmp(text: str) -> str:
    return _MULTI_SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip().lower().strip(" :.-–—")


def extract_content(
    html: str,
    remove_selectors: list[str] | None = None,
    strip_lines: list[str] | None = None,
    drop_titles: list[str] | None = None,
) -> str:
    """Pipeline đầy đủ: HTML của khối nội dung -> text sạch."""
    soup = make_soup(html)
    strip_junk(soup, remove_selectors or [])
    return clean_text(to_text(soup), strip_lines=strip_lines, drop_titles=drop_titles)
