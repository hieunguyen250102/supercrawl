"""Test offline: chạy toàn bộ pipeline crawl trên HTML giả, không cần mạng.

    python test_offline.py

Dùng để kiểm tra nhanh sau khi sửa code hoặc sửa config.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crawler import fsutil
from crawler.cli import use_utf8_console
from crawler.config import SiteRegistry
from crawler.listing import crawl_listing
from crawler.novel import crawl_novel
from crawler.state import NovelMeta, SiteState

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".test-tmp"
BASE = "https://truyenfull.vn"
NOVEL = f"{BASE}/truyen-test"
LISTING = f"{BASE}/the-loai/tien-hiep"
NOVEL_FOLDER = "Đỉnh Phong Test Hồi Quy"

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        failures.append(label)


def chapter_files(folder: Path) -> list[str]:
    return sorted(n for n in fsutil.listdir(folder) if n.endswith(".txt") and n != "info.txt")


def rmtree(path: Path) -> None:
    fsutil.rmtree(path)


# -- HTML giả -----------------------------------------------------------
def novel_page(chapter_links: str) -> str:
    return f"""<html><head><title>Truyện Test</title></head><body>
    <div class="col-info-desc">
      <h3 class="title">Đỉnh Phong Test: Hồi Quy</h3>
      <div class="info"><a itemprop="author" href="/tac-gia/abc/">Tác Giả Thử</a>
        <a itemprop="genre" href="/the-loai/tien-hiep/">Tiên hiệp</a>
        <a itemprop="genre" href="/the-loai/xuyen-khong/">Xuyên không</a>
        <span class="text-primary">Đang ra</span></div>
      <div class="book"><img src="/images/bia-test.jpg" alt="bia"></div>
      <div class="desc-text">Một dòng giới thiệu thử nghiệm cho bộ truyện giả lập.</div>
    </div>
    <div id="list-chapter"><ul class="list-chapter">{chapter_links}</ul></div>
    <div class="ads">quảng cáo không được lọt vào nội dung</div>
    {"<!-- padding -->" * 20}
    </body></html>"""


def chapter_items(numbers: list[int], slug: str = "truyen-test") -> str:
    return "".join(
        f'<li><a href="/{slug}/chuong-{n}/" title="Chương {n}">Chương {n}: Hồi thứ {n}</a></li>'
        for n in numbers
    )


def chapter_page(n: int) -> str:
    body = " ".join(
        f"Đây là câu số {i} của chương {n}, nội dung đủ dài để vượt min_length."
        for i in range(1, 8)
    )
    return f"""<html><body>
    <a class="chapter-title">Chương {n}: Hồi thứ {n}</a>
    <div id="chapter-c">
      <div class="ads-responsive">QUẢNG CÁO CẦN BỊ XOÁ</div>
      Chương {n}: Hồi thứ {n}<br><br>
      {body}<br><br>Đoạn hai của chương {n}.
      <p>Nguồn: mot-trang-nao-do.vn — dòng này phải bị lọc</p>
      <script>var x = 1;</script>
    </div>
    {"<!-- padding -->" * 20}
    </body></html>"""


def listing_page(slugs: list[str]) -> str:
    rows = "".join(
        f'<div class="row"><h3 class="truyen-title"><a href="/{s}/">Truyện {s}</a></h3></div>'
        for s in slugs
    )
    return f'<html><body><div class="list-truyen">{rows}</div>{"<!-- pad -->" * 30}</body></html>'


def empty_page() -> str:
    return f'<html><body><div class="list-truyen"></div>{"<!-- pad -->" * 40}</body></html>'


# -- Session giả --------------------------------------------------------
class FakeSession:
    """Đủ interface mà novel.py / listing.py / pager.py cần: site, html, download."""

    def __init__(self, site, pages: dict[str, str]):
        self.site = site
        self.pages = {self._key(k): v for k, v in pages.items()}
        self.requested: list[str] = []
        self.render = False
        self.concurrency = 1
        self.delay = (0.0, 0.0)
        self.stats = {"requests": 0, "retries": 0, "failures": 0}

    @staticmethod
    def _key(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", parts.query, "")
        )

    async def html(self, url: str, wait_for: str | None = None) -> str:
        self.requested.append(url)
        self.stats["requests"] += 1
        key = self._key(url)
        if key not in self.pages:
            from crawler.browser import FetchError

            raise FetchError(f"404 giả: {url}")
        return self.pages[key]

    async def download(self, url: str, dest: Path) -> bool:
        fsutil.makedirs(dest.parent)
        fsutil.write_bytes(dest, b"\x89PNG\r\n\x1a\n" + b"0" * 900)
        return True


def novel_pages(chapters: list[int], split_at: int) -> dict[str, str]:
    """Trang 1 giữ vài chương đầu, trang 2 phần còn lại, trang 3 rỗng."""
    first, rest = chapters[:split_at], chapters[split_at:]
    pages = {
        NOVEL: novel_page(chapter_items(first)),
        f"{NOVEL}/trang-2/": novel_page(chapter_items(rest)),
        f"{NOVEL}/trang-3/": novel_page(""),
    }
    for n in chapters:
        pages[f"{NOVEL}/chuong-{n}/"] = chapter_page(n)
    return pages


# -- các bài test -------------------------------------------------------
async def main() -> int:
    use_utf8_console()
    rmtree(TMP)
    out = TMP / "output"
    site = SiteRegistry.load(ROOT / "sites").by_name("truyenfull")

    print("\n[1] Crawl một truyện, có phân trang danh sách chương")
    session = FakeSession(site, novel_pages([1, 2, 3, 4, 5], split_at=3))
    result = await crawl_novel(session, out, NOVEL)
    folder = Path(result["folder"])

    check(folder.name == NOVEL_FOLDER, f"tên thư mục = {folder.name!r}")
    check(result["chapters"] == 5, f"gom đủ 5 chương qua 2 trang (được {result['chapters']})")
    check(result["downloaded"] == 5 and result["failed"] == 0, "tải xong 5 chương, 0 lỗi")

    files = chapter_files(folder)
    check(len(files) == 5, f"có 5 file chương ({len(files)})")
    check(files[0].startswith("0001 - "), f"file đầu đánh số 0 đệm: {files[0]!r}")
    check(fsutil.is_file(folder / "cover.jpg"), "lưu được ảnh bìa cover.jpg")
    check(fsutil.is_file(folder / "info.txt"), "ghi info.txt")
    check(fsutil.is_file(folder / "_meta.json"), "ghi _meta.json")

    text = fsutil.read_text(folder / files[0])
    check("QUẢNG CÁO" not in text, "xoá block quảng cáo theo chapter.remove")
    check("Nguồn:" not in text, "lọc dòng bắt đầu bằng 'Nguồn:'")
    check("var x" not in text, "xoá thẻ script")
    check(text.count("Chương 1: Hồi thứ 1") == 1, "tiêu đề chương không bị lặp 2 lần")
    check("Đoạn hai của chương 1." in text, "giữ nguyên nội dung thật")

    info = fsutil.read_text(folder / "info.txt")
    check("Tác Giả Thử" in info, "info.txt có tác giả")
    check("Tiên hiệp, Xuyên không" in info, "info.txt có đủ thể loại")

    print("\n[2] Chạy lại: không tải lại chương nào")
    session2 = FakeSession(site, novel_pages([1, 2, 3, 4, 5], split_at=3))
    again = await crawl_novel(session2, out, NOVEL)
    check(again["downloaded"] == 0, f"không tải lại (downloaded={again['downloaded']})")
    check(len(session2.requested) == 1, f"chỉ tải lại trang truyện ({len(session2.requested)} request)")

    print("\n[3] Resume: xoá 1 file chương giữa bài, chỉ tải lại đúng file đó")
    fsutil.unlink(folder / files[2])
    session3 = FakeSession(site, novel_pages([1, 2, 3, 4, 5], split_at=3))
    resumed = await crawl_novel(session3, out, NOVEL)
    check(resumed["downloaded"] == 1, f"tải lại đúng 1 chương ({resumed['downloaded']})")
    check(fsutil.is_file(folder / files[2]), "file bị xoá đã có lại")

    print("\n[4] Update: nguồn có thêm chương 6-7")
    session4 = FakeSession(site, novel_pages([1, 2, 3, 4, 5, 6, 7], split_at=3))
    updated = await crawl_novel(session4, out, NOVEL, update=True)
    check(updated["chapters"] == 7, f"danh sách thành 7 chương ({updated['chapters']})")
    check(updated["downloaded"] == 2, f"chỉ tải 2 chương mới ({updated['downloaded']})")
    meta = NovelMeta(folder)
    check([c["index"] for c in meta.chapters] == list(range(1, 8)), "index chương liên tục 1..7")
    check(meta.done_count == 7, f"đủ 7 chương done ({meta.done_count})")

    print("\n[5] Crawl URL danh sách + lưu tiến độ phân trang")
    pages = {
        LISTING: listing_page(["truyen-a", "truyen-b"]),
        f"{LISTING}/trang-2/": empty_page(),
    }
    for slug in ("truyen-a", "truyen-b"):
        url = f"{BASE}/{slug}"
        pages[url] = novel_page(chapter_items([1, 2], slug))
        pages[f"{url}/trang-2/"] = novel_page("")
        for n in (1, 2):
            pages[f"{url}/chuong-{n}/"] = chapter_page(n)

    state = SiteState(TMP / "state" / "truyenfull.json", "truyenfull")
    session5 = FakeSession(site, pages)
    summary = await crawl_listing(session5, out, LISTING, state, verbose=False)
    check(summary["novels_crawled"] == 2, f"crawl 2 truyện từ danh sách ({summary['novels_crawled']})")
    check(summary["finished"] is True, "nhận ra đã hết danh sách")
    entry = state.listing(LISTING)
    check(len(entry["novels_done"]) == 2, f"state ghi 2 truyện đã xong ({len(entry['novels_done'])})")
    check(len(state.novels()) == 2, "index truyện của site có 2 mục")

    # Hai truyện này cố tình trùng tên với nhau và với truyện ở test [1].
    folders = fsutil.listdir(out)
    check(
        f"{NOVEL_FOLDER} (truyen-a)" in folders and f"{NOVEL_FOLDER} (truyen-b)" in folders,
        f"truyện trùng tên được tách thư mục riêng ({len(folders)} thư mục)",
    )
    for slug in ("truyen-a", "truyen-b"):
        sub = out / f"{NOVEL_FOLDER} ({slug})"
        check(len(chapter_files(sub)) == 2, f"{slug}: đúng 2 file chương, không lẫn của truyện khác")

    print("\n[6] Danh sách chạy lần 2: bỏ qua truyện đã xong")
    session6 = FakeSession(site, pages)
    state2 = SiteState(TMP / "state" / "truyenfull.json", "truyenfull")
    summary2 = await crawl_listing(session6, out, LISTING, state2)
    check(summary2["novels_crawled"] == 0, f"không crawl lại ({summary2['novels_crawled']})")

    print("\n[7] Giới hạn --chapters để thử config")
    rmtree(out / NOVEL_FOLDER)
    session7 = FakeSession(site, novel_pages([1, 2, 3, 4, 5], split_at=3))
    limited = await crawl_novel(session7, out, NOVEL, limit=2)
    check(limited["downloaded"] == 2, f"chỉ tải 2 chương ({limited['downloaded']})")
    check(limited["chapters"] == 5, "vẫn lưu đủ danh sách 5 chương để tải tiếp sau")

    print("\n[8] crawl_listing phải chuyển tiếp giới hạn số chương cho từng truyện")
    out2 = TMP / "output2"
    session8 = FakeSession(site, pages)
    state3 = SiteState(TMP / "state" / "limit.json", "truyenfull")
    await crawl_listing(session8, out2, LISTING, state3, chapters=1)
    folders8 = fsutil.listdir(out2)
    counts = [len(chapter_files(out2 / name)) for name in folders8]
    check(len(folders8) == 2, f"vẫn crawl đủ 2 truyện ({len(folders8)})")
    check(all(c == 1 for c in counts), f"mỗi truyện chỉ tải 1 chương ({counts})")

    print("\n" + "=" * 60)
    if failures:
        print(f"CÓ {len(failures)} TEST FAIL:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("TẤT CẢ TEST OFFLINE ĐỀU PASS")
    rmtree(TMP)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
