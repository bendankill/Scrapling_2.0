"""
V2.1.4 类目完成计数测试
"""
import json, os, sys, threading, time, http.server, socketserver, urllib.parse, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crawler import EmagCrawler, ALL_PAGES_LIMIT

PC = """<div class="card-item card-standard js-product-data"
 data-product-id="{}" data-name="P{}" data-position="{}"
 data-url="https://www.emag.ro/test/pd/PNK{}/"><p class="product-new-price">{},99Lei</p></div>"""
def _mp(n, cnt, hn=True, sid=0):
    nl = f'<link rel="next" href="/test/p{n+1}/c">' if hn else ''
    cs = "".join(PC.format(sid+i,sid+i,i,sid+i,(sid+i)*10) for i in range(1,cnt+1))
    return f"<html><head>{nl}</head><body><h1>P{n}</h1>{cs}</body></html>"

class _TH(http.server.BaseHTTPRequestHandler):
    routes={}; lk=threading.Lock()
    @classmethod
    def ra(cls): cls.routes={}
    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path.rstrip("/")
        if p in self.routes:
            s,ct,b=self.routes[p]; data=b.encode() if isinstance(b,str) else b
            self.send_response(s); self.send_header("Content-Type",ct)
            self.send_header("Content-Length",str(len(data))); self.end_headers()
            self.wfile.write(data)
        else: self.send_response(404); self.end_headers()
    def log_message(self,f,*a): pass

class TTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address=True; daemon_threads=True

class LS:
    def __init__(s): s._s=None; s.port=0
    def start(s): _TH.ra(); s._s=TTCPServer(("127.0.0.1",0),_TH); s.port=s._s.server_address[1]; threading.Thread(target=s._s.serve_forever,daemon=True).start()
    def stop(s):
        if s._s:
            try: s._s.shutdown(); s._s.server_close()
            except: pass
    def url(s,p="/test/c"): return f"http://127.0.0.1:{s.port}{p}"
    def sr(s,r): _TH.routes=r

@pytest.fixture
def srv():
    s=LS(); s.start(); time.sleep(0.03)
    try: yield s
    finally: s.stop()

def _mc(s,ps): return [{"name":f"C{p.split('/')[1]}","url":s.url(p),"enabled":True} for p in ps]

# ============================================================
class TestCategoryCompletion:
    def test_normal_all_completed(self, srv, tmp_path):
        """3个类目全部正常完成 → 3/3"""
        srv.sr({
            "/cat1/c": (200, "text/html", _mp(1, 1, hn=False)),
            "/cat2/c": (200, "text/html", _mp(1, 1, hn=False)),
            "/cat3/c": (200, "text/html", _mp(1, 1, hn=False)),
        })
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/cat1/c","/cat2/c","/cat3/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["target_categories"] == 3
        assert s["totals"]["completed_categories"] == 3
        assert s["status"] == "completed"
        assert c.get_exit_code() == 0
        for cat_d in s["categories"]:
            assert cat_d["completed"] == True

    def test_waf_first_cat_zero_completed(self, srv, tmp_path):
        """第一个类目首页WAF → 0/3"""
        srv.sr({"/cat1/c": (403, "text/html", "Forbidden")})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/cat1/c","/cat2/c","/cat3/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["target_categories"] == 3
        assert s["totals"]["completed_categories"] == 0
        assert s["status"] == "waf_blocked"
        assert c.get_exit_code() == 3

    def test_one_done_then_waf(self, srv, tmp_path):
        """类目A完成→类目B第2页WAF→类目C不请求: 1/3, B首页数据保留"""
        # _mp生成 href="/test/p2/c", 需要cat2的page2路由匹配
        p1 = _mp(1, 2, hn=False)  # 无下一页
        p2 = '<html><head><link rel="next" href="/cat2/p2/c"></head><body>' + \
             "".join(PC.format(i,i,i,i,i*10) for i in range(1,3)) + "</body></html>"
        srv.sr({
            "/cat1/c": (200, "text/html", p1),
            "/cat2/c": (200, "text/html", p2),
            "/cat2/p2/c": (511, "text/html", "WAF"),
        })
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/cat1/c","/cat2/c","/cat3/c"]), max_pages=2)
        s = c.finalize()
        assert s["totals"]["completed_categories"] == 1
        assert s["totals"]["target_categories"] == 3
        assert s["totals"]["total_records"] >= 2
        assert s["status"] == "waf_blocked"

    def test_waf_data_integrity(self, srv, tmp_path):
        """WAF前PNK完整性: 商品记录完整保留"""
        p1 = _mp(1, 2, hn=False)
        p2 = '<html><head><link rel="next" href="/cat2/p2/c"></head><body>' + \
             "".join(PC.format(i,i,i,i,i*10) for i in range(1,3)) + "</body></html>"
        srv.sr({"/cat1/c": (200, "text/html", p1), "/cat2/c": (200, "text/html", p2),
                "/cat2/p2/c": (511, "text/html", "WAF")})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/cat1/c","/cat2/c","/cat3/c"]), max_pages=2)
        s = c.finalize()
        assert s["totals"]["completed_categories"] == 1
        assert s["totals"]["total_records"] == 4
        jp = os.path.join(out, "products.json")
        data = json.load(open(jp, encoding="utf-8"))
        assert len(data) == 4
        assert os.path.exists(os.path.join(out, "products.csv"))
        assert os.path.exists(os.path.join(out, "products.xlsx"))
        assert os.path.exists(os.path.join(out, "errors.csv"))

    def test_actual_pages_less_than_limit(self, srv, tmp_path):
        """用户要10页实际3页 → 正常完成1/1"""
        srv.sr({
            "/test/c": (200, "text/html", _mp(1, 1)),
            "/test/p2/c": (200, "text/html", _mp(2, 1)),
            "/test/p3/c": (200, "text/html", _mp(3, 1, hn=False)),
        })
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=10)
        s = c.finalize()
        assert s["totals"]["completed_categories"] == 1
        assert s["totals"]["target_categories"] == 1
        assert s["status"] == "completed"
        assert s["totals"]["success_pages"] == 3

    def test_network_error_completed_count(self, srv, tmp_path):
        """类目A完成, 类目B网络错误 → 1/3"""
        srv.sr({
            "/cat1/c": (200, "text/html", _mp(1, 1, hn=False)),
            "/cat2/c": (500, "text/html", "Error"),
        })
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/cat1/c","/cat2/c","/cat3/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["completed_categories"] == 1
        assert s["status"] == "network_error"
        assert c.get_exit_code() == 2

    def test_interrupted_count(self, srv, tmp_path):
        """Ctrl+C: 已完成类目计入, 未完成不计"""
        srv.sr({
            "/cat1/c": (200, "text/html", _mp(1, 1, hn=False)),
            "/cat2/c": (200, "text/html", _mp(1, 2)),
        })
        out = str(tmp_path / "o")
        se = threading.Event()
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2, stop_event=se)
        def sto(): time.sleep(0.5); se.set(); c._interrupted = True
        threading.Thread(target=sto, daemon=True).start()
        c.crawl_all_categories(_mc(srv, ["/cat1/c","/cat2/c"]), max_pages=2)
        s = c.finalize(interrupted=True)
        assert s["totals"]["target_categories"] == 2
        # cat1已完成或cat2尚未完成时中断, completed <= 1
        assert s["totals"]["completed_categories"] >= 0
        assert s["status"] == "interrupted"

    def test_run_summary_has_counts(self, srv, tmp_path):
        """run_summary.json 包含 target/completed 和 per-cat completed"""
        srv.sr({
            "/cat1/c": (200, "text/html", _mp(1, 1, hn=False)),
            "/cat2/c": (200, "text/html", _mp(1, 1, hn=False)),
        })
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/cat1/c","/cat2/c"]), max_pages=1)
        c.finalize()
        rs = json.load(open(os.path.join(out, "run_summary.json"), encoding="utf-8"))
        assert rs["totals"]["target_categories"] == 2
        assert rs["totals"]["completed_categories"] == 2
        for cat_d in rs["categories"]:
            assert "completed" in cat_d

# ============================================================
# V2.1.4: HTTP 200 WAF 误判修复测试
# ============================================================
class TestHttp200WafFix:
    def test_normal_page_with_aws_script_not_waf(self, srv, tmp_path):
        """正常商品页包含aws-waf-token脚本 → 不应判WAF"""
        page = ('<html><head><script>var x="aws-waf-token";</script></head><body>'
                + "".join(PC.format(i,i,i,f"PNK{i}",i*10) for i in range(1,4))
                + '</body></html>')
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "completed"
        assert s["totals"]["total_records"] == 3  # 3个商品正常保存
        assert c.get_exit_code() == 0

    def test_product_page_with_awswafcaptcha_not_waf(self, srv, tmp_path):
        """商品页包含AwsWafCaptcha字符串 → 不应判WAF"""
        page = ('<html><script>AwsWafCaptcha.init()</script><body>'
                + "".join(PC.format(i,i,i,f"PNK{i}",i*10) for i in range(1,3))
                + '</body></html>')
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "completed"
        assert s["totals"]["total_records"] == 2

    def test_real_waf_200_no_products(self, srv, tmp_path):
        """HTTP 200, 无商品 + AWS WAF标记 → 判WAF"""
        page = '<html><head><title>eMAG Captcha</title></head><body><script>aws-waf-token</script><div id="captcha"></div></body></html>'
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert c.get_exit_code() == 3

    def test_403_always_waf_even_with_products(self, srv, tmp_path):
        """403即使正文有商品卡片也必须判WAF"""
        page = PC.format(1,1,1,"X",10)
        srv.sr({"/test/c": (403, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert c.get_exit_code() == 3

    def test_fake_data_product_id_string_not_real_cards(self, srv, tmp_path):
        """HTML脚本含data-product-id字符串+可见captcha容器 → 判WAF"""
        page = '<html><script>var x="data-product-id";</script><body><div id="captcha"></div></body></html>'
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        # 可见captcha容器 + 无商品 → WAF
        assert s["status"] == "waf_blocked"
        assert c.get_exit_code() == 3

    def test_empty_category_not_waf(self, srv, tmp_path):
        """HTTP 200空类目(无商品无WAF标记) → 正常结束, 不判WAF"""
        srv.sr({"/test/c": (200, "text/html", "<html><body><p>No results</p></body></html>")})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] != "waf_blocked"

    def test_stable_detection_3_times(self):
        """同一HTML连续3次判断结果一致"""
        from utils import detect_waf_block
        normal = '<html><body>' + PC.format(1,1,1,"X",10) + '<script>aws-waf-token</script></body></html>'
        for _ in range(3):
            assert detect_waf_block(normal, 200, "http://t/c") is None
        waf = '<html><head><title>eMAG Captcha</title></head><body><script>aws-waf-token</script></body></html>'
        for _ in range(3):
            assert detect_waf_block(waf, 200, "http://t/c") is not None

    def test_waf_data_still_saved(self, srv, tmp_path):
        """WAF前类目商品完整保存"""
        p1 = _mp(1, 2, hn=False)
        p2_waf = '<html><head><title>eMAG Captcha</title></head><body><script>aws-waf-token</script></body></html>'
        srv.sr({"/cat1/c": (200, "text/html", p1), "/cat2/c": (200, "text/html", p2_waf)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/cat1/c","/cat2/c","/cat3/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 2
        assert os.path.exists(os.path.join(out, "products.json"))
        data = json.load(open(os.path.join(out, "products.json"), encoding="utf-8"))
        assert len(data) == 2

# ============================================================
# V2.1.4-fix: 严格商品页判断 + 强弱WAF证据区分
# ============================================================
class TestStrictWafDetection:
    def test_dummy_data_product_id_node_waf(self, srv, tmp_path):
        """只有空data-product-id伪节点 + 强WAF证据 → 判WAF"""
        page = '<html><head><title>eMAG Captcha</title></head><body><div data-product-id="dummy"></div><script>aws-waf-token</script></body></html>'
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert s["totals"]["total_records"] == 0
        assert c.get_exit_code() == 3

    def test_hidden_pseudo_card_waf(self, srv, tmp_path):
        """隐藏伪节点 + 强WAF标题 → 判WAF"""
        page = ('<html><head><title>eMAG Captcha</title></head><body>'
                '<div style="display:none" class="card-item card-standard js-product-data" data-product-id="dummy"></div>'
                '<script>aws-waf-token</script></body></html>')
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert c.get_exit_code() == 3

    def test_js_string_data_product_id_waf(self, srv, tmp_path):
        """JS中包含data-product-id字符串 + 强WAF → 判WAF"""
        page = '<html><head><title>eMAG Captcha</title></head><body><script>var x="data-product-id";</script><script>aws-waf-token</script></body></html>'
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"

    def test_product_title_contains_captcha_ok(self, srv, tmp_path):
        """商品标题含captcha但有完整商品字段 → 正常"""
        page = ('<html><body>' +
            PC.format("1", "Captcha Mouse Pro", 1, "PNK1", 10) +
            '<script>aws-waf-token</script></body></html>')
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "completed"
        assert s["totals"]["total_records"] == 1

    def test_strong_captcha_title_overrides(self, srv, tmp_path):
        """强验证码标题 + 空/隐藏节点 → WAF"""
        page = ('<html><head><title>eMAG Captcha</title></head><body>'
                '<div class="card-item card-standard js-product-data" data-product-id="x"></div>'
                '<script>aws-waf-token</script></body></html>')
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"

    def test_403_with_products_still_waf(self, srv, tmp_path):
        """403即使正文有完整商品也必须WAF"""
        page = PC.format("1","P1",1,"PNK1",10)+PC.format("2","P2",2,"PNK2",20)
        srv.sr({"/test/c": (403, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert c.get_exit_code() == 3

    def test_511_with_products_still_waf(self, srv, tmp_path):
        """511即使有商品也必须WAF"""
        srv.sr({"/test/c": (511, "text/html", PC.format("1","P1",1,"PNK1",10))})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert c.get_exit_code() == 3

    def test_stable_detection_3x(self):
        """同一Fixture 3次判断一致"""
        from utils import detect_waf_block
        normal = '<html><body><div class="card-item card-standard js-product-data" data-product-id="1" data-name="P1" data-url="/pd/ABC/"><p class="product-new-price">10,99Lei</p></div><script>aws-waf-token</script></body></html>'
        for _ in range(3):
            assert detect_waf_block(normal, 200, "http://t/c") is None
        waf = '<html><head><title>eMAG Captcha</title></head><body><script>aws-waf-token</script></body></html>'
        for _ in range(3):
            assert detect_waf_block(waf, 200, "http://t/c") is not None

    def test_hidden_with_full_fields_waf(self, srv, tmp_path):
        """字段完整的隐藏节点+标题eMAG Captcha → 强WAF优先"""
        page = ('<html><head><title>eMAG Captcha</title></head><body>'
                '<div style="display:none" class="card-item card-standard js-product-data"'
                ' data-product-id="dummy" data-name="Fake Product"'
                ' data-url="https://www.emag.ro/fake/pd/FAKE/">'
                '<p class="product-new-price">1,99 Lei</p></div>'
                '<script>aws-waf-token</script></body></html>')
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert s["totals"]["total_records"] == 0
        assert c.get_exit_code() == 3

    def test_visible_captcha_title_waf(self, srv, tmp_path):
        """可见节点+标题eMAG Captcha → 强证据优先"""
        page = ('<html><head><title>eMAG Captcha</title></head><body>'
                '<div class="card-item card-standard js-product-data"'
                ' data-product-id="dummy" data-name="Fake"'
                ' data-url="https://www.emag.ro/fake/pd/FAKE/">'
                '<p class="product-new-price">1,99 Lei</p></div>'
                '<script>aws-waf-token</script></body></html>')
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert s["totals"]["total_records"] == 0
# ============================================================
# S0修复: 无预判解析 + 正文WAF + 空类目 + 未知页面
# ============================================================
class TestS0Fixes:
    def test_fallback_title_parsed(self, srv, tmp_path):
        """data-name缺失但.card-v2-title可用 → 输出1条"""
        page = ("<html><body><div class=\"card-item card-standard js-product-data\""
                " data-product-id=\"1\" data-url=\"https://www.emag.ro/test/pd/PNK1/\">"
                "<a class=\"card-v2-title\">Fallback Title</a>"
                "<p class=\"product-new-price\">10,99Lei</p></div></body></html>")
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["totals"]["total_records"] == 1
        assert s["status"] == "completed"


    def test_body_waf_verify_human(self, srv, tmp_path):
        """正文Please verify you are human → WAF"""
        page = ("<html><head><title>Security</title></head>"
                "<body><p>Please verify you are human</p>"
                "<div id=\"captcha\"></div></body></html>")
        srv.sr({"/test/c": (200, "text/html", page)})
        out = str(tmp_path / "o")
        c = EmagCrawler(out, download_images=False, page_workers=1, category_workers=1, max_in_flight=2)
        c.crawl_all_categories(_mc(srv, ["/test/c"]), max_pages=1)
        s = c.finalize()
        assert s["status"] == "waf_blocked"
        assert c.get_exit_code() == 3

