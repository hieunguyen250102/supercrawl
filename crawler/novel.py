"""Crawl một truyện: metadata, ảnh bìa, danh sách chương, nội dung từng chương."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import fsutil
from .browser import FetchError, Session
from .clean import extract_content
from .config import SiteConfig
from .docs import HtmlDoc, JsonDoc, make_doc
from .naming import chapter_filename, normalize_url, safe_name, url_key, url_slug
from .pager import iter_pages
from .state import NovelMeta

Doc = HtmlDoc | JsonDoc

SAVE_EVERY = 10  # số chương tải xong thì ghi _meta.json một lần
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


async def crawl_novel(
    session: Session,
    out_dir: Path,
    url: str,
    *,
    update: bool = False,
    refresh_list: bool = False,
    limit: int | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Tải (hoặc cập nhật) một truyện vào out_dir/<tên truyện>/."""
    site = session.site
    doc = await _load(session, url, site.get("novel.wait_for"))

    title = doc.text(site.get("novel.title")) or url_slug(url)
    folder = _pick_folder(out_dir, title, url)
    fsutil.makedirs(folder)

    meta = NovelMeta(folder, url=url, site=site.name)
    _fill_metadata(meta, doc, site, url, title)

    print(f"\n=== {title}")
    print(f"    {url}")
    print(f"    -> {folder}")

    await _save_cover(session, meta, folder, verbose=verbose)

    # Danh sách chương: chỉ tải lại khi chưa có, khi update, hoặc khi bị ép refresh.
    if not meta.chapters or update or refresh_list:
        added = await _collect_chapters(session, site, url, doc, meta, verbose=verbose)
        if meta.existed and added:
            print(f"    + {added} chương mới")
    else:
        print(f"    (dùng danh sách chương đã lưu: {len(meta.chapters)} chương)")

    meta.save()
    _write_info(meta, folder)

    todo = meta.pending()
    if limit:
        todo = todo[:limit]
    total = len(meta.chapters)
    if not todo:
        print(f"    Đã đủ {total}/{total} chương, không có gì để tải.")
        return _result(title, folder, meta, 0, 0)

    print(f"    Cần tải {len(todo)} / {total} chương (đã có {meta.done_count})")
    ok, fail = await _download_chapters(session, site, meta, folder, todo, verbose=verbose)
    meta.save()
    _write_info(meta, folder)

    print(f"    Xong: +{ok} chương" + (f", lỗi {fail}" if fail else ""))
    return _result(title, folder, meta, ok, fail)


async def _load(session: Session, url: str, wait_for: str | None = None) -> Doc:
    try:
        return make_doc(session.site, await session.html(url, wait_for=wait_for), url)
    except ValueError as exc:  # khối JSON không đúng như config mô tả
        raise FetchError(str(exc)) from exc


def _pick_folder(out_dir: Path, title: str, url: str) -> Path:
    """Chọn thư mục cho truyện này.

    Hai truyện khác nhau có thể trùng tên (bản re-up, cùng tên khác tác giả).
    Thư mục nào đã có _meta.json của URL khác thì tránh, thêm slug/hash cho khỏi
    trộn chương của hai truyện vào cùng một chỗ.
    """
    base = safe_name(title)
    candidates = [
        out_dir / base,
        out_dir / safe_name(f"{base} ({url_slug(url)})"),
        out_dir / safe_name(f"{base} ({url_key(url)})"),
    ]
    for candidate in candidates:
        probe = NovelMeta(candidate)
        if not probe.existed:
            return candidate
        if normalize_url(probe.data.get("url", "")) == normalize_url(url):
            return candidate
    return candidates[-1]


def _result(title: str, folder: Path, meta: NovelMeta, ok: int, fail: int) -> dict[str, Any]:
    return {
        "title": title,
        "folder": str(folder),
        "chapters": len(meta.chapters),
        "done": meta.done_count,
        "downloaded": ok,
        "failed": fail,
    }


# -- metadata -----------------------------------------------------------
def _fill_metadata(meta: NovelMeta, doc: Doc, site: SiteConfig, url: str, title: str) -> None:
    meta.data["title"] = title
    meta.data["url"] = url
    meta.data["site"] = site.name
    for field in ("author", "status"):
        value = doc.text(site.get(f"novel.{field}"))
        if value:
            meta.data[field] = value
    genres = doc.texts(site.get("novel.genres"))
    if genres:
        meta.data["genres"] = genres
    description = doc.html(site.get("novel.description"))
    if description:
        meta.data["description"] = extract_content(description)
    cover = doc.url_of(site.get("novel.cover"), url)
    if cover:
        meta.data["cover"] = cover


async def _save_cover(session: Session, meta: NovelMeta, folder: Path, *, verbose: bool) -> None:
    cover_url = meta.data.get("cover")
    if not cover_url:
        return
    existing = meta.data.get("cover_file")
    if existing and fsutil.is_file(folder / existing):
        return
    ext = Path(urlsplit(cover_url).path).suffix.lower()
    if ext not in IMAGE_EXTS:
        ext = ".jpg"
    dest = folder / f"cover{ext}"
    if await session.download(cover_url, dest):
        meta.data["cover_file"] = dest.name
        print(f"    Ảnh bìa: {dest.name}")
    elif verbose:
        print(f"    ! Không tải được ảnh bìa: {cover_url}")


def _write_info(meta: NovelMeta, folder: Path) -> None:
    d = meta.data
    lines = [
        d.get("title", ""),
        "=" * max(4, len(d.get("title", ""))),
        "",
        f"Tác giả   : {d.get('author') or '?'}",
        f"Trạng thái: {d.get('status') or '?'}",
        f"Thể loại  : {', '.join(d.get('genres') or []) or '?'}",
        f"Nguồn     : {d.get('url', '')}",
        f"Site      : {d.get('site', '')}",
        f"Số chương : {meta.done_count} / {len(meta.chapters)} đã tải",
        f"Cập nhật  : {d.get('updated_at', '')}",
        "",
        "Giới thiệu",
        "----------",
        d.get("description") or "(không có)",
        "",
    ]
    fsutil.write_text(folder / "info.txt", "\n".join(lines))


# -- danh sách chương ---------------------------------------------------
async def _collect_chapters(
    session: Session,
    site: SiteConfig,
    novel_url: str,
    first_doc: Doc,
    meta: NovelMeta,
    *,
    verbose: bool,
) -> int:
    """Quét toàn bộ danh sách chương (có phân trang) rồi merge vào meta."""
    spec = site.get("novel.chapter_links")
    if not spec:
        raise FetchError(f"Config {site.name} thiếu novel.chapter_links")

    nav = site.chapter_pages
    collected: list[tuple[str, str]] = list(first_doc.links(spec, novel_url))
    pages_done = 1

    if nav.paginated:
        seen = {u for u, _ in collected}
        async for page_no, page_url, doc in iter_pages(
            session, novel_url, nav, from_page=1,
            wait_for=site.get("novel.wait_for"), verbose=verbose,
        ):
            found = doc.links(spec, page_url)
            fresh = [(u, t) for u, t in found if u not in seen]
            if not fresh:
                if verbose:
                    print(f"    trang chương {page_no}: không có link mới, dừng")
                break
            seen.update(u for u, _ in fresh)
            collected.extend(fresh)
            pages_done = page_no
            print(f"    danh sách chương: trang {page_no}, tổng {len(collected)} link")

    if str(site.get("novel.chapter_order", "asc")).lower() in ("desc", "reverse", "newest"):
        collected.reverse()

    meta.data["chapter_pages_done"] = pages_done
    added = meta.merge_chapters(collected)
    if not meta.chapters:
        raise FetchError(
            f"Không tìm thấy chương nào — kiểm tra lại novel.chapter_links trong {site.path}"
        )
    return added


# -- nội dung chương ----------------------------------------------------
async def _download_chapters(
    session: Session,
    site: SiteConfig,
    meta: NovelMeta,
    folder: Path,
    todo: list[dict[str, Any]],
    *,
    verbose: bool,
) -> tuple[int, int]:
    done = failed = 0
    locked_warned = False
    lock = asyncio.Lock()

    async def worker(chapter: dict[str, Any]) -> None:
        nonlocal done, failed, locked_warned
        try:
            filename = await _fetch_chapter(session, site, folder, chapter)
        except FetchError as exc:
            async with lock:
                failed += 1
                meta.mark_error(chapter, str(exc))
                print(f"    ! Chương {chapter['index']}: {exc}")
                if "đăng nhập" in str(exc) and not locked_warned:
                    locked_warned = True
                    print(f"      -> Chạy: python -m crawler login {site.name}")
            return
        async with lock:
            done += 1
            meta.mark_done(chapter, filename)
            if verbose or done % 20 == 0 or done == len(todo):
                print(f"    [{done + failed}/{len(todo)}] chương {chapter['index']}: {filename}")
            if done % SAVE_EVERY == 0:
                meta.save()

    # Semaphore trong Session đã giới hạn số request đồng thời.
    await asyncio.gather(*(worker(c) for c in todo))
    return done, failed


async def _fetch_chapter(
    session: Session,
    site: SiteConfig,
    folder: Path,
    chapter: dict[str, Any],
) -> str:
    url = chapter["url"]
    doc = await _load(session, url, site.get("chapter.wait_for"))

    locked = site.get("chapter.locked")
    if locked and doc.truthy(locked):
        raise FetchError("chương bị khoá, cần đăng nhập")

    body_html = doc.html(site.get("chapter.content"))
    if not body_html:
        raise FetchError("không khớp chapter.content")

    page_title = (
        doc.text(site.get("chapter.title"))
        or chapter.get("title")
        or f"Chương {chapter['index']}"
    )
    text = extract_content(
        body_html,
        remove_selectors=site.sels("chapter.remove"),
        strip_lines=site.sels("chapter.strip_lines"),
        drop_titles=[page_title, chapter.get("title", "")],
    )

    min_len = int(site.get("chapter.min_length", 50))
    if len(text) < min_len:
        raise FetchError(f"nội dung chỉ {len(text)} ký tự (có thể là chương VIP, cần đăng nhập)")

    title = _tidy_title(page_title, chapter["index"])
    filename = chapter_filename(chapter["index"], title)
    fsutil.write_text(folder / filename, f"{title}\n\n{text}\n")
    chapter["title"] = title
    return filename


def _tidy_title(title: str, index: int) -> str:
    title = re.sub(r"\s+", " ", title).strip(" -–—:")
    return title or f"Chương {index}"
