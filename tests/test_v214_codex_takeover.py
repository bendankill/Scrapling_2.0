"""V2.1.4 Codex接管专项：HTTP 200分类、WAF可见性与诊断完整性。"""
import csv
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.parse

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import crawler as crawler_module
from crawler import EmagCrawler
from parser import parse_product_listing, select_product_cards
from utils import _is_visible_element


PRODUCT_CARD = """<div class="card-item card-standard js-product-data"
 data-product-id="{pid}" data-name="{title}" data-position="{position}"
 data-url="https://www.emag.ro/test-{pid}/pd/PNK{pid}/">
 <p class="product-new-price">{price},99 Lei</p></div>"""


def _product_page(pid="1", title="Produs normal"):
    return ("<html><head><title>Produse eMAG</title></head><body>" +
            PRODUCT_CARD.format(pid=pid, title=title, position=pid, price=pid) +
            "</body></html>")


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}
    requests = {}
    lock = threading.Lock()

    @classmethod
    def reset(cls):
        cls.routes = {}
        cls.requests = {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        with self.lock:
            self.requests[path] = self.requests.get(path, 0) + 1
        if path not in self.routes:
            self.send_response(404)
            self.end_headers()
            return
        status, content_type, body = self.routes[path]
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class LocalServer:
    def __init__(self):
        self.server = None
        self.port = 0

    def start(self):
        _Handler.reset()
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def set_routes(self, routes):
        _Handler.routes = routes

    def request_count(self, path):
        return _Handler.requests.get(path.rstrip("/"), 0)


@pytest.fixture
def server():
    srv = LocalServer()
    srv.start()
    time.sleep(0.03)
    try:
        yield srv
    finally:
        srv.stop()


def _categories(server, paths):
    return [{"name": f"Cat{idx}", "url": server.url(path), "enabled": True}
            for idx, path in enumerate(paths, 1)]


def _run(server, tmp_path, paths, routes, *, out_name="out"):
    server.set_routes(routes)
    output = str(tmp_path / out_name)
    crawler = EmagCrawler(output, download_images=False, page_workers=1,
                          category_workers=1, max_in_flight=2)
    crawler.crawl_all_categories(_categories(server, paths), max_pages=1)
    summary = crawler.finalize()
    return crawler, summary, output


class TestWafVisibility:
    def test_hidden_parent_captcha_does_not_block_product(self, server, tmp_path):
        hidden = ('<div style="display:none"><div id="captcha">'
                  '<p>Please verify you are human</p></div></div>')
        page = _product_page() .replace("</body>", hidden + "</body>")
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        assert summary["status"] == "completed"
        assert summary["totals"]["total_records"] == 1
        assert crawler.get_exit_code() == 0

    def test_script_captcha_string_does_not_block_product(self, server, tmp_path):
        page = _product_page().replace(
            "</head>", '<script>const message = "Please verify you are human";</script></head>')
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        assert summary["status"] == "completed"
        assert summary["totals"]["total_records"] == 1
        assert crawler.get_exit_code() == 0

    def test_hidden_parent_makes_unmarked_child_invisible(self):
        soup = BeautifulSoup(
            '<div hidden><section><div id="captcha">Verify</div></section></div>',
            "lxml")
        assert _is_visible_element(soup.select_one("#captcha")) is False

    def test_aria_hidden_captcha_does_not_block_product(self, server, tmp_path):
        hidden = '<div aria-hidden="TRUE"><div id="captcha">Please verify you are human</div></div>'
        page = _product_page().replace("</body>", hidden + "</body>")
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        assert summary["status"] == "completed"
        assert summary["totals"]["total_records"] == 1
        assert crawler.get_exit_code() == 0

    def test_visibility_hidden_captcha_does_not_block_product(self, server, tmp_path):
        hidden = '<div style="visibility: hidden"><div id="captcha">Human verification</div></div>'
        page = _product_page().replace("</body>", hidden + "</body>")
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        assert summary["status"] == "completed"
        assert summary["totals"]["total_records"] == 1
        assert crawler.get_exit_code() == 0

    def test_visible_captcha_title_is_waf(self, server, tmp_path):
        page = _product_page().replace("Produse eMAG", "eMAG Captcha")
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        assert summary["status"] == "waf_blocked"
        assert summary["totals"]["completed_categories"] == 0
        assert crawler.get_exit_code() == 3

    def test_visible_captcha_ui_and_body_is_waf(self, server, tmp_path):
        visible = '<div id="captcha"><p>Please verify you are human</p></div>'
        page = _product_page().replace("</body>", visible + "</body>")
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        assert summary["status"] == "waf_blocked"
        assert summary["totals"]["completed_categories"] == 0
        assert crawler.get_exit_code() == 3

    def test_aws_waf_preload_script_does_not_block_product(self, server, tmp_path):
        script = '<script>AwsWafCaptcha.init(); var token="aws-waf-token";</script>'
        page = _product_page().replace("</head>", script + "</head>")
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        assert summary["status"] == "completed"
        assert summary["totals"]["total_records"] == 1
        assert crawler.get_exit_code() == 0

    def test_access_denied_product_title_does_not_block(self, server, tmp_path):
        page = _product_page(title="Access Denied tricou")
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        assert summary["status"] == "completed"
        assert summary["totals"]["total_records"] == 1
        assert crawler.get_exit_code() == 0


class TestHttp200Classification:
    def test_fallback_card_v2_title_is_saved(self, server, tmp_path):
        page = ("<html><body><div class=\"card-item card-standard js-product-data\""
                " data-product-id=\"7\" data-position=\"1\""
                " data-url=\"https://www.emag.ro/fallback/pd/PNK7/\">"
                "<h2 class=\"card-v2-title\">Fallback Fixture Title</h2>"
                "<p class=\"product-new-price\">10,99 Lei</p></div></body></html>")
        crawler, summary, output = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        data = json.load(open(os.path.join(output, "products.json"), encoding="utf-8"))
        assert crawler.get_exit_code() == 0
        assert summary["totals"]["total_records"] == 1
        assert data[0]["title"] == "Fallback Fixture Title"

    def test_tricouri_real_structure_fixture_is_exact_product_page(self):
        path = os.path.join(os.path.dirname(__file__), "fixtures",
                            "tricouri_sport_fashion_minimal.html")
        html = open(path, encoding="utf-8").read()
        soup = BeautifulSoup(html, "lxml")
        cards = select_product_cards(soup)
        products = parse_product_listing(
            html, "Tricouri Sport", "https://www.emag.ro/tricouri-sport/c",
            "https://www.emag.ro/tricouri-sport/c", 1)
        assert len(cards) == 1
        assert len(products) == 1
        assert products[0].pnk == "D1NG80MBM"
        assert products[0].product_id == "38726680"
        assert products[0].title == "Tricou sport Fixture"

    def test_three_category_schedule_continues_after_tricouri(self, server, tmp_path):
        fixture = os.path.join(os.path.dirname(__file__), "fixtures",
                               "tricouri_sport_fashion_minimal.html")
        fashion = open(fixture, encoding="utf-8").read()
        crawler, summary, output = _run(
            server, tmp_path, ["/a/c", "/tricouri-sport/c", "/b/c"], {
                "/a/c": (200, "text/html", _product_page("1", "A")),
                "/tricouri-sport/c": (200, "text/html", fashion),
                "/b/c": (200, "text/html", _product_page("2", "B")),
            })
        data = json.load(open(os.path.join(output, "products.json"), encoding="utf-8"))
        assert crawler.get_exit_code() == 0
        assert summary["status"] == "completed"
        assert summary["totals"]["completed_categories"] == 3
        assert summary["totals"]["total_records"] == 3
        assert server.request_count("/b/c") == 1
        assert {item["title"] for item in data} == {"A", "Tricou sport Fixture", "B"}

    def test_all_cards_failed_writes_each_error_then_page_error(
            self, server, tmp_path, monkeypatch):
        page = ("<html><body>" +
                PRODUCT_CARD.format(pid="101", title="A", position="1", price="10") +
                PRODUCT_CARD.format(pid="102", title="B", position="2", price="20") +
                "</body></html>")

        def fail_card(card, *args, **kwargs):
            raise ValueError(f"broken card {card.get('data-product-id')}")

        monkeypatch.setattr(crawler_module, "_parse_product_card", fail_card)
        crawler, summary, output = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", page)})
        with open(os.path.join(output, "errors.csv"), encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        card_rows = [row for row in rows if row["错误类型"] == "ValueError"]
        page_rows = [row for row in rows if row["错误类型"] == "ALL_PARSE_FAILED"]
        assert crawler.get_exit_code() == 2
        assert summary["status"] == "network_error"
        assert summary["totals"]["success_pages"] == 0
        assert summary["totals"]["failed_pages"] == 1
        assert summary["totals"]["completed_categories"] == 0
        assert len(card_rows) == 2
        assert {row["卡片位置"] for row in card_rows} == {"1", "2"}
        assert {row["商品键"] for row in card_rows} == {"101", "102"}
        assert all("broken card" in row["错误详情"] for row in card_rows)
        assert len(page_rows) == 1
        assert page_rows[0]["错误详情"] == "All 2 cards failed to parse"

    def test_unknown_http200_writes_html_json_diagnostics(
            self, server, tmp_path, capsys):
        page = ("<html><head><title>Mystery Page</title></head><body><main>"
                "Unrecognized content without products or block evidence."
                "</main></body></html>")
        crawler, summary, output = _run(server, tmp_path, ["/mystery/c"], {
            "/mystery/c": (200, "text/html; charset=UTF-8", page)})
        diag_dir = os.path.join(output, "diagnostics")
        html_files = [p for p in os.listdir(diag_dir)
                      if p.startswith("unknown_http200_page_") and p.endswith(".html")]
        json_files = [p for p in os.listdir(diag_dir)
                      if p.startswith("unknown_http200_page_") and p.endswith(".json")]
        diag = json.load(open(os.path.join(diag_dir, json_files[0]), encoding="utf-8"))
        stderr = capsys.readouterr().err
        assert crawler.get_exit_code() == 2
        assert summary["status"] == "network_error"
        assert summary["totals"]["completed_categories"] == 0
        assert len(html_files) == 1
        assert len(json_files) == 1
        assert diag["fatal_error_type"] == "UNKNOWN_HTTP200_PAGE"
        assert diag["http_status"] == 200
        assert diag["page_title"] == "Mystery Page"
        assert diag["candidate_cards"] == 0
        assert diag["parsed_products"] == 0
        assert diag["parse_failed"] == 0
        assert diag["request_url"].endswith("/mystery/c")
        assert diag["final_url"].endswith("/mystery/c")
        assert "错误类型: UNKNOWN_HTTP200_PAGE" in stderr
        assert "诊断文件:" in stderr

    def test_explicit_empty_category_completes_and_continues(self, server, tmp_path):
        empty = ("<html><head><title>Fara produse</title></head><body>"
                 "<main>Nu am găsit niciun produs în această categorie.</main>"
                 "</body></html>")
        crawler, summary, _ = _run(server, tmp_path, ["/empty/c", "/b/c"], {
            "/empty/c": (200, "text/html", empty),
            "/b/c": (200, "text/html", _product_page("2", "B")),
        })
        assert crawler.get_exit_code() == 0
        assert summary["status"] == "completed"
        assert summary["totals"]["completed_categories"] == 2
        assert summary["totals"]["total_records"] == 1
        assert summary["categories"][0]["stop_reason"] == "empty_category"
        assert server.request_count("/b/c") == 1

    def test_http200_waf_preserves_prior_data_and_stops_next(self, server, tmp_path):
        waf = ("<html><head><title>Security</title></head><body>"
               "<div id=\"captcha\"><p>Please verify you are human</p></div>"
               "</body></html>")
        crawler, summary, output = _run(
            server, tmp_path, ["/a/c", "/waf/c", "/c/c"], {
                "/a/c": (200, "text/html", _product_page("1", "A")),
                "/waf/c": (200, "text/html", waf),
                "/c/c": (200, "text/html", _product_page("3", "C")),
            })
        data = json.load(open(os.path.join(output, "products.json"), encoding="utf-8"))
        assert crawler.get_exit_code() == 3
        assert summary["status"] == "waf_blocked"
        assert summary["totals"]["completed_categories"] == 1
        assert len(data) == 1
        assert data[0]["title"] == "A"
        assert server.request_count("/c/c") == 0


class TestSingleDomConstruction:
    @staticmethod
    def _count_dom(monkeypatch):
        calls = {"count": 0}
        original = EmagCrawler._parse_html_once

        def counted(html):
            calls["count"] += 1
            return original(html)

        monkeypatch.setattr(EmagCrawler, "_parse_html_once", staticmethod(counted))
        return calls

    def test_normal_http200_builds_dom_once(self, server, tmp_path, monkeypatch):
        calls = self._count_dom(monkeypatch)
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", _product_page())})
        assert crawler.get_exit_code() == 0
        assert summary["status"] == "completed"
        assert calls["count"] == 1

    def test_http200_waf_builds_dom_once(self, server, tmp_path, monkeypatch):
        calls = self._count_dom(monkeypatch)
        waf = '<html><head><title>eMAG Captcha</title></head><body>Blocked</body></html>'
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (200, "text/html", waf)})
        assert crawler.get_exit_code() == 3
        assert summary["status"] == "waf_blocked"
        assert calls["count"] == 1

    def test_http_status_waf_builds_no_dom(self, server, tmp_path, monkeypatch):
        calls = self._count_dom(monkeypatch)
        crawler, summary, _ = _run(server, tmp_path, ["/a/c"], {
            "/a/c": (403, "text/html", "Forbidden")})
        assert crawler.get_exit_code() == 3
        assert summary["status"] == "waf_blocked"
        assert calls["count"] == 0
