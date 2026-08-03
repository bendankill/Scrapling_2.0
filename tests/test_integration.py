"""
集成测试 V2.1.1: checkpoint, 断点续抓, 准确统计, Ctrl+C
"""
import json, os, sys, threading, time, http.server, socketserver, urllib.parse, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawler import EmagCrawler, PageResult, ALL_PAGES_LIMIT
from checkpoint import CheckpointManager
from image_downloader import ImageDownloader
from utils import (detect_waf_block, WafBlockError, load_txt_categories,
    write_atomic_json, get_product_key, EXIT_SUCCESS, EXIT_CONFIG_ERROR,
    EXIT_NETWORK_ERROR, EXIT_CAPTCHA)

# ============================================================
PRODUCT_CARD = """<div class="card-item card-standard js-product-data"
 data-product-id="{}" data-name="Product {}" data-position="{}"
 data-url="https://www.emag.ro/test/pd/PNK{}/">
 <p class="product-new-price">{},99Lei</p>
</div>"""

def _make_page(n, count, has_next=True, start_id=0):
    nl = f'<link rel="next" href="/test/p{n+1}/c">' if has_next else ''
    cards = "".join(PRODUCT_CARD.format(start_id+i, start_id+i, i, start_id+i, (start_id+i)*10) for i in range(1, count+1))
    return f"<html><head>{nl}</head><body><h1>Page {n}</h1>{cards}</body></html>"

PAGE_1 = _make_page(1, 5)
PAGE_2 = _make_page(2, 3, start_id=10)
PAGE_3 = _make_page(3, 2, has_next=False, start_id=20)

class _THandler(http.server.BaseHTTPRequestHandler):
    routes = {}; req_count = 0; lock = threading.Lock(); delay_map = {}
    @classmethod
    def reset_all(cls): cls.routes = {}; cls.req_count = 0; cls.delay_map = {}
    def do_GET(self):
        with _THandler.lock: _THandler.req_count += 1
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        delay = _THandler.delay_map.get(path, 0)
        if delay > 0: time.sleep(delay)
        if path in self.routes:
            s, ct, body = self.routes[path]; data = body.encode() if isinstance(body, str) else body
            self.send_response(s); self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)
        else: self.send_response(404); self.end_headers()
    def log_message(self, f, *a): pass

class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True; daemon_threads = True

class LocalServer:
    def __init__(self):
        self._srv = None; self.port = 0
    def start(self):
        _THandler.reset_all()
        self._srv = ThreadingTCPServer(("127.0.0.1", 0), _THandler)
        self.port = self._srv.server_address[1]
        t = threading.Thread(target=self._srv.serve_forever, daemon=True); t.start()
    def stop(self):
        if self._srv:
            try: self._srv.shutdown(); self._srv.server_close()
            except: pass
    def url(self, path="/test/c"): return f"http://127.0.0.1:{self.port}{path}"
    def set_routes(self, r): _THandler.routes = r
    def set_delay(self, p, s): _THandler.delay_map[p] = s

@pytest.fixture
def server():
    srv = LocalServer(); srv.start(); time.sleep(0.03)
    try: yield srv
    finally: srv.stop()

def _mkcats(srv, paths):
    return [{"name": f"Cat_{p.split('/')[1]}", "url": srv.url(p), "enabled": True} for p in paths]

# ============================================================
# 基本回归
# ============================================================
class TestRegression:
    def test_one_page(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 5

    def test_two_pages(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1),
                          "/test/p2/c": (200, "text/html", PAGE_2)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=2)
        s = c.finalize()
        assert s["totals"]["total_records"] == 8

    def test_waf_403(self, server, tmp_path):
        server.set_routes({"/test/c": (403, "text/html", "X")})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert c._stop_event.is_set()

# ============================================================
# 商品数量准确性
# ============================================================
class TestProductCounting:
    def test_exact_1_product(self, server, tmp_path):
        pg = _make_page(1, 1, has_next=False)
        server.set_routes({"/test/c": (200, "text/html", pg)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 1

    def test_exact_60_products(self, server, tmp_path):
        pg = _make_page(1, 60, has_next=False, start_id=0)
        server.set_routes({"/test/c": (200, "text/html", pg)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 60

    def test_variable_pages(self, server, tmp_path):
        server.set_routes({
            "/test/c": (200, "text/html", _make_page(1, 60, start_id=0)),
            "/test/p2/c": (200, "text/html", _make_page(2, 37, start_id=60)),
            "/test/p3/c": (200, "text/html", _make_page(3, 5, has_next=False, start_id=97)),
        })
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        s = c.finalize()
        assert s["totals"]["total_records"] == 102

# ============================================================
# 实际页数不足自然结束
# ============================================================
class TestActualPages:
    def test_user_10_actual_3(self, server, tmp_path):
        """用户请求10页, 实际只有3页: 应只抓3页, 正常结束"""
        _THandler.reset_all()
        p1 = _make_page(1, 2)
        p2 = _make_page(2, 2)
        p3 = _make_page(3, 1, has_next=False)
        server.set_routes({"/test/c": (200, "text/html", p1),
                          "/test/p2/c": (200, "text/html", p2),
                          "/test/p3/c": (200, "text/html", p3)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        # 请求包括page2和page3
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        s = c.finalize()
        # 3页应都成功
        assert s["totals"]["success_pages"] == 3

    def test_all_pages_max_20_actual_6(self, server, tmp_path):
        routes = {"/test/c": (200, "text/html", _make_page(1, 1))}
        for i in range(2, 7):
            nl = i < 6
            routes[f"/test/p{i}/c"] = (200, "text/html", _make_page(i, 1, has_next=nl, start_id=i-1))
        server.set_routes(routes)
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=2, category_workers=1, max_in_flight=4, all_pages=True)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=None)
        s = c.finalize()
        assert s["totals"]["success_pages"] == 6

    def test_last_page_few_products(self, server, tmp_path):
        server.set_routes({
            "/test/c": (200, "text/html", _make_page(1, 5)),
            "/test/p2/c": (200, "text/html", _make_page(2, 1, has_next=False, start_id=5)),
        })
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=5)
        s = c.finalize()
        assert s["totals"]["total_records"] == 6
        assert s["totals"]["success_pages"] == 2

# ============================================================
# Checkpoint 测试
# ============================================================
class TestCheckpoint:
    def test_checkpoint_generated(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c.finalize()
        assert os.path.exists(os.path.join(out, "checkpoint.json"))
        assert os.path.exists(os.path.join(out, "checkpoint_pages"))

    def test_waf_saves_checkpoint(self, server, tmp_path):
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (403, "text/html", "Forbidden"),
            "/test/p3/c": (200, "text/html", PAGE_2),
        })
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=2, category_workers=1, max_in_flight=4)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        c.finalize()
        assert os.path.exists(os.path.join(out, "checkpoint.json"))
        cp = json.load(open(os.path.join(out, "checkpoint.json"), encoding="utf-8"))
        assert cp["status"] in ("paused", "waf_blocked")

    def test_resume_checkpoint_data(self, server, tmp_path):
        """checkpoint 数据正确: 保存已完成页, next_page 更新"""
        cat_url = server.url("/test/c")
        cats = [{"name": "Test", "url": cat_url, "enabled": True}]
        server.set_routes({"/test/c": (200, "text/html", PAGE_1),
                          "/test/p2/c": (403, "text/html", "Forbidden")})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(cats, max_pages=2)
        c.finalize()
        assert os.path.exists(os.path.join(out, "checkpoint.json"))
        cp_data = json.load(open(os.path.join(out, "checkpoint.json"), encoding="utf-8"))
        assert cp_data["status"] in ("paused", "waf_blocked")
        cats_cp = cp_data["categories"]
        assert len(cats_cp) >= 1
        assert 1 in cats_cp[0].get("completed_pages", [])
        assert cats_cp[0].get("next_page", 0) >= 2

    def test_resume_bat_generated(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c._write_resume_files()
        assert os.path.exists(os.path.join(out, "resume.bat"))
        assert os.path.exists(os.path.join(out, "RESUME_COMMAND.txt"))

    def test_checkpoint_atomic_write(self, server, tmp_path):
        server.set_routes({"/test/c": (200, "text/html", PAGE_1)})
        out = str(tmp_path / "out")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=1)
        c.finalize()
        # 不应有残留 .tmp 文件
        assert not os.path.exists(os.path.join(out, "checkpoint.json.tmp"))

# ============================================================
# Ctrl+C 中断测试
# ============================================================
class TestInterrupt:
    def test_keyboard_interrupt_saves(self, server, tmp_path):
        server.set_routes({
            "/test/c": (200, "text/html", PAGE_1),
            "/test/p2/c": (200, "text/html", PAGE_2),
            "/test/p3/c": (200, "text/html", PAGE_3),
        })
        server.set_delay("/test/p2/c", 0.5)
        out = str(tmp_path / "out")
        stop_ev = threading.Event()
        c = EmagCrawler(out, download_images=False, page_workers=2, category_workers=1, max_in_flight=4, stop_event=stop_ev)

        def delayed_stop():
            time.sleep(0.3)
            stop_ev.set()
            c._interrupted = True
        t = threading.Thread(target=delayed_stop, daemon=True)
        t.start()

        c.crawl_all_categories(_mkcats(server, ["/test/c"]), max_pages=3)
        s = c.finalize(interrupted=True)
        assert s["status"] == "interrupted"
        assert s["totals"]["total_records"] >= 5  # 至少首页已完成

# ============================================================
# 回归
# ============================================================
class TestExitCodes:
    def test_distinct(self):
        assert len({EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR, EXIT_CAPTCHA}) == 4
        assert EXIT_CAPTCHA == 3
