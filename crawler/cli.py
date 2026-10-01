"""CLI: crawl / update / login / sites / probe."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .browser import FetchError, Session, manual_login
from .config import SiteConfig, SiteRegistry
from .docs import HtmlDoc, JsonDoc, make_doc
from .listing import crawl_listing
from .novel import crawl_novel
from .state import SiteState

ROOT = Path(__file__).resolve().parent.parent


def use_utf8_console() -> None:
    """Console Windows mặc định là cp1252 -> in tiếng Việt sẽ crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


# -- tiện ích ------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crawler",
        description="Crawl truyện chữ đa site: mỗi truyện 1 thư mục, mỗi chương 1 file .txt.",
    )
    parser.add_argument("--sites-dir", type=Path, default=ROOT / "sites", help="Thư mục config site")
    parser.add_argument("--out", type=Path, default=ROOT / "output", help="Thư mục kết quả")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data", help="Nơi lưu state + profile")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_fetch_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--site", help="Ép dùng config site này (mặc định: nhận theo domain)")
        sp.add_argument("-c", "--concurrency", type=int, help="Số request song song")
        sp.add_argument("--delay", type=float, nargs="+", metavar="GIÂY",
                        help="Giãn cách giữa các request: 1 số hoặc MIN MAX")
        sp.add_argument("--headful", action="store_true", help="Hiện cửa sổ browser")
        render = sp.add_mutually_exclusive_group()
        render.add_argument("--render", dest="render", action="store_true", help="Buộc chạy JS")
        render.add_argument("--no-render", dest="render", action="store_false", help="Chỉ tải HTML")
        sp.set_defaults(render=None)

    p_crawl = sub.add_parser("crawl", help="Crawl 1 URL truyện hoặc 1 URL danh sách truyện")
    p_crawl.add_argument("urls", nargs="+")
    p_crawl.add_argument("--as", dest="kind", choices=["novel", "listing"], help="Bỏ qua tự nhận dạng")
    p_crawl.add_argument("--limit", type=int, help="Số truyện tối đa (với URL danh sách)")
    p_crawl.add_argument("--chapters", type=int, help="Số chương tối đa mỗi truyện (để thử config)")
    p_crawl.add_argument("--max-pages", type=int, help="Số trang danh sách tối đa")
    p_crawl.add_argument("--from-page", type=int, help="Bắt đầu từ trang này (bỏ qua tiến độ đã lưu)")
    p_crawl.add_argument("--restart", action="store_true", help="Xoá tiến độ của URL này rồi chạy lại")
    p_crawl.add_argument("--update", action="store_true", help="Quét lại danh sách chương để lấy chương mới")
    add_fetch_opts(p_crawl)

    p_update = sub.add_parser("update", help="Lấy chương mới cho các truyện đã tải")
    p_update.add_argument("urls", nargs="*", help="Để trống = mọi truyện đã lưu trong state")
    p_update.add_argument("--chapters", type=int)
    add_fetch_opts(p_update)

    p_login = sub.add_parser("login", help="Mở browser để đăng nhập bằng tay (lưu cookie theo site)")
    p_login.add_argument("target", help="Tên site trong sites/ hoặc một URL của site đó")

    sub.add_parser("sites", help="Liệt kê các site đã có config")

    p_probe = sub.add_parser("probe", help="Thử selector trên 1 URL để dò/sửa config")
    p_probe.add_argument("url")
    add_fetch_opts(p_probe)

    p_status = sub.add_parser("status", help="Xem tiến độ đã lưu")
    p_status.add_argument("--site", help="Chỉ xem 1 site")
    return parser


def make_session(args: argparse.Namespace, site: SiteConfig) -> Session:
    delay = None
    if getattr(args, "delay", None):
        values = args.delay
        delay = (values[0], values[1] if len(values) > 1 else values[0])
    return Session(
        site,
        args.data_dir / "profiles",
        headless=not getattr(args, "headful", False),
        render=getattr(args, "render", None),
        concurrency=getattr(args, "concurrency", None),
        delay=delay,
        verbose=args.verbose,
    )


def site_state(args: argparse.Namespace, site: SiteConfig) -> SiteState:
    return SiteState(args.data_dir / "state" / f"{site.name}.json", site.name)


async def detect_kind(session: Session, url: str) -> str:
    """Tải 1 lần để phân biệt trang truyện và trang danh sách."""
    site = session.site
    doc = make_doc(site, await session.html(url), url)
    if doc.links(site.get("novel.chapter_links"), url):
        return "novel"
    if doc.links(site.get("listing.novel_links"), url):
        return "listing"
    raise FetchError(
        "Không nhận ra đây là trang truyện hay trang danh sách. "
        f"Chạy `probe` để kiểm tra selector, hoặc thêm cờ --as novel|listing."
    )


# -- các lệnh ------------------------------------------------------------
async def cmd_crawl(args: argparse.Namespace, registry: SiteRegistry) -> int:
    failures = 0
    by_site: dict[str, list[str]] = {}
    for url in args.urls:
        site = registry.resolve(url, args.site)
        by_site.setdefault(site.name, []).append(url)

    for site_name, urls in by_site.items():
        site = registry.by_name(site_name)
        state = site_state(args, site)
        async with make_session(args, site) as session:
            print(f"# site: {site.name} | render={session.render} "
                  f"| song song={session.concurrency} | delay={session.delay[0]:.1f}-{session.delay[1]:.1f}s")
            for url in urls:
                kind = args.kind or site.url_kind(url) or await detect_kind(session, url)
                try:
                    if kind == "novel":
                        result = await crawl_novel(
                            session, args.out, url,
                            update=args.update, limit=args.chapters, verbose=args.verbose,
                        )
                        state.put_novel(url, {
                            "title": result["title"], "folder": result["folder"],
                            "chapters": result["chapters"], "done": result["done"],
                        })
                        state.save()
                        failures += 1 if result["failed"] else 0
                    else:
                        summary = await crawl_listing(
                            session, args.out, url, state,
                            limit=args.limit, chapters=args.chapters,
                            max_pages=args.max_pages,
                            from_page=args.from_page, restart=args.restart,
                            update=args.update, verbose=args.verbose,
                        )
                        print(f"\n# Danh sách xong: {summary['novels_crawled']} truyện, "
                              f"tới trang {summary['page']}"
                              + (" (hết danh sách)" if summary["finished"] else ""))
                        failures += summary["novels_failed"]
                except FetchError as exc:
                    failures += 1
                    print(f"! {url}: {exc}")
            print(f"\n# request: {session.stats['requests']}, "
                  f"retry: {session.stats['retries']}, thất bại: {session.stats['failures']}")
    return 1 if failures else 0


async def cmd_update(args: argparse.Namespace, registry: SiteRegistry) -> int:
    targets: dict[str, list[str]] = {}
    if args.urls:
        for url in args.urls:
            site = registry.resolve(url, args.site)
            targets.setdefault(site.name, []).append(url)
    else:
        for site in registry.sites:
            if args.site and site.name.lower() != args.site.lower():
                continue
            state = site_state(args, site)
            urls = [n["url"] for n in state.novels() if n.get("url")]
            if urls:
                targets[site.name] = urls

    if not targets:
        print("Chưa có truyện nào trong state. Chạy `crawl <url>` trước đã.")
        return 0

    failures = 0
    for site_name, urls in targets.items():
        site = registry.by_name(site_name)
        state = site_state(args, site)
        print(f"# update site {site.name}: {len(urls)} truyện")
        async with make_session(args, site) as session:
            for url in urls:
                try:
                    result = await crawl_novel(
                        session, args.out, url,
                        update=True, limit=args.chapters, verbose=args.verbose,
                    )
                    state.put_novel(url, {
                        "title": result["title"], "folder": result["folder"],
                        "chapters": result["chapters"], "done": result["done"],
                    })
                    state.save()
                except FetchError as exc:
                    failures += 1
                    print(f"! {url}: {exc}")
    return 1 if failures else 0


async def cmd_login(args: argparse.Namespace, registry: SiteRegistry) -> int:
    target = args.target
    site = registry.for_url(target) if "://" in target else registry.by_name(target)
    await manual_login(site, args.data_dir / "profiles")
    return 0


async def cmd_probe(args: argparse.Namespace, registry: SiteRegistry) -> int:
    """In ra selector nào khớp / không khớp — dùng khi viết config cho site mới."""
    site = registry.resolve(args.url, args.site)
    async with make_session(args, site) as session:
        raw = await session.html(args.url)
    try:
        doc = make_doc(site, raw, args.url)
    except ValueError as exc:
        print(f"\n! {exc}")
        print(f"  Kiểm tra json.container trong {site.path}")
        return 1

    print(f"\nSite config : {site.path}")
    print(f"Kiểu trang  : {doc.kind}  (HTML {len(raw)} ký tự)")
    print(f"URL         : {args.url}\n")

    single = [
        ("novel.title", "Tên truyện"),
        ("novel.author", "Tác giả"),
        ("novel.status", "Trạng thái"),
        ("novel.cover", "Ảnh bìa"),
        ("chapter.title", "Tên chương"),
    ]
    for key, label in single:
        spec = site.get(key)
        value = doc.text(spec) if spec else ""
        print(f"  {label:<12} [{key}] -> {_short(value) if value else '(không khớp)'}")

    for key, label in (("novel.genres", "Thể loại"), ("novel.description", "Giới thiệu")):
        values = doc.texts(site.get(key))
        joined = ", ".join(values)
        print(f"  {label:<12} [{key}] -> {_short(joined) if joined else '(không khớp)'}")

    for key, label in (("novel.chapter_links", "Link chương"), ("listing.novel_links", "Link truyện")):
        links = doc.links(site.get(key), args.url)
        print(f"  {label:<12} [{key}] -> {len(links)} link")
        for url, title in links[:3]:
            print(f"                  · {_short(title, 40)} | {url}")

    body = doc.html(site.get("chapter.content"))
    if body:
        from .clean import extract_content

        text = extract_content(
            body,
            remove_selectors=site.sels("chapter.remove"),
            strip_lines=site.sels("chapter.strip_lines"),
        )
        print(f"  {'Nội dung':<12} [chapter.content] -> {len(text)} ký tự sau khi làm sạch")
        print(f"  {'':<12} 60 ký tự đầu: {_short(text.replace(chr(10), ' / '), 60)}")
    else:
        print(f"  {'Nội dung':<12} [chapter.content] -> (không khớp)")
    return 0


async def cmd_sites(args: argparse.Namespace, registry: SiteRegistry) -> int:
    if not registry.sites:
        print(f"Chưa có config nào trong {args.sites_dir}")
        return 1
    for site in registry.sites:
        login = "có" if site.get("auth.login_url") else "-"
        print(f"  {site.name:<16} domain: {', '.join(site.domains):<34} "
              f"render: {str(site.render):<5} login: {login}")
    return 0


async def cmd_status(args: argparse.Namespace, registry: SiteRegistry) -> int:
    any_found = False
    for site in registry.sites:
        if args.site and site.name.lower() != args.site.lower():
            continue
        path = args.data_dir / "state" / f"{site.name}.json"
        if not path.is_file():
            continue
        any_found = True
        state = SiteState(path, site.name)
        print(f"\n# {site.name}")
        for entry in state.data["listings"].values():
            flag = "hết danh sách" if entry.get("finished") else f"đang ở trang {entry.get('page', 0)}"
            print(f"  danh sách: {entry['url']}")
            print(f"             {flag}, đã xong {len(entry.get('novels_done', []))} truyện")
        for novel in sorted(state.novels(), key=lambda n: n.get("title", "")):
            done, total = novel.get("done", 0), novel.get("chapters", 0)
            mark = "đủ" if total and done >= total else "thiếu"
            print(f"  [{mark:<5}] {done}/{total:<6} {novel.get('title', '?')}")
    if not any_found:
        print("Chưa có state nào.")
    return 0


def _short(text: str, width: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


COMMANDS = {
    "crawl": cmd_crawl,
    "update": cmd_update,
    "login": cmd_login,
    "probe": cmd_probe,
    "sites": cmd_sites,
    "status": cmd_status,
}


async def run(args: argparse.Namespace) -> int:
    registry = SiteRegistry.load(args.sites_dir)
    (args.data_dir / "state").mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    return await COMMANDS[args.command](args, registry)


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n^C — đã dừng. Tiến độ đã được lưu, chạy lại lệnh cũ để tiếp tục.")
        return 130
    except (KeyError, FileNotFoundError, FetchError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
