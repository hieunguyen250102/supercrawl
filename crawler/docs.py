"""Một lớp bọc chung cho hai kiểu trang.

- `HtmlDoc`: trang HTML thường, config dùng CSS selector.
- `JsonDoc`: SPA nhúng sẵn dữ liệu JSON trong HTML (Inertia.js, Next.js, Nuxt),
  config dùng đường dẫn trong JSON. Không cần chạy JavaScript vì dữ liệu đã
  nằm trong response đầu tiên.

Cả hai cùng bộ phương thức (text / texts / html / url / links / truthy) nên
novel.py và listing.py không cần biết trang thuộc kiểu nào.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from .clean import make_soup
from .config import SiteConfig
from .extract import html_of, links_of, select_all, text_of, texts_of, url_of

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


class HtmlDoc:
    """Config là CSS selector — hành vi y như trước khi có JsonDoc."""

    kind = "html"

    def __init__(self, html: str, url: str):
        self.soup = make_soup(html)
        self.url = url

    def text(self, spec: Any, default: str = "") -> str:
        return text_of(self.soup, _as_list(spec), default)

    def texts(self, spec: Any) -> list[str]:
        return texts_of(self.soup, _as_list(spec))

    def html(self, spec: Any) -> str | None:
        return html_of(self.soup, _as_list(spec))

    def url_of(self, spec: Any, base: str | None = None) -> str:
        return url_of(self.soup, _as_list(spec), base or self.url)

    def links(self, spec: Any, base: str | None = None) -> list[tuple[str, str]]:
        return links_of(self.soup, _as_list(spec), base or self.url)

    def truthy(self, spec: Any) -> bool:
        return bool(select_all(self.soup, _as_list(spec)))


class JsonDoc:
    r"""Config là đường dẫn trong JSON.

    Các dạng spec dùng được trong YAML:

        title: props.novel.title                    # đường dẫn thẳng
        genres: props.novel.tags[].name             # [] = duyệt cả list
        cover:  {tpl: "https://x.com/img/{props.novel.pic}"}
        chapter_links:
          list: props.novel.chapters                # list các object
          url:  "https://x.com/truyen/{@slug}/{id}" # {field} lấy từ từng item
          title: "{title}"                          # {@slug}/{@url} = trang hiện tại
        locked: {path: props.novel.content, empty: true}
    """

    kind = "json"

    def __init__(self, payload: dict, url: str):
        self.data = payload
        self.url = url

    @classmethod
    def from_html(cls, html: str, site: SiteConfig, url: str) -> "JsonDoc":
        selector = site.get("json.container", "#app@data-page")
        attr = None
        if "@" in selector:
            selector, attr = selector.rsplit("@", 1)
        soup = make_soup(html)
        node = soup.select_one(selector)
        if node is None:
            raise ValueError(f"Không tìm thấy khối JSON: {site.get('json.container')}")
        raw = node.get(attr) if attr else node.get_text()
        if not raw:
            raise ValueError(f"Khối JSON rỗng: {site.get('json.container')}")
        # Parser đã giải mã entity trong attribute rồi; chỉ unescape thêm khi
        # config nói rõ là cần (trường hợp JSON bị escape hai lớp).
        if site.get("json.unescape", False):
            raw = htmllib.unescape(raw)
        try:
            return cls(json.loads(raw), url)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Khối JSON không parse được ({exc}). "
                f"Thử đảo giá trị json.unescape trong config."
            ) from exc

    # -- đi trong JSON --------------------------------------------------
    def dig(self, path: str, root: Any = None) -> Any:
        """'a.b[].c' hoặc 'a.b.0.c'. Gặp [] thì trả về list các giá trị con."""
        current = self.data if root is None else root
        for part in str(path).split("."):
            if not part:
                continue
            wildcard = part.endswith("[]")
            if wildcard:
                part = part[:-2]
            if part:
                current = self._step(current, part)
            if wildcard:
                if not isinstance(current, list):
                    return None
                current = list(current)
            if current is None:
                return None
        return current

    @staticmethod
    def _step(node: Any, key: str) -> Any:
        if isinstance(node, list):
            if key.isdigit():
                index = int(key)
                return node[index] if index < len(node) else None
            # đang ở giữa một wildcard: lấy field đó của mọi phần tử
            return [item.get(key) if isinstance(item, dict) else None for item in node]
        if isinstance(node, dict):
            return node.get(key)
        return None

    def render(self, template: str, item: Any = None, strict: bool = False) -> str:
        """Thay {…} trong chuỗi. {@url}/{@slug} = trang hiện tại.

        strict=True: thiếu bất kỳ placeholder nào thì trả chuỗi rỗng, để người
        gọi bỏ qua item đó thay vì tạo ra URL cụt.
        """
        missing = False

        def swap(match: re.Match[str]) -> str:
            nonlocal missing
            key = match.group(1).strip()
            if key == "@url":
                return self.url
            if key == "@slug":
                return urlsplit(self.url).path.rstrip("/").rsplit("/", 1)[-1]
            value = self.dig(key, item) if item is not None else self.dig(key)
            if value is None and item is not None:
                value = self.dig(key)
            text = _flatten(value)
            if not text:
                missing = True
            return text

        rendered = _PLACEHOLDER.sub(swap, template)
        return "" if (strict and missing) else rendered

    # -- cùng interface với HtmlDoc --------------------------------------
    def _value(self, spec: Any, item: Any = None) -> Any:
        if spec is None:
            return None
        if isinstance(spec, (list, tuple)):
            for option in spec:
                value = self._value(option, item)
                if value not in (None, "", [], {}):
                    return value
            return None
        if isinstance(spec, dict):
            if "tpl" in spec:
                value = self.render(str(spec["tpl"]), item)
            elif "path" in spec:
                value = self.dig(str(spec["path"]), item)
            else:
                return None
            if "map" in spec:
                # đổi cờ số/boolean thành nhãn đọc được, ví dụ 1 -> "Hoàn thành"
                key = value if not isinstance(value, (list, dict)) else None
                value = spec["map"].get(key, spec.get("default"))
            if spec.get("unescape") and isinstance(value, str):
                # field chứa HTML nhưng đã bị escape thêm một lớp trong JSON
                value = htmllib.unescape(value)
            return value
        return self.dig(str(spec), item)

    def text(self, spec: Any, default: str = "") -> str:
        return _flatten(self._value(spec)) or default

    def texts(self, spec: Any) -> list[str]:
        value = self._value(spec)
        if value is None:
            return []
        if isinstance(value, list):
            return [t for t in (_flatten(v) for v in value) if t]
        text = _flatten(value)
        return [text] if text else []

    def html(self, spec: Any) -> str | None:
        value = self._value(spec)
        text = _flatten(value)
        return text or None

    def url_of(self, spec: Any, base: str | None = None) -> str:
        value = _flatten(self._value(spec))
        return urljoin(base or self.url, value) if value else ""

    def links(self, spec: Any, base: str | None = None) -> list[tuple[str, str]]:
        if not isinstance(spec, dict) or "list" not in spec:
            value = self._value(spec)
            items = value if isinstance(value, list) else []
            return [(urljoin(base or self.url, _flatten(v)), "") for v in items if v]

        items = self.dig(str(spec["list"]))
        if not isinstance(items, list):
            return []
        url_tpl = str(spec.get("url", "{url}"))
        title_tpl = str(spec.get("title", ""))
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in items:
            if spec.get("skip_when") and self._matches(spec["skip_when"], item):
                continue
            link = self.render(url_tpl, item, strict=True).strip()
            if not link or "{" in link:
                continue
            link = urljoin(base or self.url, link)
            if link in seen:
                continue
            seen.add(link)
            out.append((link, self.render(title_tpl, item).strip() if title_tpl else ""))
        return out

    def truthy(self, spec: Any) -> bool:
        if spec is None:
            return False
        if isinstance(spec, (list, tuple)):
            return any(self.truthy(s) for s in spec)
        if isinstance(spec, dict):
            return self._matches(spec, None)
        return bool(self._value(spec))

    def _matches(self, cond: dict, item: Any) -> bool:
        value = self.dig(str(cond["path"]), item) if "path" in cond else None
        if "equals" in cond:
            return value == cond["equals"]
        if cond.get("empty"):
            return value in (None, "", [], {})
        return bool(value)


def _flatten(value: Any) -> str:
    """JSON hay trả title dạng list 1 phần tử — ép về chuỗi cho gọn."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(p for p in (_flatten(v) for v in value) if p)
    if isinstance(value, dict):
        return ""
    return str(value)


def make_doc(site: SiteConfig, html: str, url: str) -> HtmlDoc | JsonDoc:
    if str(site.get("source", "html")).lower() == "json":
        return JsonDoc.from_html(html, site, url)
    return HtmlDoc(html, url)
