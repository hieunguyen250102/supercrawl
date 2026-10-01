# Truyen Crawler

Crawl truyện chữ đa site bằng Python + Playwright. Mỗi truyện một thư mục, mỗi chương một file `.txt`, kèm ảnh bìa.

```
output/
  Tên Truyện A/
    cover.jpg
    info.txt                      # tác giả, thể loại, giới thiệu, nguồn
    0001 - Chương 1 Mở đầu.txt
    0002 - Chương 2 Gặp gỡ.txt
    ...
    _meta.json                    # danh sách chương + chương nào đã tải (dùng để resume)
  Tên Truyện B/
    ...
```

## Cài đặt

```bash
pip install -r requirements.txt
```

Nếu máy chưa từng dùng Playwright, tải browser một lần:

```bash
python -m playwright install chromium
```

Yêu cầu Python 3.10+.

## Dùng nhanh

```bash
python -m crawler crawl https://truyenfull.vn/ten-truyen/
```

```bash
python -m crawler crawl https://truyenfull.vn/the-loai/tien-hiep/ --limit 10
```

Tool tự nhận URL bạn đưa là **một truyện** hay **một danh sách truyện** (thể loại, tìm kiếm, top...).
Nếu nhận sai thì ép bằng `--as novel` hoặc `--as listing`.

## Các lệnh

| Lệnh | Việc |
|---|---|
| `crawl <url...>` | Crawl URL truyện hoặc URL danh sách. Tự resume nếu đã chạy dở. |
| `update [url...]` | Quét lại danh sách chương, chỉ tải chương mới. Không truyền URL = update mọi truyện đã lưu. |
| `login <site\|url>` | Mở browser để bạn tự đăng nhập; cookie được lưu lại cho các lần crawl sau. |
| `probe <url>` | In ra selector nào khớp / không khớp trên URL đó. Dùng khi viết hoặc sửa config site. |
| `status` | Xem tiến độ: danh sách đang ở trang mấy, truyện nào thiếu chương. |
| `sites` | Liệt kê các site đã có config. |

Cờ hay dùng:

```
--limit N        số truyện tối đa (khi crawl URL danh sách)
--chapters N     số chương tối đa mỗi truyện — để thử config cho nhanh
--max-pages N    số trang danh sách tối đa cho lần chạy này
--from-page N    bắt đầu từ trang N, bỏ qua tiến độ đã lưu
--restart        xoá tiến độ của URL này rồi chạy lại từ đầu
-c 5             số request song song
--delay 1 2      giãn cách ngẫu nhiên 1-2 giây giữa các request
--headful        hiện cửa sổ browser (để xem site đang trả về gì)
--render         buộc chạy JavaScript  /  --no-render chỉ tải HTML
-o, --out DIR    thư mục kết quả (mặc định ./output)
```

## Tiến độ và resume

Có hai tầng, đúng như cách site tổ chức dữ liệu:

- **Theo site** — `data/state/<site>.json`: mỗi URL danh sách lưu đang ở **trang phân trang** nào và những truyện nào đã crawl xong.
- **Theo truyện** — `<thư mục truyện>/_meta.json`: toàn bộ danh sách chương, chương nào đã tải, file tên gì.

Nên:

- `Ctrl+C` giữa lúc chạy rồi chạy lại đúng lệnh cũ → tiếp tục từ trang và chương đang dở, không tải lại từ đầu.
- Xoá tay một file chương → lần chạy sau tải lại đúng file đó.
- Copy cả thư mục truyện sang máy khác → `_meta.json` đi theo, vẫn resume được.
- State ghi theo kiểu atomic (file tạm rồi replace), nên ngắt điện giữa lúc ghi cũng không làm hỏng file.

## Đăng nhập (chương VIP / site bắt login)

```bash
python -m crawler login metruyencv
```

Browser thật mở ra, bạn đăng nhập bằng tay (kể cả OTP, captcha), quay lại terminal bấm Enter.
Cookie lưu trong `data/profiles/<site>/` và tự dùng lại cho mọi lần crawl sau, kể cả chạy headless.

Chương nào bị khoá mà chưa đăng nhập, tool báo rõ `chương bị khoá, cần đăng nhập` thay vì lưu file rỗng.

## Thêm site mới

1. Copy `sites/_template.yml` thành `sites/<ten-site>.yml`, sửa `name` và `match`.
2. Mở trang truyện trên browser, F12, tìm selector cho tiêu đề / nội dung / link chương.
3. Kiểm tra bằng `probe`:

```bash
python -m crawler probe https://site-moi.com/ten-truyen/
```

```bash
python -m crawler probe https://site-moi.com/ten-truyen/chuong-1/
```

`probe` in ra từng selector khớp gì, và 200 ký tự đầu của nội dung sau khi làm sạch. Sửa YAML đến khi không còn dòng `(không khớp)`.

4. Chạy thử giới hạn nhỏ trước khi chạy thật:

```bash
python -m crawler crawl https://site-moi.com/ten-truyen/ --chapters 3
```

Cú pháp selector trong config:

- `div.book img` → lấy **text** của phần tử đầu tiên khớp
- `div.book img@src` → lấy **attribute** `src` (tự fallback `data-src`, `data-original` cho ảnh lazy-load)
- `["a.x", "h1.y"]` → thử lần lượt, dùng selector đầu tiên khớp
- Chỉ CSS selector, không có XPath

Phân trang chọn một trong hai cách:

```yaml
pages:
  next: "ul.pagination li.active + li > a"   # selector link "trang sau"
  # url_pattern: "{url}/trang-{page}/"       # hoặc ghép URL theo số trang
```

## Site SPA nhúng JSON (`source: json`)

Nhiều site đời mới (Inertia.js, Next.js, Nuxt) không render truyện ra HTML mà nhúng
nguyên khối JSON vào response đầu tiên. Với những site đó, đặt `source: json` và dùng
đường dẫn trong JSON thay cho CSS selector — **không cần bật `render`**, vì dữ liệu đã
có sẵn ngay ở lần tải đầu, nhanh hơn nhiều so với mở page thật.

```yaml
source: json
json:
  container: '#app@data-page'   # nơi chứa chuỗi JSON (Next.js: 'script#__NEXT_DATA__')
  unescape: false               # bật nếu JSON bị escape thêm một lớp

novel:
  title: props.novel.title                       # đường dẫn thẳng
  genres: props.novel.tags[].name                # [] = duyệt cả list
  status:
    path: props.novel.is_complete
    map: {1: "Hoàn thành", 0: "Đang ra"}         # đổi cờ số thành nhãn
  cover:
    tpl: "https://site.com/img/{props.novel.pic}"
  chapter_links:
    list: props.novel.chapters                   # list các object
    url: "https://site.com/truyen/{props.novel.slug}/{id}"
    title: "{title}"
chapter:
  content:
    path: props.chapter.content
    unescape: true                               # field chứa HTML bị escape
  locked:
    path: props.chapter.content
    empty: true                                  # rỗng = chương trả phí chưa mua
```

Trong template, `{đường.dẫn}` lấy từ gốc JSON; bên trong `chapter_links` thì `{field}`
lấy từ từng item, còn `{@url}` / `{@slug}` là URL trang đang đọc. Item nào thiếu dữ liệu
để ghép URL sẽ bị bỏ qua thay vì tạo ra link cụt.

`probe` in ra `Kiểu trang : json` và kết quả từng đường dẫn, dùng y như với site HTML.
Xem [sites/rookies.yml](sites/rookies.yml) làm ví dụ đầy đủ.

## Làm sạch nội dung

Mỗi chương đi qua: bỏ `script`/`style`/`iframe`/`ins` → bỏ các selector trong `chapter.remove` → ép về text giữ ngắt dòng → chuẩn hoá unicode NFC, bỏ ký tự zero-width → bỏ dòng khớp `chapter.strip_lines` → gom dòng trống → bỏ tiêu đề chương bị lặp ở dòng đầu.

Có sẵn một bộ regex lọc rác chung (dòng `Nguồn:`, `converter by`, `quảng cáo`, `chương trước / chương sau`...). Rác riêng của từng site thì thêm vào `chapter.strip_lines`.

Chương nào sau khi làm sạch ngắn hơn `chapter.min_length` sẽ bị coi là tải lỗi, ghi `error` vào `_meta.json` và tải lại ở lần chạy sau — tránh việc lưu file rỗng rồi tưởng là xong.

## Tốc độ và lịch sự với server

- `fetch.concurrency` giới hạn số request đồng thời, `fetch.delay` giữ khoảng cách tối thiểu giữa các request **trên toàn session**. Mặc định 3 luồng, 0.6-1.4s.
- Vì delay là hàng đợi chung, tốc độ thực tế ≈ `1 / delay trung bình` request mỗi giây, bất kể để bao nhiêu luồng. Tăng `-c` chỉ giúp che độ trễ mạng; muốn nhanh hơn thì giảm `--delay`.
- `render: false` (mặc định) chỉ tải HTML — nhanh, đủ cho hầu hết site truyện. `render: true` mở page thật chạy JS, chậm hơn nhiều, dùng cho site Vue/React.
- Khi render, ảnh/font/video bị chặn để tải nhanh hơn.
- Request lỗi được retry với backoff tăng dần (`fetch.retries`).

Đừng hạ delay xuống quá thấp: site truyện thường chặn IP khi thấy request dồn dập, và tool không có cách nào vượt qua việc đó.

## Test

```bash
python test_offline.py
```

Chạy toàn bộ pipeline trên HTML giả, không cần mạng: phân trang danh sách chương, resume sau khi xoá file, update lấy chương mới, crawl URL danh sách, tách thư mục cho truyện trùng tên, lọc quảng cáo. Nên chạy sau mỗi lần sửa code.

## Cấu trúc code

```
crawler/
  cli.py        # các lệnh CLI
  config.py     # nạp sites/*.yml, merge với giá trị mặc định
  browser.py    # Playwright: profile giữ login, rate limit, retry, đăng nhập tay
  pager.py      # đi qua danh sách phân trang (selector hoặc url_pattern)
  novel.py      # crawl 1 truyện: metadata, cover, danh sách chương, nội dung
  listing.py    # crawl 1 URL danh sách truyện
  docs.py       # lớp chung: HtmlDoc (CSS selector) và JsonDoc (đường dẫn JSON)
  extract.py    # áp selector lên HTML
  clean.py      # HTML chương -> text sạch
  state.py      # tiến độ theo site + theo truyện
  naming.py     # tên file/thư mục an toàn trên Windows
  fsutil.py     # đọc/ghi chịu được path dài > 260 ký tự
sites/          # config từng site
data/
  state/        # tiến độ
  profiles/     # cookie đăng nhập theo site
output/         # kết quả
```

## Lưu ý

Tình trạng các config có sẵn:

| Config | Tình trạng |
|---|---|
| `sites/rookies.yml` | Đã kiểm chứng trên trang thật (30/09/2026): danh sách, trang truyện, trang chương, ảnh bìa, phân trang đều chạy. |
| `sites/truyenfull.yml` | **Chưa đối chiếu** với trang thật — selector viết theo cấu trúc quen thuộc, cần `probe` lại. |
| `sites/metruyencv.yml` | **Chưa đối chiếu** với trang thật, site render bằng JS nên càng dễ lệch. |

Site truyện đổi layout khá thường xuyên, nên khi một config bỗng không ra chương nữa, chạy `probe` trên một URL cụ thể là cách nhanh nhất để biết selector nào hỏng.

Tool này để tải truyện về đọc cá nhân. Nội dung trên các site đó thuộc về tác giả và đơn vị phát hành — đừng đăng lại hay phân phối những gì bạn tải về.
#   s u p e r c r a w l  
 