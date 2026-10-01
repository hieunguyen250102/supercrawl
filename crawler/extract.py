"""Áp selector trong config lên HTML đã tải."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from .clean import make_soup

# "div.book img@src" -> query "div.book img", attr "src"
_ATTR_TAIL = re.compile(r"@([\w:-]+)$")


def parse_selector(raw: str) -> tuple[str, str | None]:
    match = _ATTR_TAIL.search(raw.strip())
    if not match:
        return raw.strip(), None
    return raw[: match.start()].strip(), match.group(1)


def select_all(soup: BeautifulSoup | Tag, selectors: list[str]) -> list[tuple[Tag, str | None]]:
    """Thử lần lượt các selector, trả về kết quả của selector đầu tiên khớp."""
    for raw in selectors:
        query, attr = parse_selector(raw)
        if not query:
            continue
        try:
            found = soup.select(query)
        except Exception:
            # Selector sai trong config không được làm gãy cả lượt crawl.
            continue
        if found:
            return [(node, attr) for node in found]
    return []


def select_one(soup: BeautifulSoup | Tag, selectors: list[str]) -> tuple[Tag, str | None] | None:
    found = select_all(soup, selectors)
    return found[0] if found else None


def node_value(node: Tag, attr: str | None) -> str:
    """Lấy attribute nếu selector chỉ định @attr, không thì lấy text."""
    if attr:
        value = node.get(attr)
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        if not value and attr == "src":
            # Site dùng lazy-load: ảnh thật nằm ở data-src / data-original.
            for alt in ("data-src", "data-original", "data-lazy-src", "srcset"):
                value = node.get(alt)
                if value:
                    break
            if isinstance(value, str) and "," in value:
                value = value.split(",")[0].strip().split(" ")[0]
        return (value or "").strip()
    return re.sub(r"\s+", " ", node.get_text(" ")).strip()


def text_of(soup: BeautifulSoup | Tag, selectors: list[str], default: str = "") -> str:
    hit = select_one(soup, selectors)
    return node_value(*hit) if hit else default


def texts_of(soup: BeautifulSoup | Tag, selectors: list[str]) -> list[str]:
    return [v for v in (node_value(n, a) for n, a in select_all(soup, selectors)) if v]


def html_of(soup: BeautifulSoup | Tag, selectors: list[str]) -> str | None:
    """innerHTML của khối đầu tiên khớp — dùng cho nội dung chương."""
    hit = select_one(soup, selectors)
    return hit[0].decode_contents() if hit else None


def url_of(soup: BeautifulSoup | Tag, selectors: list[str], base_url: str) -> str:
    """Selector không chỉ rõ @attr thì mặc định lấy href (thẻ a) / src (thẻ img)."""
    hit = select_one(soup, selectors)
    if not hit:
        return ""
    node, attr = hit
    if attr is None:
        attr = "src" if node.name in ("img", "image", "source") else "href"
    value = node_value(node, attr)
    return urljoin(base_url, value) if value else ""


def links_of(soup: BeautifulSoup | Tag, selectors: list[str], base_url: str) -> list[tuple[str, str]]:
    """Trả về [(url tuyệt đối, text)] đã dedupe, giữ nguyên thứ tự xuất hiện."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for node, attr in select_all(soup, selectors):
        href = node_value(node, attr or "href")
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        title = re.sub(r"\s+", " ", node.get("title") or node.get_text(" ")).strip()
        out.append((absolute, title))
    return out


def next_page_url(soup: BeautifulSoup | Tag, selectors: list[str], base_url: str) -> str:
    url = url_of(soup, selectors, base_url)
    return url if url and url != base_url else ""


def parse(html: str) -> BeautifulSoup:
    return make_soup(html)
