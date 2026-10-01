"""Crawl một URL danh sách truyện (thể loại, tìm kiếm, trang chủ...) có phân trang."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .browser import FetchError, Session
from .config import PageNav
from .docs import HtmlDoc, JsonDoc
from .naming import normalize_url
from .novel import crawl_novel
from .pager import iter_pages, next_url
from .state import SiteState

Doc = HtmlDoc | JsonDoc


async def crawl_listing(
    session: Session,
    out_dir: Path,
    url: str,
    state: SiteState,
    *,
    limit: int | None = None,
    chapters: int | None = None,
    max_pages: int | None = None,
    from_page: int | None = None,
    restart: bool = False,
    update: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Đi từng trang của danh sách, crawl từng truyện, lưu tiến độ sau mỗi trang."""
    site = session.site
    nav = site.listing_pages
    if max_pages:
        nav.max = max_pages

    spec = site.get("listing.novel_links")
    if not spec:
        raise FetchError(f"Config {site.name} thiếu listing.novel_links")

    entry = state.reset_listing(url) if restart else state.listing(url)
    if from_page is not None:
        entry["page"] = max(0, from_page - 1)
        entry["next_url"] = url
        entry["finished"] = False

    # Resume: site dùng url_pattern thì tính lại URL theo số trang,
    # site dùng 'trang sau' thì đi tiếp từ next_url đã lưu.
    start_url, skip_pages, page_offset = url, 0, 0
    if entry["page"] > 0 and not entry.get("finished"):
        if nav.url_pattern:
            skip_pages = entry["page"]
        elif entry.get("next_url") and normalize_url(entry["next_url"]) != normalize_url(url):
            start_url = entry["next_url"]
            page_offset = entry["page"]
        print(f"    (tiếp tục từ trang {entry['page'] + 1})")

    novels_crawled = 0
    novels_failed = 0

    async for page_no, page_url, doc in iter_pages(
        session, start_url, nav, from_page=skip_pages,
        wait_for=site.get("listing.wait_for"), verbose=verbose,
    ):
        real_page = page_no + page_offset
        found = doc.links(spec, page_url)
        print(f"\n--- Trang {real_page}: {len(found)} truyện — {page_url}")
        if not found:
            entry["finished"] = True
            state.save()
            break

        entry["novels_seen"] = entry.get("novels_seen", 0) + len(found)

        for novel_url, _title in found:
            if state.is_novel_done(url, novel_url) and not update:
                if verbose:
                    print(f"    (bỏ qua, đã xong) {novel_url}")
                continue
            try:
                result = await crawl_novel(
                    session, out_dir, novel_url,
                    update=update, limit=chapters, verbose=verbose,
                )
            except FetchError as exc:
                novels_failed += 1
                print(f"    ! Lỗi truyện {novel_url}: {exc}")
                continue
            novels_crawled += 1
            if not result["failed"]:
                state.mark_novel_done(url, novel_url)
            state.put_novel(
                novel_url,
                {
                    "title": result["title"],
                    "folder": result["folder"],
                    "chapters": result["chapters"],
                    "done": result["done"],
                    "from_listing": url,
                },
            )
            state.save()

            if limit and novels_crawled >= limit:
                _remember_page(entry, real_page, doc, page_url, nav)
                state.save()
                print(f"\n Đã đạt giới hạn {limit} truyện, dừng lại.")
                return _summary(novels_crawled, novels_failed, entry)

        _remember_page(entry, real_page, doc, page_url, nav)
        state.save()

    else:
        entry["finished"] = True
        state.save()

    return _summary(novels_crawled, novels_failed, entry)


def _remember_page(entry: dict[str, Any], page_no: int, doc: Doc, page_url: str, nav: PageNav) -> None:
    entry["page"] = page_no
    if nav.next:
        entry["next_url"] = next_url(doc, nav, page_url) or entry.get("next_url", "")
    else:
        entry["next_url"] = page_url


def _summary(crawled: int, failed: int, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "novels_crawled": crawled,
        "novels_failed": failed,
        "page": entry.get("page", 0),
        "finished": entry.get("finished", False),
    }
