"""Nạp và tra cứu config của từng site (sites/*.yml)."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

# Giá trị mặc định, site config chỉ cần ghi đè phần khác biệt.
DEFAULTS: dict[str, Any] = {
    "render": False,
    "fetch": {
        "concurrency": 3,
        "delay": [0.6, 1.4],
        "timeout": 45000,
        "retries": 3,
        "wait_until": "domcontentloaded",
        "wait_for": None,
        "block_resources": True,
    },
    "listing": {
        "pages": {"next": None, "url_pattern": None, "max": 200, "start": 1},
    },
    "novel": {
        "chapter_order": "asc",
        "chapter_pages": {"next": None, "url_pattern": None, "max": 300, "start": 1},
    },
    "chapter": {
        "remove": [],
        "strip_lines": [],
        "min_length": 50,
    },
    "auth": {},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class PageNav:
    """Mô tả cách sang trang tiếp theo của một danh sách phân trang."""

    def __init__(self, data: dict[str, Any]):
        # Giữ nguyên dạng trong YAML: CSS selector (str/list) với site HTML,
        # hoặc đường dẫn JSON với site kiểu SPA.
        self.next: Any = data.get("next")
        self.url_pattern: str | None = data.get("url_pattern")
        self.max: int = int(data.get("max") or 1)
        self.start: int = int(data.get("start") or 1)

    @property
    def paginated(self) -> bool:
        return bool(self.next or self.url_pattern)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


class SiteConfig:
    def __init__(self, name: str, data: dict[str, Any], path: Path | None = None):
        self.name = name
        self.data = _deep_merge(DEFAULTS, data or {})
        self.path = path

    # -- tra cứu chung -------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur if cur is not None else default

    def sels(self, dotted: str) -> list[str]:
        """Trả về danh sách selector (config cho phép ghi 1 string hoặc list)."""
        return _as_list(self.get(dotted))

    # -- các phần hay dùng ---------------------------------------------
    @property
    def domains(self) -> list[str]:
        return [d.lower() for d in self.sels("match")]

    @property
    def render(self) -> bool:
        return bool(self.get("render"))

    @property
    def listing_pages(self) -> PageNav:
        return PageNav(self.get("listing.pages", {}) or {})

    @property
    def chapter_pages(self) -> PageNav:
        return PageNav(self.get("novel.chapter_pages", {}) or {})

    def matches_domain(self, url: str) -> bool:
        host = (urlparse(url).netloc or "").lower()
        if not host:
            return False
        host = host.split(":")[0]
        return any(host == d or host.endswith("." + d) or d in host for d in self.domains)

    def url_kind(self, url: str) -> str | None:
        """'novel' | 'listing' | None — dựa trên regex detect trong config."""
        for kind, key in (("novel", "detect.novel_url"), ("listing", "detect.listing_url")):
            for pattern in self.sels(key):
                if re.search(pattern, url):
                    return kind
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SiteConfig {self.name} domains={self.domains}>"


class SiteRegistry:
    def __init__(self, sites: list[SiteConfig]):
        self.sites = sites

    @classmethod
    def load(cls, sites_dir: Path) -> "SiteRegistry":
        sites: list[SiteConfig] = []
        if not sites_dir.is_dir():
            raise FileNotFoundError(f"Không thấy thư mục config site: {sites_dir}")
        for path in sorted(sites_dir.glob("*.y*ml")):
            if path.name.startswith("_"):  # _template.yml, _notes.yml ...
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            name = data.get("name") or path.stem
            sites.append(SiteConfig(name, data, path))
        return cls(sites)

    def by_name(self, name: str) -> SiteConfig:
        for site in self.sites:
            if site.name.lower() == name.lower():
                return site
        known = ", ".join(s.name for s in self.sites) or "(trống)"
        raise KeyError(f"Chưa có config cho site '{name}'. Hiện có: {known}")

    def for_url(self, url: str) -> SiteConfig:
        for site in self.sites:
            if site.matches_domain(url):
                return site
        host = urlparse(url).netloc
        raise KeyError(
            f"Chưa có config cho domain '{host}'. "
            f"Copy sites/_template.yml thành sites/{host.split('.')[0]}.yml rồi điền selector."
        )

    def resolve(self, url: str, site_name: str | None = None) -> SiteConfig:
        return self.by_name(site_name) if site_name else self.for_url(url)
