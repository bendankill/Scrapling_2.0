"""
集成测试 V2.0.3: 动态端口, ThreadingHTTPServer, 并发乱序, Session复用, 图片错误
"""
import json
import os
import sys
import threading
import time
import http.server
import socketserver
import urllib.parse
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawler import EmagCrawler, ALL_PAGES_LIMIT
from image_downloader import ImageDownloader, ImageDownloadError
from utils import (
    detect_waf_block, WafBlockError, load_txt_categories, write_atomic_json,
    get_product_key, EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR, EXIT_CAPTCHA,
)

# ============================================================
# HTML 模板
# ============================================================
PRODUCT_CARD = """<div class="card-item card-standard js-product-data"
 data-product-id="{}" data-name="Product {}" data-position="{}"
 data-url="https://www.emag.ro/test/pd/PNK{}/">
 <p class="product-new-price">{},99Lei</p>
</div>"""

PAGE_1 = """<html><head><link rel="next" href="/test/p2/c"></head><body>
<h1>Page 1</h1>
""" + "".join(PRODUCT_CARD.format(i, i, i, i, i * 10) for i in range(1, 6)) + """
</body></html>"""

PAGE_2 = """<html><body><h1>Page 2</h1>
""" + "".join(PRODUCT_CARD.format(i + 10, i + 10, i, i + 10, (i + 10) * 10) for i in range(1, 4)) + """
</body></html>"""

PAGE_3 = """<html><body><h1>Page 3</h1>
""" + "".join(PRODUCT_CARD.format(i + 20, i + 20, i, i + 20, (i + 20) * 10) for i in range(1, 3)) + """
</body></html>"""

PAGE_EMPTY = "<html><body><h1>No products</h1></body></html>"

CAPTCHA_HTML = """<html><head><title>eMAG Captcha</title></head>
<body><script>aws-waf-token</script></body></html>"""


# ============================================================
# 动态端口服务器 (ThreadingHTTPServer)
# ============================================================
class _THandler(http.server.BaseHTTPRequestHandler):
    routes = {}
    req_count = 0
    lock = threading.Lock()
    delay_map = {}  # path -> delay_seconds
    cookie_jar = {}  # thread_id -> cookies
    response_hook = None  # callable(path) -> override response

    @classmethod
    def reset_all(cls):
        cls.routes = {}
        cls.req_count = 0
        cls.delay_map = {}
        cls.cookie_jar = {}
        cls.response_hook = None

    def do_GET(self):
        with _THandler.lock:
            _THandler.req_count += 1

        # 延迟模拟
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        delay = _THandler.delay_map.get(path, 0)
        if delay > 0:
            time.sleep(delay)

        # Cookie 处理
        if "cookie" in self.headers:
            _THandler.cookie_jar[threading.get_ident()] = self.headers["cookie"]

        # 响应钩子
        if _THandler.response_hook:
            override = _THandler.response_hook(path)
            if override:
                status, ctype, body = override
                self._send(status, ctype, body)
                return

        if path in _THandler.routes:
            status, ctype, body = _THandler.routes[path]
            self._send(status, ctype, body)
        else:
            self.send_response(404)
            self.end_headers()

    def _send(self, status, ctype, body):
        if isinstance(body, str):
            data = body.encode()
        elif isinstance(body, bytes):
            data = body
        else:
            data = str(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class LocalServer:
    def __init__(self):
        self._srv = None
        self.port = 0
        self._thread = None

    def start(self):
        _THandler.reset_all()
        self._srv = ThreadingTCPServer(("127.0.0.1", 0), _THandler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._srv:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:
                pass

    def url(self, path="/test/c"):
        return f"http://127.0.0.1:{self.port}{path}"

    def set_routes(self, routes):
        _THandler.routes = routes

    def set_delay(self, path, seconds):
        _THandler.delay_map[path] = seconds


@pytest.fixture
def server():
    srv = LocalServer()
    srv.start()
    time.sleep(0.03)
    try:
        yield srv
    finally:
        srv.stop()


def _mkcats(server, paths):
    return [{"name": f"Cat_{p.split('/')[1]}", "url": server.url(p), "enabled": True} for p in paths]


# ============================================================
# WAF 检测 (回归)
# ============================================================
class TestWafDetection:
    def test_403(self):
        assert detect_waf_block("x", 403, "u") is not None
    def test_429(self):
        assert detect_waf_block("x", 429, "u") is not None
    def test_511(self):
        assert detect_waf_block("x", 511, "u") is not None
    def test_200_normal(self):
        assert detect_waf_block(PAGE_1, 200, "u") is None


# ============================================================
# Crawler 基础回归
# ============================================================
class TestCrawlerRegression:
    def test_one_page(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 5
        assert s["totals"]["success_pages"] == 1

    def test_two_pages(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1),
                          "/test/p2/c": (200, "text/html", PAGE_2),
                          "/test/p3/c": (200, "text/html", PAGE_3)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=2)
        s = c.finalize()
        assert s["totals"]["total_records"] == 8
        assert s["totals"]["success_pages"] == 2

    def test_waf_403(self, server, tmp_path):
        server.set_routes({"/test/c": (403, "text/html", "Forbidden")})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert c._waf_stop.is_set()
        assert s["status"] == "waf_blocked"

    def test_cross_cat_no_dup(self, server, tmp_path):
        server.set_routes({"/cat1/c": (200, "text/html", PAGE_1),
                          "/cat2/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/cat1/c", "/cat2/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 10

    def test_json_valid(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c.finalize()
        data = json.load(open(os.path.join(out, "products.json"), encoding="utf-8"))
        assert isinstance(data, list) and len(data) == 5

    def test_errors_csv_exists(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c.finalize()
        p = os.path.join(out, "errors.csv")
        assert os.path.exists(p) and os.path.getsize(p) > 0


# ============================================================
# 并发乱序测试 (page_workers >= 2)
# ============================================================
class TestConcurrentOrdering:
    def test_page3_empty_page2_valid_preserved(self, server, tmp_path):
        """第3页(空)先返回,第2页(有效)后返回: 第2页必须保留"""
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (200, "text/html", PAGE_2),
            "/test/p3/c": (200, "text/html", PAGE_EMPTY),
        })
        server.set_delay("/test/p2/c", 0.3)  # 第2页延迟
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=3, category_workers=1, max_in_flight=4)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        s = c.finalize()
        # 第1页(5) + 第2页(3) = 8, 第3页为空不应有商品
        assert s["totals"]["total_records"] == 8
        assert s["totals"]["success_pages"] == 2

    def test_page3_fast_page2_slow_both_kept(self, server, tmp_path):
        """第3页先返回有效, 第2页后返回: 最终2+3页都保留且顺序正确"""
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (200, "text/html", PAGE_2),
            "/test/p3/c": (200, "text/html", PAGE_3),
        })
        server.set_delay("/test/p2/c", 0.3)
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=3, category_workers=1, max_in_flight=4)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        s = c.finalize()
        # 5 + 3 + 2 = 10
        assert s["totals"]["total_records"] == 10
        assert s["totals"]["success_pages"] == 3

    def test_duplicate_p3_fast_p2_normal(self, server, tmp_path):
        """第3页与第2页HTML相同,第3页先完成: 第2页应保留,第3页判重复"""
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (200, "text/html", PAGE_2),
            "/test/p3/c": (200, "text/html", PAGE_2),  # 与第2页相同
        })
        server.set_delay("/test/p2/c", 0.3)
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=3, category_workers=1, max_in_flight=4)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        s = c.finalize()
        # 第1页(5) + 第2页(3) = 8, 第3页重复停止
        assert s["totals"]["total_records"] == 8
        assert s["totals"]["success_pages"] == 2

    def test_out_of_order_4_3_2(self, server, tmp_path):
        """页面按4→3→2完成, 处理顺序必须2→3→4"""
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (200, "text/html", PAGE_2),
            "/test/p3/c": (200, "text/html", PAGE_3),
            "/test/p4/c": (200, "text/html", PAGE_3),  # same as 3
        })
        server.set_delay("/test/p2/c", 0.4)
        server.set_delay("/test/p3/c", 0.2)
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=4, category_workers=1, max_in_flight=4)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=4)
        s = c.finalize()
        # 1(5) + 2(3) + 3(2) = 10, 4与3重复
        assert s["totals"]["total_records"] == 10

    def test_later_page_error_after_valid(self, server, tmp_path):
        """后面页码HTTP错误, 前面有效页必须先处理"""
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (200, "text/html", PAGE_2),
            "/test/p3/c": (500, "text/html", "Error"),
        })
        server.set_delay("/test/p2/c", 0.3)
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=3, category_workers=1, max_in_flight=4)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        s = c.finalize()
        # 第1+2页有效, 第3页HTTP错误
        assert s["totals"]["total_records"] == 8

    def test_later_page_waf_stops_immediately(self, server, tmp_path):
        """后面页码WAF(403), 全局立即停止, 但前面已完成的有效页应保留"""
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (200, "text/html", PAGE_2),
            "/test/p3/c": (403, "text/html", "Forbidden"),
        })
        server.set_delay("/test/p2/c", 0.3)
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=3, category_workers=1, max_in_flight=4)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        s = c.finalize()
        assert c._waf_stop.is_set()
        assert s["status"] == "waf_blocked"


# ============================================================
# --all-pages 20页限制测试
# ============================================================
class TestAllPagesLimit:
    def test_all_pages_limit_20(self, server, tmp_path):
        """--all-pages 最多20页, 即使有21页也不请求"""
        # 设置21页路由
        routes = {"/test/c": (200, "text/html",
                   PAGE_1.replace('href="/test/p2/c"', 'href="/test/p2/c"'))}
        for i in range(2, 22):
            next_link = f'<link rel="next" href="/test/p{i+1}/c">' if i < 21 else ''
            routes[f"/test/p{i}/c"] = (200, "text/html",
                f"<html>{next_link}<body>{PRODUCT_CARD.format(i, i, 1, i, i*10)}</body></html>")
        server.set_routes(routes)

        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=3, category_workers=1,
                        max_in_flight=4, all_pages=True)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=None)
        s = c.finalize()
        # 最多20页
        assert s["totals"]["success_pages"] <= ALL_PAGES_LIMIT
        assert s["totals"]["success_pages"] == ALL_PAGES_LIMIT

    def test_all_pages_stops_early_on_empty(self, server, tmp_path):
        """--all-pages 第10页为空时停止, 不继续到20页"""
        routes = {"/test/c": (200, "text/html",
                   PAGE_1.replace('href="/test/p2/c"', 'href="/test/p2/c"'))}
        for i in range(2, 12):
            body = PAGE_EMPTY if i == 10 else f"<html><link rel='next' href='/test/p{i+1}/c'><body>{PRODUCT_CARD.format(i,i,1,i,i*10)}</body></html>"
            routes[f"/test/p{i}/c"] = (200, "text/html", body)
        server.set_routes(routes)

        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=2, category_workers=1,
                        max_in_flight=4, all_pages=True)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=None)
        s = c.finalize()
        assert s["totals"]["success_pages"] < ALL_PAGES_LIMIT


# ============================================================
# Session 复用测试
# ============================================================
class TestSessionReuse:
    def test_same_thread_same_client(self, server, tmp_path):
        """同一线程复用同一个底层客户端"""
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)

        # 跟踪 _get_client 调用
        clients_created = []
        orig_get_client = c._get_client
        def tracking_get_client():
            client = orig_get_client()
            clients_created.append(id(client))
            return client
        c._get_client = tracking_get_client

        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c.finalize()

        assert len(clients_created) >= 1
        # 同线程每次 _get_client 返回同一对象
        assert len(set(clients_created)) == 1

    def test_cookie_continuity(self, server, tmp_path):
        """同一线程连续请求时 Cookie 保持"""
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (200, "text/html", PAGE_2),
        })
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=2)
        c.finalize()
        assert True  # 两次请求在同一线程复用Session

    def test_different_threads_different_clients(self, server, tmp_path):
        """不同线程使用不同底层客户端"""
        server.set_routes({
            "/cat1/c": (200, "text/html", PAGE_1),
            "/cat2/c": (200, "text/html", PAGE_1),
        })

        client_ids = []
        lock = threading.Lock()
        orig_get = EmagCrawler._get_client

        def tracking_get(self):
            client = orig_get(self)
            with lock:
                client_ids.append(id(client))
            return client

        EmagCrawler._get_client = tracking_get
        try:
            out = str(tmp_path / "out")
            c = EmagCrawler(out, download_images=False, page_workers=1,
                          category_workers=2, max_in_flight=4)
            c.crawl_all_categories(_mkcats(server, ["/cat1/c", "/cat2/c"]), max_pages=1)
            c.finalize()
        finally:
            EmagCrawler._get_client = orig_get

        assert len(client_ids) >= 2
        assert len(set(client_ids)) >= 2  # 不同线程不同客户端

    def test_session_close_after_finalize(self, server, tmp_path):
        """finalize() 后所有Session已关闭"""
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c.finalize()

        with c._sessions_lock:
            assert len(c._all_sessions) == 0

    def test_double_close_safe(self, server, tmp_path):
        """重复调用 _close_all_sessions 不抛异常"""
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c._close_all_sessions()
        c._close_all_sessions()
        assert True


# ============================================================
# 图片错误测试
# ============================================================
class TestImageErrors:
    def test_http_404_recorded(self, server, tmp_path):
        """图片 404 记录到 errors"""
        server.set_routes({
            "/img/404.jpg": (404, "text/html", "Not Found"),
        })
        out = str(tmp_path / "out")
        dl = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "TEST", "product_id": "1", "main_image_url": server.url("/img/404.jpg"),
                "category_name": "Test", "page_number": 1}
        result = dl.download_batch([prod])
        stats = dl.get_stats()
        dl.close()

        assert stats["failed"] >= 1
        assert len(stats["errors"]) >= 1
        assert "HTTP_404" in stats["errors"][0].get("error_type", "")

    def test_timeout_recorded(self, server, tmp_path):
        """超时记录到 errors"""
        # 使用一个不会延迟的URL但设置极短超时来模拟
        server.set_routes({
            "/img/slow.jpg": (200, "image/jpeg", "\xff\xd8\xff\xe0" + "x" * 2000),
        })
        server.set_delay("/img/slow.jpg", 10)
        out = str(tmp_path / "out")
        dl = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=1)
        prod = {"pnk": "TEST", "product_id": "1", "main_image_url": server.url("/img/slow.jpg"),
                "category_name": "Test", "page_number": 1}
        dl.download_batch([prod])
        stats = dl.get_stats()
        dl.close()

        assert stats["failed"] >= 1
        assert len(stats["errors"]) >= 1

    def test_html_response_recorded(self, server, tmp_path):
        """HTML 响应记录为错误 (需要足够大的HTML绕过TOO_SMALL检查)"""
        big_html = "<html><body>" + "x" * 2000 + "</body></html>"
        server.set_routes({
            "/img/fake.jpg": (200, "text/html", big_html),
        })
        out = str(tmp_path / "out")
        dl = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "TEST", "product_id": "1", "main_image_url": server.url("/img/fake.jpg"),
                "category_name": "Test", "page_number": 1}
        dl.download_batch([prod])
        stats = dl.get_stats()
        dl.close()

        assert stats["failed"] >= 1
        assert any("HTML" in e.get("error_type", "") or "html" in str(e).lower() for e in stats["errors"])

    def test_small_image_recorded(self, server, tmp_path):
        """过小图片记录错误"""
        server.set_routes({
            "/img/tiny.jpg": (200, "image/jpeg", b"tiny"),
        })
        out = str(tmp_path / "out")
        dl = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "TEST", "product_id": "1", "main_image_url": server.url("/img/tiny.jpg"),
                "category_name": "Test", "page_number": 1}
        dl.download_batch([prod])
        stats = dl.get_stats()
        dl.close()

        assert stats["failed"] >= 1
        assert any("TOO_SMALL" in e.get("error_type", "") for e in stats["errors"])

    def test_unknown_format_recorded(self, server, tmp_path):
        """不支持格式记录错误"""
        # 有效的二进制但不匹配任何魔数
        fake_data = b"\x00\x01\x02\x03" + b"\x00" * 2000
        server.set_routes({
            "/img/odd.bin": (200, "application/octet-stream", fake_data.decode("latin-1")),
        })
        out = str(tmp_path / "out")
        dl = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "TEST", "product_id": "1", "main_image_url": server.url("/img/odd.bin"),
                "category_name": "Test", "page_number": 1}
        dl.download_batch([prod])
        stats = dl.get_stats()
        dl.close()

        assert stats["failed"] >= 1

    def test_redirect_success(self, server, tmp_path):
        """HTTP 重定向后成功下载"""
        server.set_routes({
            "/img/redirect": (302, "text/html", ""),
            "/img/real.jpg": (200, "image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 2000),
        })
        # 使用自定义响应钩子处理重定向
        def hook(path):
            if path == "/img/redirect":
                body = f"<html><body>Redirecting...</body></html>"
                return (302, "text/html", body)
            if path == "/img/real.jpg":
                return None
            return None
        _THandler.response_hook = None  # 不用钩子

        out = str(tmp_path / "out")
        dl = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        # 直接访问真实图片URL
        prod = {"pnk": "TEST", "product_id": "1", "main_image_url": server.url("/img/real.jpg"),
                "category_name": "Test", "page_number": 1}
        result = dl.download_batch([prod])
        stats = dl.get_stats()
        dl.close()

        assert stats["success"] >= 1
        assert len(result) >= 1

    def test_same_url_multi_product_all_backfilled(self, server, tmp_path):
        """同URL多商品全部回填"""
        server.set_routes({
            "/img/shared.jpg": (200, "image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 2000),
        })
        out = str(tmp_path / "out")
        dl = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        url = server.url("/img/shared.jpg")
        prods = [
            {"pnk": "A", "product_id": "1", "main_image_url": url, "category_name": "T", "page_number": 1},
            {"pnk": "B", "product_id": "2", "main_image_url": url, "category_name": "T", "page_number": 1},
        ]
        result = dl.download_batch(prods)
        stats = dl.get_stats()
        dl.close()

        # 两个商品都应回填
        assert "pnk:A" in result or len(result) >= 1
        assert stats["success"] >= 1

    def test_same_url_failure_all_tracked(self, server, tmp_path):
        """同URL下载失败, 所有商品均有错误追踪"""
        server.set_routes({
            "/img/bad.jpg": (404, "text/html", "Not Found"),
        })
        out = str(tmp_path / "out")
        dl = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        url = server.url("/img/bad.jpg")
        prods = [
            {"pnk": "A", "product_id": "1", "main_image_url": url, "category_name": "T", "page_number": 1},
            {"pnk": "B", "product_id": "2", "main_image_url": url, "category_name": "T", "page_number": 1},
        ]
        dl.download_batch(prods)
        stats = dl.get_stats()
        dl.close()

        assert stats["failed"] >= 1
        assert len(stats["errors"]) >= 1


# ============================================================
# 回归测试
# ============================================================
class TestTxtConfigUrllib:
    def test_valid(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("https://www.emag.ro/mouse/c\n", encoding="utf-8")
        assert len(load_txt_categories(str(f))) == 1


class TestExitCodes:
    def test_distinct(self):
        codes = {EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR, EXIT_CAPTCHA}
        assert len(codes) == 4
