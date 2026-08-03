"""
集成测试 V2.0.2: 本地HTTP服务器 + 真实Crawler组件
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

from crawler import EmagCrawler
from utils import (
    detect_waf_block, WafBlockError, load_txt_categories, write_atomic_json,
    get_product_key, EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR, EXIT_CAPTCHA,
)

# ============================================================
# 测试用 HTML
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

PAGE_3 = """<html><body><h1>Page 3 (Last)</h1>
""" + "".join(PRODUCT_CARD.format(i + 20, i + 20, i, i + 20, (i + 20) * 10) for i in range(1, 3)) + """
</body></html>"""

CAPTCHA_HTML = """<html><head><title>eMAG Captcha</title></head>
<body><script>aws-waf-token</script></body></html>"""


class _THandler(http.server.BaseHTTPRequestHandler):
    routes = {}
    req_count = 0
    lock = threading.Lock()

    @classmethod
    def reset_count(cls):
        cls.req_count = 0

    def do_GET(self):
        with _THandler.lock:
            _THandler.req_count += 1
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path in self.routes:
            status, ctype, body = self.routes[path]
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class LocalServer:
    def __init__(self):
        self.port = 19878
        self._srv = None

    def start(self):
        _THandler.reset_count()
        self._srv = socketserver.TCPServer(("127.0.0.1", self.port), _THandler)
        self._srv.allow_reuse_address = True
        t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        t.start()

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    def url(self, path="/test/c"):
        return f"http://127.0.0.1:{self.port}{path}"

    def set_routes(self, routes):
        _THandler.routes = routes


@pytest.fixture
def server():
    srv = LocalServer()
    srv.start()
    time.sleep(0.05)
    yield srv
    srv.stop()


def _mkcats(server, paths):
    return [{"name": f"Cat_{p.split('/')[1]}", "url": server.url(p), "enabled": True} for p in paths]


# ============================================================
# WAF 检测测试
# ============================================================
class TestWafDetection:
    def test_403_triggers_waf(self):
        err = detect_waf_block("<html></html>", 403, "http://t/c")
        assert err is not None and err.status_code == 403

    def test_429_triggers_waf(self):
        assert detect_waf_block("<html></html>", 429, "http://t/c") is not None

    def test_511_plain_body(self):
        err = detect_waf_block("<html><body>hi</body></html>", 511, "http://t/c")
        assert err is not None and err.status_code == 511

    def test_200_captcha_body(self):
        assert detect_waf_block(CAPTCHA_HTML, 200, "http://t/c") is not None

    def test_200_normal(self):
        assert detect_waf_block(PAGE_1, 200, "http://t/c") is None

    def test_attrs(self):
        err = WafBlockError(403, "Cat", 1, "url", "T", "ev")
        assert err.block_type == "T" and err.status_code == 403


# ============================================================
# Crawler 集成测试
# ============================================================
class TestCrawlerIntegration:
    def test_one_page(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 5
        assert s["totals"]["success_pages"] == 1
        assert os.path.exists(os.path.join(out, "products.json"))
        assert os.path.exists(os.path.join(out, "errors.csv"))

    def test_two_pages_limit(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1),
                          "/test/p2/c": (200, "text/html", PAGE_2),
                          "/test/p3/c": (200, "text/html", PAGE_3)})
        out = str(tmp_path / "out")
        _THandler.reset_count()
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=2)
        s = c.finalize()
        assert s["totals"]["total_records"] == 8  # 5+3
        assert s["totals"]["success_pages"] == 2
        assert _THandler.req_count <= 3

    def test_waf_403(self, server, tmp_path):
        server.set_routes({"/test/c": (403, "text/html", "Forbidden")})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert c._waf_stop.is_set()
        assert s["status"] == "waf_blocked"
        assert os.path.exists(os.path.join(out, "diagnostics", "captcha_diagnostic.json"))

    def test_waf_511(self, server, tmp_path):
        server.set_routes({"/test/c": (511, "text/html", "plain")})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c.finalize()
        assert c._waf_stop.is_set()

    def test_cross_cat_no_dup(self, server, tmp_path):
        server.set_routes({"/cat1/c": (200, "text/html", PAGE_1),
                          "/cat2/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/cat1/c", "/cat2/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 10
        assert s["totals"]["success_pages"] == 2

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

    def test_real_requested_count(self, server, tmp_path):
        """requested_pages 是真实请求数, 不是上限值"""
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=5)
        s = c.finalize()
        for cat in s["categories"]:
            # 首页+尝试第2页(404) = 2次真实请求, 但绝不是5
            assert cat["requested_pages"] < 5


# ============================================================
# TXT 配置 (urllib.parse)
# ============================================================
class TestTxtConfigUrllib:
    def test_valid(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("https://www.emag.ro/mouse/c\n", encoding="utf-8")
        assert len(load_txt_categories(str(f))) == 1

    def test_http_ok(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("http://www.emag.ro/mouse/c\n", encoding="utf-8")
        assert len(load_txt_categories(str(f))) == 1

    def test_ftp_rejected(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("ftp://www.emag.ro/mouse/c\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_txt_categories(str(f))

    def test_bad_domain(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("https://evil.com/mouse/c\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_txt_categories(str(f))

    def test_no_c_suffix(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("https://www.emag.ro/category\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_txt_categories(str(f))

    def test_pd_rejected(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("https://www.emag.ro/test/pd/ABC/\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_txt_categories(str(f))


# ============================================================
# 退出码
# ============================================================
class TestExitCodes:
    def test_distinct(self):
        codes = {EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR, EXIT_CAPTCHA}
        assert len(codes) == 4 and EXIT_CAPTCHA == 3

    def test_pages_0(self):
        from main import validate_positive
        with pytest.raises(SystemExit) as e:
            validate_positive(0, "--pages")
        assert e.value.code == EXIT_CONFIG_ERROR


# ============================================================
# JSON / 产品键
# ============================================================
class TestJsonAndKey:
    def test_atomic(self, tmp_path):
        f = tmp_path / "p.json"
        write_atomic_json(str(f), [{"extra": {"k": "v"}}])
        d = json.load(open(str(f), encoding="utf-8"))
        assert isinstance(d[0]["extra"], dict)
        assert not os.path.exists(str(f) + ".tmp")

    def test_product_key(self):
        k1 = get_product_key({"pnk": "ABC", "product_id": "1"})
        k2 = get_product_key({"pnk": "ABC", "product_id": "2"})
        assert k1 == k2 == "pnk:ABC"
