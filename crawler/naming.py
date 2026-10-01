"""Sinh tên file/thư mục an toàn trên Windows và key ổn định cho state."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

_ILLEGAL = re.compile(r'[<>:"/\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")
# Tên bị Windows cấm dùng làm tên file.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_name(text: str, max_len: int = 120, fallback: str = "khong-ten") -> str:
    """Biến tiêu đề bất kỳ thành một thành phần đường dẫn hợp lệ."""
    text = unicodedata.normalize("NFC", text or "")
    text = _ILLEGAL.sub(" ", text)
    text = _WS.sub(" ", text).strip(" .")
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .")
    if text.upper() in _RESERVED:
        text = f"_{text}"
    return text or fallback


def chapter_filename(index: int, title: str, width: int = 4, ext: str = ".txt") -> str:
    """Ví dụ: '0012 - Chương 12 Bí mật dưới hồ.txt' — sắp xếp đúng theo tên."""
    name = safe_name(title, max_len=100, fallback=f"chuong-{index}")
    return f"{index:0{width}d} - {name}{ext}"


def normalize_url(url: str) -> str:
    """Bỏ fragment và trailing slash để so sánh/dedupe URL."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def url_key(url: str) -> str:
    """Key ngắn, ổn định để lưu trong file state."""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def url_slug(url: str) -> str:
    """Phần slug cuối của URL, dùng khi site không cho lấy được tiêu đề."""
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "truyen"
