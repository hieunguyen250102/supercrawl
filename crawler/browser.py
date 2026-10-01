"""Playwright: profile giữ đăng nhập, tải trang có rate limit, retry, song song."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PWError
from playwright.async_api import Page, TimeoutError as PWTimeout, async_playwright

from . import fsutil
from .config import SiteConfig

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BLOCKED_TYPES = {"image", "media", "font"}


class FetchError(RuntimeError):
    pass


class Session:
    """Một browser context bền (profile riêng cho từng site) + fetcher."""

    def __init__(
        self,
        site: SiteConfig,
        profiles_dir: Path,
        *,
        headless: bool = True,
        render: bool | None = None,
        concurrency: int | None = None,
        delay: tuple[float, float] | None = None,
        verbose: bool = False,
    ):
        self.site = site
        self.profile_dir = profiles_dir / site.name
        self.headless = headless
        self.render = site.render if render is None else render
        self.concurrency = max(1, int(concurrency or site.get("fetch.concurrency", 3)))
        raw_delay = delay or site.get("fetch.delay", [0.6, 1.4])
        lo, hi = (raw_delay if isinstance(raw_delay, (list, tuple)) else (raw_delay, raw_delay))[:2]
        self.delay = (float(lo), float(max(hi, lo)))
        self.timeout = int(site.get("fetch.timeout", 45000))
        self.retries = max(1, int(site.get("fetch.retries", 3)))
        self.wait_until = site.get("fetch.wait_until", "domcontentloaded")
        self.wait_for = site.get("fetch.wait_for")
        self.block_resources = bool(site.get("fetch.block_resources", True))
        self.headers: dict[str, str] = dict(site.get("fetch.headers", {}) or {})
        self.verbose = verbose

        self._pw = None
        self.context = None
        self._pages: asyncio.Queue[Page] | None = None
        self._sem = asyncio.Semaphore(self.concurrency)
        self._pace_lock = asyncio.Lock()
        self._next_at = 0.0
        self.stats = {"requests": 0, "retries": 0, "failures": 0}

    # -- vòng đời -------------------------------------------------------
    async def __aenter__(self) -> "Session":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self.context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1366, "height": 900},
            user_agent=self.site.get("fetch.user_agent", UA),
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            ignore_https_errors=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self.context.set_default_timeout(self.timeout)
        if self.headers:
            await self.context.set_extra_http_headers(self.headers)
        if self.render and self.block_resources:
            # Không cần ảnh/font để lấy chữ -> tải nhanh hơn nhiều.
            await self.context.route("**/*", self._block_heavy)
        if self.render:
            self._pages = asyncio.Queue()
            for _ in range(self.concurrency):
                await self._pages.put(await self.context.new_page())
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        try:
            if self.context:
                await self.context.close()
        finally:
            if self._pw:
                await self._pw.stop()

    @staticmethod
    async def _block_heavy(route, request) -> None:
        if request.resource_type in BLOCKED_TYPES:
            await route.abort()
        else:
            await route.continue_()

    # -- rate limit ------------------------------------------------------
    async def _pace(self) -> None:
        """Giữ khoảng cách tối thiểu giữa các request trên toàn bộ session."""
        loop = asyncio.get_running_loop()
        async with self._pace_lock:
            now = loop.time()
            start_at = max(now, self._next_at)
            self._next_at = start_at + random.uniform(*self.delay)
        wait = start_at - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)

    # -- tải nội dung ----------------------------------------------------
    async def html(self, url: str, wait_for: str | None = None) -> str:
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            async with self._sem:
                await self._pace()
                try:
                    self.stats["requests"] += 1
                    content = (
                        await self._html_rendered(url, wait_for)
                        if self.render
                        else await self._html_plain(url)
                    )
                    if content and len(content) > 200:
                        return content
                    raise FetchError(f"Nội dung rỗng hoặc quá ngắn ({len(content)} ký tự)")
                except (PWError, PWTimeout, FetchError) as exc:
                    last = exc
                    self.stats["retries"] += 1
                    if self.verbose:
                        print(f"    retry {attempt}/{self.retries} {url} -> {exc}")
            if attempt < self.retries:
                await asyncio.sleep(1.5 * attempt + random.random())
        self.stats["failures"] += 1
        raise FetchError(f"Tải thất bại sau {self.retries} lần: {url} ({last})")

    async def _html_plain(self, url: str) -> str:
        """Lấy HTML qua HTTP request (dùng chung cookie với context)."""
        response = await self.context.request.get(url, timeout=self.timeout)
        if not response.ok:
            raise FetchError(f"HTTP {response.status}")
        return await response.text()

    async def _html_rendered(self, url: str, wait_for: str | None) -> str:
        """Mở bằng page thật cho site render bằng JavaScript."""
        page = await self._pages.get()
        try:
            await page.goto(url, wait_until=self.wait_until, timeout=self.timeout)
            selector = wait_for or self.wait_for
            if selector:
                try:
                    await page.wait_for_selector(selector, timeout=self.timeout)
                except PWTimeout:
                    pass  # có trang thiếu khối đó, cứ lấy HTML hiện có
            return await page.content()
        finally:
            await self._pages.put(page)

    async def download(self, url: str, dest: Path) -> bool:
        """Tải file nhị phân (ảnh bìa)."""
        try:
            await self._pace()
            response = await self.context.request.get(url, timeout=self.timeout)
            if not response.ok:
                return False
            body = await response.body()
            if len(body) < 512:
                return False
            fsutil.makedirs(dest.parent)
            fsutil.write_bytes(dest, body)
            return True
        except (PWError, PWTimeout, OSError):
            return False

    # -- đăng nhập -------------------------------------------------------
    async def is_logged_in(self, probe_url: str | None = None) -> bool | None:
        """None = config không khai báo cách kiểm tra."""
        selectors = self.site.sels("auth.logged_in")
        if not selectors:
            return None
        url = probe_url or self.site.get("auth.probe_url") or self.site.get("auth.login_url")
        if not url:
            return None
        from .extract import select_one, parse  # nội bộ, tránh import vòng

        try:
            soup = parse(await self.html(url))
        except FetchError:
            return None
        return select_one(soup, selectors) is not None


async def manual_login(site: SiteConfig, profiles_dir: Path) -> None:
    """Mở browser thật để bạn tự đăng nhập; cookie lưu vào profile của site."""
    url = site.get("auth.login_url") or (f"https://{site.domains[0]}/" if site.domains else None)
    if not url:
        raise SystemExit(f"Config {site.name} chưa có auth.login_url và cũng chưa khai báo match/domain.")

    async with Session(site, profiles_dir, headless=False, render=True, concurrency=1) as session:
        # Trang đăng nhập cần ảnh/captcha hiển thị bình thường -> bỏ chặn resource.
        await session.context.unroute("**/*")
        page = session.context.pages[0] if session.context.pages else await session.context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        print(f"\n  Browser đã mở: {url}")
        print("  Đăng nhập bằng tay trong cửa sổ đó (kể cả OTP / captcha).")
        print("  Xong thì quay lại đây và bấm Enter để lưu phiên...\n")
        await asyncio.to_thread(input, "  [Enter] khi đã đăng nhập xong: ")

        selectors = site.sels("auth.logged_in")
        if selectors:
            from .extract import parse, select_one

            soup = parse(await page.content())
            ok = select_one(soup, selectors) is not None
            print("  -> Đã nhận diện trạng thái đăng nhập." if ok
                  else "  -> Chưa thấy dấu hiệu đăng nhập (auth.logged_in không khớp). Cookie vẫn được lưu.")
        print(f"  Profile lưu tại: {session.profile_dir}")
