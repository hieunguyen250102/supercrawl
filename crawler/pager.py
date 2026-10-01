"""Đi qua một danh sách phân trang: theo 'trang sau' hoặc theo URL pattern."""

from __future__ import annotations

from typing import AsyncIterator

from .browser import FetchError, Session
from .config import PageNav
from .docs import HtmlDoc, JsonDoc, make_doc
from .naming import normalize_url

Doc = HtmlDoc | JsonDoc


def next_url(doc: Doc, nav: PageNav, current: str) -> str:
    """URL trang sau theo config, rỗng nếu không có hoặc trỏ về chính nó."""
    if not nav.next:
        return ""
    found = doc.url_of(nav.next, current)
    return found if found and normalize_url(found) != normalize_url(current) else ""


async def iter_pages(
    session: Session,
    start_url: str,
    nav: PageNav,
    *,
    from_page: int = 0,
    wait_for: str | None = None,
    verbose: bool = False,
) -> AsyncIterator[tuple[int, str, Doc]]:
    """Sinh ra (số trang, url, doc).

    Người gọi tự quyết định dừng (break) khi một trang không còn item mới —
    đó là cách duy nhất biết đã hết trang khi site dùng url_pattern.

    from_page: bỏ qua các trang <= số này (dùng khi resume).
    """
    page_no = max(nav.start, from_page + 1)
    base = start_url.rstrip("/")
    url = start_url if page_no == nav.start else _build(nav, base, page_no)
    seen: set[str] = set()
    limit = nav.start + nav.max - 1

    while url and page_no <= limit:
        key = normalize_url(url)
        if key in seen:
            if verbose:
                print(f"    trang {page_no}: URL lặp lại, dừng")
            return
        seen.add(key)

        try:
            doc = make_doc(session.site, await session.html(url, wait_for=wait_for), url)
        except (FetchError, ValueError) as exc:
            print(f"    ! Bỏ qua trang {page_no}: {exc}")
            return

        yield page_no, url, doc

        page_no += 1
        if nav.url_pattern:
            url = _build(nav, base, page_no)
        elif nav.next:
            url = next_url(doc, nav, url)
        else:
            return  # danh sách không phân trang


def _build(nav: PageNav, base: str, page_no: int) -> str:
    try:
        return nav.url_pattern.format(url=base, page=page_no, base=base)
    except (KeyError, IndexError, AttributeError):
        return ""
