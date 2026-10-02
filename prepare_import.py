r"""Chuyển thư mục `output/` thành thư mục staging cho lệnh `php artisan truyen:import`.

Lệnh import của web (repo novel, app/Console/Commands/ImportStories.php) đọc một
format hơi khác output của crawler. Script này làm phần chuyển đổi:

  output/<Tên truyện>/              ->  staging/<Tên truyện đã làm sạch>/
    _meta.json  (cover_file, status)      _meta.json  (image, is_completed, genres=slug)
    cover.jpg                             cover.webp  (đã resize)
    0001 - Chương 1 Mở đầu.txt            0001 - Mở đầu.txt  (dòng đầu chuẩn hoá)

Bốn việc chính:

1. Đổi khoá `cover_file` -> `image` và `status` -> `is_completed` cho khớp lệnh import.
2. Chuẩn hoá dòng đầu mỗi file chương thành "Chương <số>: <tên>". Nguồn có 166 kiểu
   dòng đầu khác nhau (`#1`, `164. ...`, `Ngoại truyện`, ...) mà lệnh import chỉ nhận
   `^Chương\s*(\d+)`, nên ta không đoán theo dòng đầu: số chương lấy từ
   `_meta.json` -> `chapters[].index` (đã kiểm chứng: duy nhất và liên tục 1..N ở
   mọi truyện) rồi tự ghi lại dòng đầu.
3. Map thể loại của nguồn sang slug category của web (xem GENRE_MAP).
4. Resize ảnh bìa — nguồn có file 57MB, driver ảnh `gd` của web nạp ảnh giải nén
   vào RAM nên dễ vượt memory_limit.

Chỉ xuất truyện đã tải đủ chương: lệnh import không thêm chương vào truyện đã có,
nên truyện đăng lúc còn tải dở sẽ mãi thiếu chương.

Dùng:

    python prepare_import.py
    python prepare_import.py --out D:\import-staging --limit 5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from crawler import fsutil
from crawler.naming import chapter_filename, safe_name

# Toàn bộ slug trong bảng categories của web (database/seeders/CategorySeeder.php).
# Dùng để chặn lỗi gõ sai trong GENRE_MAP ngay lúc nạp module.
CATEGORY_SLUGS = frozenset({
    "truyen-sang-tac", "tien-hiep", "kiem-hiep", "ngon-tinh", "dam-my",
    "quan-truong", "chu-cong", "vong-du", "khoa-huyen", "he-thong",
    "huyen-huyen", "di-gioi", "di-nang", "quan-su", "sac", "lich-su",
    "xuyen-khong", "xuyen-sach", "xuyen-nhanh", "can-dai", "trong-sinh",
    "trinh-tham", "tham-hiem", "linh-di", "nguoc", "sung", "cung-dau",
    "nu-cuong", "gia-dau", "dong-phuong", "do-thi", "bach-hop", "hai-huoc",
    "dien-van", "co-dai", "mat-the", "truyen-teen", "phuong-tay", "nu-phu",
    "light-novel", "viet-nam", "tuong-lai", "giai-tri", "doan-van",
    "review-sach", "truyen-ngan", "khac",
})

# Slug dùng khi không map được thể loại nào.
FALLBACK_CATEGORY = "khac"

# Thể loại nguồn -> slug category của web.
#
# Ghi slug (không ghi tên hiển thị) vì lệnh import so bằng Str::slug($genre), mà
# Str::slug("ngon-tinh") == "ngon-tinh" — khỏi phụ thuộc vào chuyện Python mô
# phỏng hàm chuyển dấu của Laravel có khớp tuyệt đối hay không.
#
# Thể loại không có trong bảng này bị bỏ qua có chủ ý: nguồn trộn lẫn tag phụ
# (`Kết HE`, `1 x 1`, `Tiểu thuyết`, `Bút Chiến 2025`...) với thể loại thật, mà
# web không có category tương ứng.
GENRE_MAP = {
    # Khớp sẵn với slug của web — liệt kê thẳng để không phải chuyển dấu lúc chạy.
    "Truyện sáng tác": "truyen-sang-tac",
    "Hài hước": "hai-huoc",
    "Việt Nam": "viet-nam",
    "Cổ đại": "co-dai",
    "Dị năng": "di-nang",
    "Truyện ngắn": "truyen-ngan",
    "Cận đại": "can-dai",
    "Phương Tây": "phuong-tay",
    "Tương lai": "tuong-lai",
    "Mạt thế": "mat-the",

    # Ngôn tình / tình cảm
    "Truyện Ngôn tình": "ngon-tinh",
    "Truyện Tình cảm lãng mạn": "ngon-tinh",
    "Lãng mạn": "ngon-tinh",
    "Nam x Nữ": "ngon-tinh",
    "Cưới trước yêu sau": "ngon-tinh",
    "Hợp đồng hôn nhân": "ngon-tinh",
    "Thanh mai trúc mã": "ngon-tinh",
    "Tình yêu sét đánh": "ngon-tinh",
    "Yêu thầm": "ngon-tinh",
    "Giả yêu thành thật": "ngon-tinh",
    "Truyện Hôn nhân - Gia đình": "ngon-tinh",

    # Đam mỹ / bách hợp
    "Truyện Đam mỹ": "dam-my",
    "Nam x Nam": "dam-my",
    "Truyện Sinh tử văn": "dam-my",
    "Truyện Bách hợp": "bach-hop",
    "Nữ x Nữ": "bach-hop",

    # Teen / học đường
    "Truyện Học đường": "truyen-teen",
    "Học sinh": "truyen-teen",
    "Trường học": "truyen-teen",
    "Tình yêu thầy trò": "truyen-teen",

    # Đô thị / hiện đại
    "Hiện đại": "do-thi",
    "Thành thị": "do-thi",
    "Tổng giám đốc/Chủ tịch": "do-thi",
    "Con nhà giàu": "do-thi",
    "Xã hội đen": "do-thi",
    "Truyện Thế giới ngầm": "do-thi",
    "Truyện Thương trường": "do-thi",
    "Nhân viên công sở": "do-thi",

    # Sủng / sắc
    "Ngọt ngào": "sung",
    "Sắc - Sạch - Sủng": "sung",
    "Chiếm hữu": "sung",
    "Nóng bỏng": "sac",
    "Truyện BDSM": "sac",
    "Tình một đêm": "sac",

    # Ngược
    "Truyện Ngược": "nguoc",
    "Truyện Tự ngược": "nguoc",
    "Bi thương": "nguoc",
    "Kết SE": "nguoc",

    # Huyền huyễn / tiên hiệp
    "Thế giới giả tưởng": "huyen-huyen",
    "Truyện Hư cấu kỳ ảo": "huyen-huyen",
    "Truyện Huyền huyễn": "huyen-huyen",
    "Truyện Siêu nhiên": "huyen-huyen",
    "Có phép thuật": "huyen-huyen",
    "Nhân vật thần thoại": "huyen-huyen",
    "Truyện Tiên hiệp": "tien-hiep",

    # Linh dị / kinh dị
    "Truyện Thần bí": "linh-di",
    "Rùng rợn": "linh-di",
    "Truyện Kinh dị": "linh-di",
    "Ma quỷ": "linh-di",
    "Truyện Tâm linh": "linh-di",
    "Người - Ma": "linh-di",
    "Việt Nam Huyền Bí": "linh-di",

    # Trinh thám
    "Truyện Trinh thám": "trinh-tham",
    "Truyện Tội phạm": "trinh-tham",
    "Cảnh sát - Tội phạm": "trinh-tham",

    # Xuyên / trọng sinh / hệ thống
    "Truyện Xuyên không": "xuyen-khong",
    "Thế giới song song": "xuyen-khong",
    "Truyện Xuyên nhanh": "xuyen-nhanh",
    "Truyện Xuyên sách": "xuyen-sach",
    "Truyện Trùng sinh": "trong-sinh",
    "Truyện Hệ thống": "he-thong",

    # Cổ đại / lịch sử / quân sự
    "Trung đại": "co-dai",
    "Xã hội cổ đại": "co-dai",
    "Hoàng thất": "co-dai",
    "Truyện Dã sử": "lich-su",
    "Truyện Cung đấu": "cung-dau",
    "Truyện Gia đấu": "gia-dau",
    "Truyện Tranh quyền đoạt lợi": "quan-truong",
    "Truyện Quân sự": "quan-su",
    "Bộ đội/Cảnh sát": "quan-su",
    "Chiến tranh": "quan-su",

    # Khoa huyễn / mạt thế
    "Truyện Khoa học viễn tưởng": "khoa-huyen",
    "Ngoài vũ trụ": "khoa-huyen",
    "Người ngoài hành tinh": "khoa-huyen",
    "Truyện Sinh tồn": "mat-the",
    "Zombie": "mat-the",

    # Điền văn / đời thường
    "Truyện Đời thường": "dien-van",
    "Nông thôn": "dien-van",
    "Truyện Ẩm thực": "dien-van",
    "Chữa lành": "dien-van",

    # Còn lại
    "Phương Đông": "dong-phuong",
    "Nước ngoài": "phuong-tay",
    "Truyện Nữ quyền": "nu-cuong",
    "Nhân vật phụ": "nu-phu",
    "Người nổi tiếng": "giai-tri",
    "Nghệ thuật gia": "giai-tri",
    "Thần tượng - Fan": "giai-tri",
    "Biên kịch/Nhà văn": "giai-tri",
    "Oneshot": "doan-van",
}

_BAD_SLUGS = sorted(set(GENRE_MAP.values()) - CATEGORY_SLUGS)
if _BAD_SLUGS:
    raise SystemExit(f"GENRE_MAP trỏ tới slug không có trong web: {_BAD_SLUGS}")

# Trạng thái nguồn tương ứng "đã hoàn thành".
COMPLETED_STATUSES = {"hoàn thành", "hoàn thanh", "full", "đã hoàn thành"}

# Tiền tố số chương cần cắt khỏi tên chương, để trang đọc không hiện
# "Chương 1: Chương 1: Cơ Bụng" (Chapter::$full_title đã tự ghép "Chương <số>: ").
# Dạng "Chương 12" / "Chapter 12" / "#12" cắt vô điều kiện; dạng số trần
# ("164. ...") chỉ cắt khi số khớp số chương — xem strip_number_prefix().
_LABELLED_PREFIX = re.compile(
    r"^\s*(?:chương|chuong|chapter|chap|ch|#)\s*0*(\d+)\s*[:.\-–—)]*\s*",
    re.IGNORECASE,
)
_BARE_PREFIX = re.compile(r"^\s*0*(\d+)\s*[:.\-–—)]+\s*")

# Ngoặc vuông làm PHP glob() trong lệnh import coi là character class, khiến
# glob("<thư mục>/*.txt") không khớp file nào. Bỏ khỏi tên thư mục staging.
_GLOB_META = re.compile(r"[\[\]{}]")

# Cạnh dài tối đa của ảnh bìa sau resize. Web còn hạ tiếp xuống 600x900.
COVER_MAX_EDGE = 1200
COVER_QUALITY = 85


def strip_number_prefix(title: str, number: int) -> str:
    """Bỏ tiền tố số chương khỏi tên chương.

    >>> strip_number_prefix("Chương 1: Cơ Bụng", 1)
    'Cơ Bụng'
    >>> strip_number_prefix("#7", 7)
    ''
    >>> strip_number_prefix("164. Truy đuổi", 164)
    'Truy đuổi'
    >>> strip_number_prefix("Ngoại truyện", 12)
    'Ngoại truyện'

    Số trong tên không nhất thiết khớp số chương: nguồn đánh số theo cách của nó
    (trang giới thiệu chiếm số 1), còn ta dùng `index` của crawler. Dạng có nhãn
    ("Chương 2") vẫn cắt vì chắc chắn là nhãn chương, còn dạng số trần chỉ cắt
    khi khớp — để không phá tên kiểu "2003: Năm ấy".
    """
    text = (title or "").strip()

    stripped = _LABELLED_PREFIX.sub("", text, count=1)
    if stripped != text:
        return stripped.strip()

    match = _BARE_PREFIX.match(text)
    if match and int(match.group(1)) == number:
        return text[match.end():].strip()

    return text


def heading_line(number: int, title: str) -> str:
    """Dòng đầu file chương theo đúng regex lệnh import chờ đợi."""
    return f"Chương {number}: {title}" if title else f"Chương {number}"


def chapter_body(text: str, number: int, raw_title: str) -> str:
    """Thân chương: bỏ dòng tiêu đề mà crawler ghi ở đầu file, nếu có.

    Crawler ghi dòng đầu là tiêu đề chương rồi một dòng trống rồi mới tới nội
    dung. Giữ lại thì người đọc thấy tên chương hai lần.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = text.split("\n")
    if not lines:
        return ""

    first = lines[0].strip()
    looks_like_heading = (
        first == (raw_title or "").strip()
        or strip_number_prefix(first, number) == strip_number_prefix(raw_title, number)
        or _LABELLED_PREFIX.match(first) is not None
    )
    if looks_like_heading:
        lines = lines[1:]

    return "\n".join(lines).strip("\n")


def resolve_genres(genres: list[str]) -> tuple[list[str], list[str]]:
    """(slug đã map, thể loại không map được) — slug giữ thứ tự, không trùng."""
    slugs: list[str] = []
    unmapped: list[str] = []

    for genre in genres:
        name = (genre or "").strip()
        if not name:
            continue
        slug = GENRE_MAP.get(name)
        if slug is None:
            unmapped.append(name)
        elif slug not in slugs:
            slugs.append(slug)

    return (slugs or [FALLBACK_CATEGORY]), unmapped


def staging_folder_name(name: str) -> str:
    """Tên thư mục staging: hợp lệ trên Windows và an toàn với PHP glob()."""
    return safe_name(_GLOB_META.sub("", name).strip()) or "truyen"


def convert_cover(source: Path, dest: Path) -> bool:
    """Resize ảnh bìa sang WebP. False khi ảnh không đọc được."""
    from PIL import Image

    try:
        with Image.open(fsutil.raw(source)) as image:
            image.load()
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.mode else "RGB")
            image.thumbnail((COVER_MAX_EDGE, COVER_MAX_EDGE), Image.LANCZOS)
            image.save(fsutil.raw(dest), "WEBP", quality=COVER_QUALITY, method=4)
    except Exception as error:  # ảnh tải dở, format lạ, quá lớn...
        print(f"    ! bìa {source.name} không đọc được ({error}) — đăng không bìa")
        return False

    return True


class Report:
    """Đếm kết quả để in tổng kết."""

    def __init__(self) -> None:
        self.written = 0
        self.chapters = 0
        self.no_cover = 0
        self.skipped: list[tuple[str, str]] = []
        self.unmapped: Counter[str] = Counter()

    def skip(self, name: str, reason: str) -> None:
        self.skipped.append((name, reason))
        print(f"  - bỏ qua: {reason}")


def prune_orphans(out_root: Path, folders: list[Path]) -> list[str]:
    """Xoá thư mục staging không còn truyện tương ứng trong output.

    Chỉ xét thư mục có `_meta.json` — tức do script này tạo — để không bao giờ
    xoá thứ gì khác mà người dùng để trong thư mục staging.
    """
    expected = {staging_folder_name(f.name) for f in folders}
    removed = []

    for entry in sorted(out_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name in expected:
            continue
        if not fsutil.is_file(entry / "_meta.json"):
            continue
        fsutil.rmtree(entry)
        removed.append(entry.name)

    return removed


def prepare_story(folder: Path, out_root: Path, report: Report) -> None:
    """Đọc một thư mục truyện và ghi bản staging của nó."""
    # Dọn bản staging cũ ngay từ đầu, trước cả các bước kiểm tra: truyện bị bỏ
    # qua lần này mà lần trước đã staging thì bản cũ phải mất theo, không thì nó
    # vẫn được đăng lên. Crawler quét thêm chương làm truyện 30/30 thành 30/50
    # là trường hợp gặp thật.
    dest = out_root / staging_folder_name(folder.name)
    fsutil.rmtree(dest)

    meta_path = folder / "_meta.json"
    if not fsutil.is_file(meta_path):
        report.skip(folder.name, "thiếu _meta.json (truyện đang tải)")
        return

    try:
        meta = json.loads(fsutil.read_text(meta_path))
    except json.JSONDecodeError as error:
        report.skip(folder.name, f"_meta.json lỗi: {error}")
        return

    title = (meta.get("title") or "").strip()
    if not title:
        report.skip(folder.name, "_meta.json thiếu title")
        return
    if len(title) > 200:
        report.skip(folder.name, f"title dài {len(title)} ký tự (web giới hạn 200)")
        return

    total = meta.get("chapter_count") or 0
    done = meta.get("done_count") or 0
    if done < total:
        report.skip(folder.name, f"mới tải {done}/{total} chương")
        return

    chapters = [c for c in (meta.get("chapters") or []) if c.get("done")]
    if not chapters:
        report.skip(folder.name, "chưa tải được chương nào")
        return

    fsutil.makedirs(dest)

    written = 0
    for chapter in chapters:
        number = chapter.get("index")
        source = chapter.get("file") or ""
        if not isinstance(number, int) or number < 1 or not source:
            continue

        source_path = folder / source
        if not fsutil.is_file(source_path):
            print(f"    ! chương {number}: thiếu file {source} — bỏ qua chương")
            continue

        raw_title = chapter.get("title") or ""
        clean_title = strip_number_prefix(raw_title, number)
        body = chapter_body(fsutil.read_text(source_path), number, raw_title)
        if not body:
            print(f"    ! chương {number}: không có nội dung — bỏ qua chương")
            continue

        fsutil.write_text(
            dest / chapter_filename(number, clean_title or f"Chương {number}"),
            heading_line(number, clean_title) + "\n\n" + body + "\n",
        )
        written += 1

    if not written:
        report.skip(folder.name, "không ghi được chương nào")
        fsutil.rmtree(dest)
        return

    slugs, unmapped = resolve_genres(meta.get("genres") or [])
    report.unmapped.update(unmapped)

    out_meta = {
        "title": title,
        "description": (meta.get("description") or "").strip(),
        "genres": slugs,
        "is_completed": (meta.get("status") or "").strip().lower() in COMPLETED_STATUSES,
    }

    cover_file = (meta.get("cover_file") or "").strip()
    cover_source = folder / cover_file if cover_file else None
    if cover_source and fsutil.is_file(cover_source):
        if convert_cover(cover_source, dest / "cover.webp"):
            out_meta["image"] = "cover.webp"
        else:
            report.no_cover += 1
    else:
        print("    ! không có file bìa — đăng không bìa")
        report.no_cover += 1

    fsutil.write_text(
        dest / "_meta.json",
        json.dumps(out_meta, ensure_ascii=False, indent=2) + "\n",
    )

    report.written += 1
    report.chapters += written
    print(f"  -> {written} chương, thể loại: {', '.join(slugs)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chuẩn bị thư mục staging cho lệnh php artisan truyen:import.",
    )
    parser.add_argument(
        "source", nargs="?", default="output",
        help="thư mục output của crawler (mặc định ./output)",
    )
    parser.add_argument(
        "-o", "--out", default="import-staging",
        help="thư mục staging ghi ra (mặc định ./import-staging)",
    )
    parser.add_argument(
        "--limit", type=int, help="chỉ xử lý N truyện đầu — để chạy thử cho nhanh",
    )
    args = parser.parse_args(argv)

    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Cần Pillow để resize ảnh bìa: pip install -r requirements.txt")
        return 1

    # Nguồn có ảnh bìa 57MB; Pillow chặn ảnh quá lớn để phòng decompression bomb.
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 300_000_000

    source = Path(args.source)
    if not source.is_dir():
        print(f"Không tìm thấy thư mục: {source}")
        return 1

    out_root = Path(args.out)
    if _GLOB_META.search(str(out_root.resolve())):
        print(f"Đường dẫn staging không được chứa ngoặc vuông: {out_root.resolve()}")
        return 1

    folders = sorted(
        (p for p in source.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    if args.limit:
        folders = folders[:args.limit]

    if not folders:
        print(f"Không có thư mục truyện nào trong {source}")
        return 1

    fsutil.makedirs(out_root)
    print(f"{len(folders)} thư mục truyện trong {source} -> {out_root}\n")

    if not args.limit:
        for name in prune_orphans(out_root, folders):
            print(f"! dọn thư mục staging không còn trong output: {name}")

    report = Report()
    for folder in folders:
        print(f"[{folder.name}]")
        prepare_story(folder, out_root, report)

    print(f"\n{'=' * 60}")
    print(f"Ghi ra : {report.written} truyện, {report.chapters} chương")
    print(f"Bỏ qua : {len(report.skipped)} truyện")
    for name, reason in report.skipped:
        print(f"    {name} — {reason}")
    if report.no_cover:
        print(f"Không bìa: {report.no_cover} truyện")
    if report.unmapped:
        print(f"\nThể loại chưa map ({len(report.unmapped)} loại) — bỏ qua có chủ ý,")
        print("thêm vào GENRE_MAP nếu muốn dùng:")
        for name, count in report.unmapped.most_common():
            print(f"    {count:4d}  {name}")

    print(f"\nBước tiếp: php artisan truyen:import {out_root.resolve()} --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
