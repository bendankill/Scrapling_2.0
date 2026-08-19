"""V2.2.1 专项：列表页真实类目证据、旁路缓存、隔离、性能与三格式。"""

import copy
import csv
import http.server
import json
import socketserver
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from openpyxl import load_workbook

import crawler as crawler_module
from category_hierarchy import (
    CategoryLevelRegistry,
    CategoryPathEvidence,
    clean_category_levels,
    extract_page_category_evidence,
    normalize_category_name,
    normalize_category_url,
    validate_category_path,
)
from crawler import EmagCrawler, FetchResult
from exporters import Exporters
from models import ProductItem
from output_schema import CATEGORY_LEVEL_FIELDS, PRODUCT_OUTPUT_FIELD_MAP, product_to_output_dict


ROOT = Path(__file__).resolve().parent
BOXE_FIXTURE = ROOT / "fixtures" / "v221_boxe_listing_minimal.html"
SAMPLE_FIXTURE = ROOT / "fixtures" / "v220_product_sample.json"
FULL_LEVELS = [
    "TV, Audio-Video & Foto",
    "Audio HI-FI & Profesionale",
    "Audio Hi-Fi",
    "Boxe",
]


def _soup(html):
    return BeautifulSoup(html, "lxml")


def _product_card(index):
    return f"""<div class="card-item card-standard js-product-data"
      data-product-id="{index}" data-name="Produs {index}" data-position="{index}"
      data-url="https://www.emag.ro/produs-{index}/pd/PNK{index}/">
      <p class="product-new-price">{index},99 Lei</p></div>"""


def _listing_page(card_count=1, breadcrumb=True):
    crumb = ""
    if breadcrumb:
        crumb = """<nav aria-label="Breadcrumb"><ol class="breadcrumb">
          <li>eMAG</li><li><a href="/tv-audio-video-foto/c">TV, Audio-Video &amp; Foto</a></li>
          <li><a href="/audio-hi-fi-profesionale/c">Audio HI-FI &amp; Profesionale</a></li>
          <li><a href="/audio-hi-fi/c">Audio Hi-Fi</a></li><li>Boxe</li>
        </ol></nav>"""
    cards = "".join(_product_card(index) for index in range(1, card_count + 1))
    return f"<html><head><title>Boxe</title></head><body>{crumb}<h1>Boxe</h1>{cards}</body></html>"


class TestPageEvidenceSources:
    def test_boxe_visible_breadcrumb_exact_four_levels(self):
        soup = _soup(BOXE_FIXTURE.read_text(encoding="utf-8"))
        evidence = extract_page_category_evidence(
            soup, "Boxe", "https://www.emag.ro/boxe/c?ref=bc")
        assert evidence.levels == tuple(FULL_LEVELS)
        assert evidence.source == "json_ld_breadcrumb"
        assert evidence.category_links_verified is True
        assert evidence.is_tentative is False

    def test_json_ld_breadcrumb_list_extracts_full_path(self):
        payload = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"position": 1, "name": "eMAG"},
                {"position": 2, "name": FULL_LEVELS[0]},
                {"position": 3, "name": FULL_LEVELS[1]},
                {"position": 4, "name": FULL_LEVELS[2]},
                {"position": 5, "name": "Boxe"},
            ],
        }
        soup = _soup(
            "<html><head><script type='application/ld+json'>" +
            json.dumps(payload, ensure_ascii=False) +
            "</script></head><body><h1>Boxe</h1></body></html>")
        evidence = extract_page_category_evidence(
            soup, "Boxe", "https://www.emag.ro/boxe/c")
        assert evidence.levels == tuple(FULL_LEVELS)
        assert evidence.source == "json_ld_breadcrumb"

    def test_embedded_initial_state_category_path(self):
        payload = {"catalog": {"categoryPath": "/".join(FULL_LEVELS)}}
        html = ("<html><body><h1>Boxe</h1><script id='__INITIAL_STATE__'>" +
                "window.__INITIAL_STATE__ = " + json.dumps(payload, ensure_ascii=False) +
                ";</script></body></html>")
        evidence = extract_page_category_evidence(
            _soup(html), "Boxe", "https://www.emag.ro/boxe/c")
        assert evidence.levels == tuple(FULL_LEVELS)
        assert evidence.source == "embedded_category_state"
        assert evidence.is_tentative is True

    def test_parent_only_breadcrumb_appends_only_with_category_links(self):
        html = """<html><body><nav aria-label="Breadcrumb"><ol>
          <li>eMAG</li><li><a href="/tv-audio-video-foto/c">TV, Audio-Video &amp; Foto</a></li>
          <li><a href="/audio-hi-fi-profesionale/c">Audio HI-FI &amp; Profesionale</a></li>
          <li><a href="/audio-hi-fi/c">Audio Hi-Fi</a></li>
        </ol></nav><h1>Boxe</h1></body></html>"""
        evidence = extract_page_category_evidence(
            _soup(html), "Boxe", "https://www.emag.ro/boxe/c")
        assert list(evidence.levels) == FULL_LEVELS

    def test_parent_only_plain_breadcrumb_is_not_authorized_by_h1(self):
        html = """<html><body><nav aria-label="Breadcrumb"><ol>
          <li>eMAG</li><li>TV, Audio-Video &amp; Foto</li><li>Audio Hi-Fi</li>
        </ol></nav><h1>Boxe</h1></body></html>"""
        evidence = extract_page_category_evidence(
            _soup(html), "Boxe", "https://www.emag.ro/catalog-audio/c")
        assert evidence is None

    def test_existing_current_category_is_not_appended_twice(self):
        html = _listing_page(0, breadcrumb=True)
        evidence = extract_page_category_evidence(
            _soup(html), "Boxe", "https://www.emag.ro/boxe/c")
        assert list(evidence.levels) == FULL_LEVELS
        assert list(evidence.levels).count("Boxe") == 1

    def test_more_complete_consistent_page_source_wins(self):
        payload = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"position": index + 1, "name": name}
                for index, name in enumerate(["eMAG", *FULL_LEVELS])
            ],
        }
        html = """<html><head><script type="application/ld+json">{}</script></head>
          <body><nav aria-label="Breadcrumb"><ol><li>eMAG</li>
          <li>TV, Audio-Video &amp; Foto</li><li>Boxe</li></ol></nav>
          <h1>Boxe</h1></body></html>""".format(json.dumps(payload, ensure_ascii=False))
        evidence = extract_page_category_evidence(
            _soup(html), "Boxe", "https://www.emag.ro/boxe/c")
        assert list(evidence.levels) == FULL_LEVELS
        assert evidence.source == "json_ld_breadcrumb"

    def test_inconsistent_path_is_rejected(self):
        html = """<html><body><nav aria-label="Breadcrumb"><ol>
          <li>eMAG</li><li>Electronice</li><li>Televizoare</li>
        </ol></nav></body></html>"""
        assert extract_page_category_evidence(
            _soup(html), "Boxe", "https://www.emag.ro/not-boxe/c") is None

    def test_hidden_breadcrumb_is_not_page_evidence(self):
        html = """<html><body><div style="display:none"><nav aria-label="Breadcrumb">
          <ol><li>eMAG</li><li>Electronice</li><li>Boxe</li></ol>
        </nav></div><h1>Boxe</h1></body></html>"""
        assert extract_page_category_evidence(
            _soup(html), "Boxe", "https://www.emag.ro/boxe/c") is None

    def test_multiple_website_roots_and_duplicate_levels_are_removed(self):
        assert clean_category_levels([
            "Home", "Acasă", "eMAG", "Audio Hi-Fi", "Audio Hi Fi", "Boxe", "Boxe",
        ]) == ["Audio Hi-Fi", "Boxe"]

    @pytest.mark.parametrize("variant", [
        "audio hi fi", "AUDIO-HI-FI", "Audio, Hi-Fi", "  Audio   Hi Fi  ",
    ])
    def test_name_consistency_ignores_case_punctuation_hyphen_and_spaces(self, variant):
        assert normalize_category_name(variant) == normalize_category_name("Audio Hi-Fi")


class TestCandidateFallbackAndBoundaries:
    def test_verified_page_levels_override_incomplete_card_paths(self):
        extra = {
            "favorite_data": {"category_trail": "TV/Audio/Boxe"},
            "data-category-trail": "Marketplace/Boxe",
        }
        output = product_to_output_dict(
            {"category_name": "Boxe", "extra": extra}, category_levels=FULL_LEVELS)
        assert [output[field] for field in CATEGORY_LEVEL_FIELDS[:4]] == FULL_LEVELS
        assert "五级类" not in output

    def test_missing_page_source_falls_back_to_favorite_data(self):
        output = product_to_output_dict({
            "category_name": "Boxe",
            "extra": {"favorite_data": {"category_trail": "Electronice/Audio/Boxe"}},
        })
        assert [output[field] for field in CATEGORY_LEVEL_FIELDS[:3]] == [
            "Electronice", "Audio", "Boxe"]

    def test_missing_favorite_path_falls_back_to_card_attribute(self):
        output = product_to_output_dict({
            "category_name": "Boxe",
            "extra": {"data-category-trail": "Electronice/Audio/Boxe"},
        })
        assert [output[field] for field in CATEGORY_LEVEL_FIELDS[:3]] == [
            "Electronice", "Audio", "Boxe"]

    def test_all_inconsistent_sources_produce_no_guessed_levels(self):
        output = product_to_output_dict({
            "category_name": "Boxe",
            "extra": {
                "favorite_data": {"category_trail": "Electronice/TV"},
                "data-category-trail": "Marketplace/Casti",
            },
        }, category_levels=["Electrocasnice", "Frigidere"])
        assert not set(CATEGORY_LEVEL_FIELDS).intersection(output)

    def test_invalid_page_evidence_falls_back_to_valid_favorite_path(self):
        output = product_to_output_dict({
            "category_name": "Boxe",
            "extra": {"favorite_data": {"category_trail": "Electronice/Audio/Boxe"}},
        }, category_levels=["Electrocasnice", "Frigidere"])
        assert [output[field] for field in CATEGORY_LEVEL_FIELDS[:3]] == [
            "Electronice", "Audio", "Boxe"]

    @pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6])
    def test_one_to_six_levels_only_emit_up_to_five(self, count):
        levels = [f"L{index}" for index in range(1, count + 1)]
        category_name = levels[-1]
        output = product_to_output_dict(
            {"category_name": category_name}, category_levels=levels)
        expected = levels[:5] if count <= 5 else []
        if count <= 5:
            assert [output[field] for field in CATEGORY_LEVEL_FIELDS[:count]] == expected
            assert not set(CATEGORY_LEVEL_FIELDS[count:]).intersection(output)
        else:
            # 完整六级路径先验证真实末级，再只导出前五级。
            assert [output[field] for field in CATEGORY_LEVEL_FIELDS] == levels[:5]

    def test_whitespace_empty_segments_romanian_and_repeats(self):
        levels = validate_category_path(
            " Acasă // Casă, Grădină / Îngrijire copii / Îngrijire-copii / Jucării ",
            "Jucării",
        )
        assert levels == ["Casă, Grădină", "Îngrijire copii", "Jucării"]


class TestRegistryConcurrencyAndIsolation:
    def test_same_normalized_url_hits_across_query_and_page(self):
        registry = CategoryLevelRegistry()
        evidence = CategoryPathEvidence(tuple(FULL_LEVELS), "visible_breadcrumb", 300)
        assert registry.register("https://www.emag.ro/boxe/c?ref=bc", evidence)
        assert normalize_category_url("https://emag.ro/boxe/p2/c?x=1") == normalize_category_url(
            "https://www.emag.ro/boxe/c?ref=bc")
        assert registry.get("https://emag.ro/boxe/p2/c?x=1") == evidence

    def test_different_category_urls_never_share_levels(self):
        registry = CategoryLevelRegistry()
        boxe = CategoryPathEvidence(tuple(FULL_LEVELS), "visible_breadcrumb", 300)
        casti = CategoryPathEvidence(("Electronice", "Casti"), "json_ld_breadcrumb", 300)
        registry.register("https://www.emag.ro/boxe/c", boxe)
        registry.register("https://www.emag.ro/casti/c", casti)
        assert registry.get("https://www.emag.ro/boxe/c") == boxe
        assert registry.get("https://www.emag.ro/casti/c") == casti

    def test_later_shorter_path_cannot_overwrite_complete_path(self):
        registry = CategoryLevelRegistry()
        complete = CategoryPathEvidence(tuple(FULL_LEVELS), "visible_breadcrumb", 300)
        shorter = CategoryPathEvidence(("Audio", "Boxe"), "visible_breadcrumb", 300)
        assert registry.register("https://www.emag.ro/boxe/c", complete)
        assert registry.register("https://www.emag.ro/boxe/c", shorter)
        assert registry.get("https://www.emag.ro/boxe/c") is None

    def test_later_more_complete_path_replaces_shorter_path(self):
        registry = CategoryLevelRegistry()
        shorter = CategoryPathEvidence(
            ("Audio", "Boxe"), "embedded_category_state", 170,
            is_tentative=True)
        complete = CategoryPathEvidence(
            tuple(FULL_LEVELS), "json_ld_breadcrumb", 420,
            category_links_verified=True, structured_breadcrumb=True)
        registry.register("https://www.emag.ro/boxe/c", shorter)
        assert registry.register("https://www.emag.ro/boxe/c", complete)
        assert registry.get("https://www.emag.ro/boxe/c") == complete

    def test_same_length_higher_reliability_replaces_weaker_source(self):
        registry = CategoryLevelRegistry()
        weak = CategoryPathEvidence(tuple(FULL_LEVELS), "embedded_category_state", 200)
        strong = CategoryPathEvidence(tuple(FULL_LEVELS), "visible_breadcrumb", 300)
        registry.register("https://www.emag.ro/boxe/c", weak)
        assert registry.register("https://www.emag.ro/boxe/c", strong)
        assert registry.get("https://www.emag.ro/boxe/c") == strong

    def test_multithreaded_registration_preserves_best_per_category(self):
        registry = CategoryLevelRegistry()
        candidates = [
            CategoryPathEvidence(
                ("Audio", "Boxe"), "embedded_category_state", 170,
                is_tentative=True),
            CategoryPathEvidence(
                tuple(FULL_LEVELS), "visible_breadcrumb", 400,
                category_links_verified=True),
        ] * 30
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(
                lambda evidence: registry.register(
                    "https://www.emag.ro/boxe/c?ref=thread", evidence),
                candidates,
            ))
        assert registry.get("https://www.emag.ro/boxe/c").levels == tuple(FULL_LEVELS)
        assert len(registry.snapshot()) == 1


class TestCrawlerPerformanceAndReuse:
    def test_sixty_cards_parse_one_soup_and_extract_hierarchy_once(self, tmp_path, monkeypatch):
        crawler = EmagCrawler(str(tmp_path / "out"), download_images=False)
        html = _listing_page(card_count=60, breadcrumb=True)
        calls = {"fetch": 0, "soup": 0, "hierarchy": 0}
        original_extract = crawler_module.extract_page_category_decision

        def fetch(url):
            calls["fetch"] += 1
            return FetchResult(html=html, status=200, request_url=url, final_url=url)

        def parse_once(raw_html):
            calls["soup"] += 1
            return BeautifulSoup(raw_html, "lxml")

        def extract_once(soup, name, url):
            calls["hierarchy"] += 1
            return original_extract(soup, name, url)

        monkeypatch.setattr(crawler, "_fetch_page", fetch)
        monkeypatch.setattr(crawler, "_parse_html_once", parse_once)
        monkeypatch.setattr(crawler_module, "extract_page_category_decision", extract_once)
        result = crawler._fetch_and_parse_page(
            "Boxe", "https://www.emag.ro/boxe/c", 1,
            "https://www.emag.ro/boxe/c")
        assert result.products_parsed == 60
        assert result.category_levels == FULL_LEVELS
        assert calls == {"fetch": 1, "soup": 1, "hierarchy": 1}

    def test_same_category_second_page_confirms_first_verified_levels(self, tmp_path, monkeypatch):
        crawler = EmagCrawler(str(tmp_path / "out"), download_images=False)
        pages = {
            1: _listing_page(card_count=1, breadcrumb=True),
            2: _listing_page(card_count=1, breadcrumb=False),
        }
        calls = {"hierarchy": 0}
        original_extract = crawler_module.extract_page_category_decision

        def fetch(url):
            page = 2 if "/p2/" in url else 1
            return FetchResult(html=pages[page], status=200, request_url=url, final_url=url)

        def extract_once(soup, name, url):
            calls["hierarchy"] += 1
            return original_extract(soup, name, url)

        monkeypatch.setattr(crawler, "_fetch_page", fetch)
        monkeypatch.setattr(crawler_module, "extract_page_category_decision", extract_once)
        first = crawler._fetch_and_parse_page(
            "Boxe", "https://www.emag.ro/boxe/c", 1,
            "https://www.emag.ro/boxe/c")
        second = crawler._fetch_and_parse_page(
            "Boxe", "https://www.emag.ro/boxe/c", 2,
            "https://www.emag.ro/boxe/p2/c")
        assert first.category_levels == second.category_levels == FULL_LEVELS
        assert first.category_levels_from_cache is False
        assert second.category_levels_from_cache is False
        assert calls["hierarchy"] == 2

    def test_concurrent_same_category_extracts_once(self, tmp_path, monkeypatch):
        crawler = EmagCrawler(str(tmp_path / "out"), download_images=False)
        soup = _soup(_listing_page(1, breadcrumb=True))
        calls = {"count": 0}
        lock = threading.Lock()
        original_extract = crawler_module.extract_page_category_decision

        def slow_extract(page_soup, name, url):
            with lock:
                calls["count"] += 1
            time.sleep(0.01)
            return original_extract(page_soup, name, url)

        monkeypatch.setattr(crawler_module, "extract_page_category_decision", slow_extract)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(
                lambda _index: crawler._get_or_extract_category_evidence(
                    soup, "Boxe", "https://www.emag.ro/boxe/c?ref=x",
                    page_number=1),
                range(20),
            ))
        assert calls["count"] == 1
        assert all(list(result[0].levels) == FULL_LEVELS for result in results)

    def test_concurrent_different_categories_remain_isolated(self, tmp_path):
        crawler = EmagCrawler(str(tmp_path / "out"), download_images=False)
        boxe_soup = _soup(_listing_page(1, breadcrumb=True))
        casti_html = """<html><body><nav aria-label="Breadcrumb"><ol>
          <li>eMAG</li><li><a href="/electronice/c">Electronice</a></li>
          <li><a href="/casti/c">Casti</a></li>
        </ol></nav><h1>Casti</h1></body></html>"""
        casti_soup = _soup(casti_html)
        jobs = [
            (boxe_soup, "Boxe", "https://www.emag.ro/boxe/c"),
            (casti_soup, "Casti", "https://www.emag.ro/casti/c"),
        ] * 10
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(
                lambda job: crawler._get_or_extract_category_evidence(*job),
                jobs,
            ))
        assert list(crawler.exporters.get_category_level_evidence(
            "https://www.emag.ro/boxe/c").levels) == FULL_LEVELS
        assert list(crawler.exporters.get_category_level_evidence(
            "https://www.emag.ro/casti/c").levels) == ["Electronice", "Casti"]


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}
    requests = {}
    lock = threading.Lock()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        with self.lock:
            self.requests[path] = self.requests.get(path, 0) + 1
        status, content_type, body = self.routes.get(
            path, (404, "text/plain", "Not found"))
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


@pytest.fixture
def local_server():
    _Handler.routes = {}
    _Handler.requests = {}
    server = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class TestProductionWriteAndDataIntegrity:
    def test_dynamic_local_http_and_three_format_round_trip(self, tmp_path, local_server):
        html = BOXE_FIXTURE.read_text(encoding="utf-8")
        _Handler.routes = {"/boxe/c": (200, "text/html; charset=utf-8", html)}
        category_url = f"http://127.0.0.1:{local_server.server_address[1]}/boxe/c?ref=test"
        output_dir = tmp_path / "actual"
        crawler = EmagCrawler(
            str(output_dir), download_images=False, page_workers=1,
            category_workers=1, max_in_flight=1)
        crawler.crawl_all_categories(
            [{"name": "Boxe", "url": category_url, "enabled": True}],
            max_pages=1,
        )
        summary = crawler.finalize()

        json_rows = json.loads((output_dir / "products.json").read_text(encoding="utf-8"))
        raw_csv = (output_dir / "products.csv").read_bytes()
        with (output_dir / "products.csv").open(
                encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        workbook = load_workbook(output_dir / "products.xlsx", data_only=True)
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        xlsx_rows = [
            dict(zip(headers, [cell.value for cell in row]))
            for row in sheet.iter_rows(min_row=2)
        ]
        workbook.close()

        assert summary["status"] == "completed"
        assert _Handler.requests == {"/boxe/c": 1}
        assert len(json_rows) == len(csv_rows) == len(xlsx_rows) == 1
        assert [json_rows[0][field] for field in CATEGORY_LEVEL_FIELDS[:4]] == FULL_LEVELS
        assert "五级类" not in json_rows[0]
        assert headers[3:7] == list(CATEGORY_LEVEL_FIELDS[:4])
        assert raw_csv.startswith(b"\xef\xbb\xbf")
        assert isinstance(json_rows[0]["前端价格"], float)
        assert isinstance(xlsx_rows[0]["前端价格"], float)
        assert isinstance(xlsx_rows[0]["评论分数"], float)
        assert isinstance(xlsx_rows[0]["评价数量"], int)
        assert json_rows[0]["链接打标"] == csv_rows[0]["链接打标"] == xlsx_rows[0]["链接打标"] == "Top Favorite"
        assert json_rows[0]["扩展信息"]["收藏数据"]["类目路径"].endswith("/Boxe")
        assert json_rows[0]["扩展信息"]["类目路径"].endswith("/Boxe")

    def test_non_category_fields_and_original_extra_are_unchanged(self, tmp_path):
        sample = json.loads(SAMPLE_FIXTURE.read_text(encoding="utf-8"))
        product = ProductItem(**copy.deepcopy(sample))
        internal_before = product.to_dict()
        baseline = product_to_output_dict(internal_before)
        exporter = Exporters(str(tmp_path / "out"))
        exporter.register_category_levels(
            product.category_url,
            CategoryPathEvidence(tuple(FULL_LEVELS), "visible_breadcrumb", 300),
        )
        exporter.add_product(product)
        output = exporter.get_json_buffer()[0]

        baseline_non_levels = {
            key: value for key, value in baseline.items()
            if key not in CATEGORY_LEVEL_FIELDS
        }
        output_non_levels = {
            key: value for key, value in output.items()
            if key not in CATEGORY_LEVEL_FIELDS
        }
        assert output_non_levels == baseline_non_levels
        assert product.to_dict() == internal_before
        assert exporter.get_products_sorted()[0] == internal_before
        allowed = set(PRODUCT_OUTPUT_FIELD_MAP.values()) | set(CATEGORY_LEVEL_FIELDS)
        assert set(output) <= allowed
        assert [output[field] for field in CATEGORY_LEVEL_FIELDS[:4]] == FULL_LEVELS
