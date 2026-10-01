r"""Đọc/ghi file chịu được đường dẫn dài trên Windows.

Windows chặn đường dẫn quá 260 ký tự. Tên truyện + tên chương tiếng Việt rất dễ
vượt mức đó, nên mọi thao tác file của tool đi qua đây: khi path dài, ta thêm
tiền tố \\?\ để dùng API đường dẫn mở rộng.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

LONG_PATH_THRESHOLD = 230  # dưới mức này giữ path nguyên bản cho dễ đọc log
_PREFIX = "\\\\?\\"


def raw(path: Path | str) -> str:
    """Đường dẫn dạng string mà os/open dùng được, kể cả khi rất dài."""
    text = str(path)
    if os.name != "nt" or text.startswith(_PREFIX):
        return text
    if len(text) < LONG_PATH_THRESHOLD and not os.path.isabs(text):
        return text
    absolute = os.path.abspath(text)
    if len(absolute) < LONG_PATH_THRESHOLD:
        return absolute
    if absolute.startswith("\\\\"):  # UNC: \\server\share -> \\?\UNC\server\share
        return _PREFIX + "UNC" + absolute[1:]
    return _PREFIX + absolute


def force_long(path: Path | str) -> str:
    r"""Luôn trả về dạng \\?\ — cần cho thao tác đệ quy (rmtree) vì các file bên
    trong có thể dài hơn 260 ký tự dù thư mục gốc thì không."""
    text = os.path.abspath(str(path))
    if os.name != "nt" or text.startswith(_PREFIX):
        return text
    if text.startswith("\\\\"):
        return _PREFIX + "UNC" + text[1:]
    return _PREFIX + text


def makedirs(path: Path | str) -> None:
    os.makedirs(raw(path), exist_ok=True)


def rmtree(path: Path | str, ignore_errors: bool = True) -> None:
    shutil.rmtree(force_long(path), ignore_errors=ignore_errors)


def is_file(path: Path | str) -> bool:
    return os.path.isfile(raw(path))


def write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    with open(raw(path), "w", encoding=encoding, errors="replace") as fh:
        fh.write(text)


def write_bytes(path: Path | str, data: bytes) -> None:
    with open(raw(path), "wb") as fh:
        fh.write(data)


def read_text(path: Path | str, encoding: str = "utf-8") -> str:
    with open(raw(path), "r", encoding=encoding, errors="replace") as fh:
        return fh.read()


def unlink(path: Path | str) -> None:
    try:
        os.remove(raw(path))
    except FileNotFoundError:
        pass


def replace(src: Path | str, dst: Path | str) -> None:
    os.replace(raw(src), raw(dst))


def mkstemp_in(directory: Path | str, suffix: str = ".tmp") -> tuple[int, str]:
    return tempfile.mkstemp(dir=raw(directory), suffix=suffix)


def listdir(path: Path | str) -> list[str]:
    try:
        return os.listdir(raw(path))
    except FileNotFoundError:
        return []
