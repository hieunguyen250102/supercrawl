"""Lưu tiến độ: theo site (phân trang danh sách) và theo từng truyện (số chương)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import fsutil
from .naming import normalize_url, url_key

META_FILENAME = "_meta.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _write_json_atomic(path: Path, data: Any) -> None:
    """Ghi qua file tạm rồi replace: Ctrl+C giữa lúc ghi không làm hỏng state."""
    fsutil.makedirs(path.parent)
    fd, tmp = fsutil.mkstemp_in(path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        fsutil.replace(tmp, path)
    except BaseException:
        fsutil.unlink(tmp)
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    if not fsutil.is_file(path):
        return None
    try:
        return json.loads(fsutil.read_text(path))
    except (json.JSONDecodeError, OSError):
        backup = path.with_suffix(path.suffix + ".corrupt")
        try:
            fsutil.replace(path, backup)
            print(f"  ! State hỏng, đã đổi tên thành {backup.name}, bắt đầu lại từ đầu")
        except OSError:
            pass
        return None


class SiteState:
    """state/<site>.json — tiến độ phân trang của các URL danh sách + index truyện."""

    def __init__(self, path: Path, site_name: str):
        self.path = path
        self.data: dict[str, Any] = _read_json(path) or {
            "site": site_name,
            "listings": {},
            "novels": {},
        }
        self.data.setdefault("listings", {})
        self.data.setdefault("novels", {})

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        _write_json_atomic(self.path, self.data)

    # -- tiến độ danh sách (thể loại / tìm kiếm / trang chủ) -----------
    def listing(self, url: str) -> dict[str, Any]:
        key = url_key(url)
        entry = self.data["listings"].get(key)
        if entry is None:
            entry = {
                "url": url,
                "page": 0,          # trang đã xử lý xong gần nhất
                "next_url": url,    # trang sẽ tải tiếp
                "finished": False,
                "novels_seen": 0,
                "novels_done": [],  # URL truyện đã crawl xong từ danh sách này
            }
            self.data["listings"][key] = entry
        entry.setdefault("novels_done", [])
        return entry

    def reset_listing(self, url: str) -> dict[str, Any]:
        self.data["listings"].pop(url_key(url), None)
        return self.listing(url)

    def mark_novel_done(self, listing_url: str, novel_url: str) -> None:
        entry = self.listing(listing_url)
        norm = normalize_url(novel_url)
        if norm not in entry["novels_done"]:
            entry["novels_done"].append(norm)

    def is_novel_done(self, listing_url: str, novel_url: str) -> bool:
        return normalize_url(novel_url) in self.listing(listing_url)["novels_done"]

    # -- index truyện của site -----------------------------------------
    def put_novel(self, novel_url: str, info: dict[str, Any]) -> None:
        self.data["novels"][url_key(novel_url)] = {
            "url": novel_url,
            **info,
            "updated_at": now_iso(),
        }

    def novels(self) -> list[dict[str, Any]]:
        return list(self.data["novels"].values())


class NovelMeta:
    """<thư mục truyện>/_meta.json — danh sách chương + chương nào đã tải xong."""

    def __init__(self, folder: Path, url: str = "", site: str = ""):
        self.folder = folder
        self.path = folder / META_FILENAME
        loaded = _read_json(self.path) or {}
        self._loaded_before = bool(loaded)
        self.data: dict[str, Any] = {
            "url": url or loaded.get("url", ""),
            "site": site or loaded.get("site", ""),
            "title": loaded.get("title", ""),
            "author": loaded.get("author", ""),
            "status": loaded.get("status", ""),
            "genres": loaded.get("genres", []),
            "description": loaded.get("description", ""),
            "cover": loaded.get("cover", ""),
            "cover_file": loaded.get("cover_file", ""),
            "chapter_pages_done": loaded.get("chapter_pages_done", 0),
            "chapters": loaded.get("chapters", []),
            "created_at": loaded.get("created_at", now_iso()),
        }

    @property
    def existed(self) -> bool:
        return self._loaded_before

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        self.data["chapter_count"] = len(self.data["chapters"])
        self.data["done_count"] = self.done_count
        _write_json_atomic(self.path, self.data)

    # -- danh sách chương ----------------------------------------------
    @property
    def chapters(self) -> list[dict[str, Any]]:
        return self.data["chapters"]

    @property
    def done_count(self) -> int:
        return sum(1 for c in self.chapters if c.get("done"))

    def chapter_urls(self) -> set[str]:
        return {normalize_url(c["url"]) for c in self.chapters if c.get("url")}

    def merge_chapters(self, found: list[tuple[str, str]]) -> int:
        """Thêm chương mới vào cuối danh sách, giữ index cũ. Trả về số chương mới."""
        known = self.chapter_urls()
        added = 0
        for url, title in found:
            norm = normalize_url(url)
            if norm in known:
                continue
            known.add(norm)
            self.chapters.append(
                {
                    "index": len(self.chapters) + 1,
                    "title": title,
                    "url": url,
                    "file": "",
                    "done": False,
                }
            )
            added += 1
        return added

    def pending(self) -> list[dict[str, Any]]:
        """Chương chưa tải, hoặc đã đánh done nhưng file bị xoá tay."""
        out = []
        for chapter in self.chapters:
            if not chapter.get("done"):
                out.append(chapter)
                continue
            name = chapter.get("file")
            if not name or not fsutil.is_file(self.folder / name):
                chapter["done"] = False
                out.append(chapter)
        return out

    def mark_done(self, chapter: dict[str, Any], filename: str) -> None:
        chapter["file"] = filename
        chapter["done"] = True
        chapter.pop("error", None)

    def mark_error(self, chapter: dict[str, Any], message: str) -> None:
        chapter["done"] = False
        chapter["error"] = message[:300]
