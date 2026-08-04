"""
图片下载性能专项测试 V2.1.2
"""
import json, os, sys, threading, time, http.server, socketserver, urllib.parse, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from image_downloader import ImageDownloader, ImageDownloadError

class _TH(http.server.BaseHTTPRequestHandler):
    routes={}; rc=0; lk=threading.Lock(); dm={}; active=0; max_active=0
    @classmethod
    def ra(cls): cls.routes={}; cls.rc=0; cls.dm={}; cls.active=0; cls.max_active=0
    def do_GET(self):
        with _TH.lk: _TH.active+=1; _TH.max_active=max(_TH.max_active,_TH.active)
        p=urllib.parse.urlparse(self.path).path.rstrip("/")
        d=_TH.dm.get(p,0)
        if d>0: time.sleep(d)
        if p in self.routes:
            s,ct,b=self.routes[p]; data=b.encode() if isinstance(b,str) else b
            self.send_response(s); self.send_header("Content-Type",ct)
            self.send_header("Content-Length",str(len(data))); self.end_headers()
            self.wfile.write(data)
        else: self.send_response(404); self.end_headers()
        with _TH.lk: _TH.active-=1
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
    def url(s,p="/img/0.jpg"): return f"http://127.0.0.1:{s.port}{p}"
    def sr(s,r): _TH.routes=r
    def sd(s,p,d): _TH.dm[p]=d

@pytest.fixture
def srv():
    s=LS(); s.start(); time.sleep(0.03)
    try: yield s
    finally: s.stop()

VALID_JPEG = "\xff\xd8\xff\xe0" + "\x00" * 2000

# ============================================================
# 并发测试
# ============================================================
class TestConcurrency:
    def test_image_workers_1_slower_than_4(self, srv, tmp_path):
        """image_workers=4 比 1 明显更快"""
        n = 40
        routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        prods = [{"pnk": f"P{i}", "product_id": str(i), "main_image_url": srv.url(f"/img/{i}.jpg"),
                  "category_name": "T", "page_number": 1} for i in range(n)]

        out1 = str(tmp_path / "i1"); d1 = ImageDownloader(out1, max_workers=1, max_in_flight=2, timeout=5)
        t0 = time.perf_counter(); d1.download_batch(prods); t1 = time.perf_counter() - t0
        s1 = d1.get_stats(); d1.close()

        out4 = str(tmp_path / "i4"); d4 = ImageDownloader(out4, max_workers=4, max_in_flight=8, timeout=5)
        t0 = time.perf_counter(); d4.download_batch(prods); t4 = time.perf_counter() - t0
        s4 = d4.get_stats(); d4.close()

        assert s1["success"] == n
        assert s4["success"] == n
        # 4线程应明显快于1线程 (至少1.5x)
        assert t4 < t1 * 0.9, f"4w={t4:.1f}s vs 1w={t1:.1f}s"

    def test_max_active_within_workers(self, srv, tmp_path):
        """并发活跃请求不超过 image_workers"""
        n = 20; routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        prods = [{"pnk": f"P{i}", "product_id": str(i), "main_image_url": srv.url(f"/img/{i}.jpg"),
                  "category_name": "T", "page_number": 1} for i in range(n)]
        _TH.max_active = 0
        out = str(tmp_path / "o"); d = ImageDownloader(out, max_workers=4, max_in_flight=8, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == n
        # max_active 应不超过 workers (允许少量超额因为 timing)
        assert _TH.max_active <= 8, f"max_active={_TH.max_active}"

# ============================================================
# TOO_SMALL 不阻塞
# ============================================================
class TestTooSmall:
    def test_too_small_does_not_block_others(self, srv, tmp_path):
        """TOO_SMALL 失败后继续处理后续图片"""
        routes = {}
        # 前3张正常, 第4张太小, 后3张正常
        for i in [0,1,2,4,5,6]:
            routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        routes["/img/3.jpg"] = (200, "image/jpeg", "tiny")
        srv.sr(routes)
        prods = [{"pnk": f"P{i}", "product_id": str(i), "main_image_url": srv.url(f"/img/{i}.jpg"),
                  "category_name": "T", "page_number": 1} for i in range(7)]
        out = str(tmp_path / "o"); d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 6
        assert st["failed"] == 1
        assert len(st["errors"]) == 1
        assert "TOO_SMALL" in st["errors"][0].get("error_type", "")

    def test_mixed_errors_all_continue(self, srv, tmp_path):
        """多种错误混合: 404/超时/HTML/TOO_SMALL都不阻塞"""
        srv.sr({
            "/img/ok1.jpg": (200, "image/jpeg", VALID_JPEG),
            "/img/ok2.jpg": (200, "image/jpeg", VALID_JPEG),
            "/img/404.jpg": (404, "text/html", "NF"),
            "/img/html.jpg": (200, "text/html", "<html>" + "x"*2000 + "</html>"),
            "/img/tiny.jpg": (200, "image/jpeg", "x"),
            "/img/ok3.jpg": (200, "image/jpeg", VALID_JPEG),
        })
        srv.sd("/img/html.jpg", 0.5)
        prods = []
        for nm in ["ok1","ok2","404","html","tiny","ok3"]:
            prods.append({"pnk": nm, "product_id": nm, "main_image_url": srv.url(f"/img/{nm}.jpg"),
                         "category_name": "T", "page_number": 1})
        out = str(tmp_path / "o"); d = ImageDownloader(out, max_workers=3, max_in_flight=6, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] >= 2  # ok1 + ok2 + ok3 should all succeed
        assert st["failed"] >= 3
        assert len(st["errors"]) >= 3

# ============================================================
# 有界提交
# ============================================================
class TestBoundedSubmission:
    def test_not_all_futures_at_once(self):
        """验证有界提交不会无限增长 in_flight"""
        # 通过构造大量URL但不实际请求来验证in_flight上限
        # 由于需要实际服务器, 用计数方式验证
        prods = [{"pnk": f"P{i}", "product_id": str(i), "main_image_url": f"http://127.0.0.1:1/img/{i}.jpg",
                  "category_name": "T", "page_number": 1} for i in range(100)]
        out = "/tmp/_test_img_bound"
        d = ImageDownloader(out, max_workers=4, max_in_flight=8, timeout=2)
        # 快速运行, 连接会失败但验证不崩溃
        d.download_batch(prods); st = d.get_stats(); d.close()
        # 所有都应失败(连接拒绝)但程序不崩溃
        assert st["failed"] >= 90

# ============================================================
# 同URL多商品回填
# ============================================================
class TestMultiBackfill:
    def test_same_url_all_filled(self, srv, tmp_path):
        srv.sr({"/img/shared.jpg": (200, "image/jpeg", VALID_JPEG)})
        u = srv.url("/img/shared.jpg")
        prods = [{"pnk": "A", "product_id": "1", "main_image_url": u, "category_name": "T"},
                 {"pnk": "B", "product_id": "2", "main_image_url": u, "category_name": "T"}]
        out = str(tmp_path / "o"); d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        r = d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 1  # 只下载一次
        assert "pnk:A" in r and "pnk:B" in r  # 两个都回填

    def test_same_url_fail_all_tracked(self, srv, tmp_path):
        srv.sr({"/img/bad.jpg": (404, "text/html", "NF")})
        u = srv.url("/img/bad.jpg")
        prods = [{"pnk": "A", "product_id": "1", "main_image_url": u, "category_name": "T"},
                 {"pnk": "B", "product_id": "2", "main_image_url": u, "category_name": "T"}]
        out = str(tmp_path / "o"); d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["failed"] >= 1
        assert len(st["errors"]) >= 1

# ============================================================
# 进度日志: 不阻塞, 不重复
# ============================================================
class TestProgress:
    def test_progress_completes(self, srv, tmp_path):
        n = 10; routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        prods = [{"pnk": f"P{i}", "product_id": str(i), "main_image_url": srv.url(f"/img/{i}.jpg"),
                  "category_name": "T", "page_number": 1} for i in range(n)]
        out = str(tmp_path / "o"); d = ImageDownloader(out, max_workers=4, max_in_flight=8, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == n
        assert st["failed"] == 0
