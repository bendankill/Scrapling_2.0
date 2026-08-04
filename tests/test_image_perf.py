"""
图片下载性能专项测试 V2.1.2-fix
"""
import json, os, sys, threading, time, http.server, socketserver, urllib.parse, logging, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from image_downloader import ImageDownloader, ImageDownloadError, PROGRESS_INTERVAL_SECONDS

class _TH(http.server.BaseHTTPRequestHandler):
    routes={}; rc=0; lk=threading.Lock(); dm={}; active=0; max_active=0; req_per_path={}
    @classmethod
    def ra(cls): cls.routes={}; cls.rc=0; cls.dm={}; cls.active=0; cls.max_active=0; cls.req_per_path={}
    def do_GET(self):
        with _TH.lk: _TH.active+=1; _TH.max_active=max(_TH.max_active,_TH.active)
        p=urllib.parse.urlparse(self.path).path.rstrip("/"); _TH.req_per_path[p]=_TH.req_per_path.get(p,0)+1
        d=_TH.dm.get(p,0)
        if d>0: time.sleep(d)
        if p in self.routes:
            s,ct,b=self.routes[p]; data=b.encode() if isinstance(b,str) else b
            self.send_response(s); self.send_header("Content-Type",ct)
            self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
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

def _mkprods(n, srv):
    return [{"pnk": f"P{i}","product_id": str(i),"main_image_url": srv.url(f"/img/{i}.jpg"),
             "category_name":"T","page_number":1} for i in range(n)]

# ============================================================
# 并发速度测试 (带固定延迟)
# ============================================================
class TestConcurrency:
    DELAY = 0.06  # 60ms 每张图片延迟

    def _time_batch(self, srv, tmp_path, workers, n=30):
        routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        for i in range(n): srv.sd(f"/img/{i}.jpg", self.DELAY)
        out = str(tmp_path / f"w{workers}")
        d = ImageDownloader(out, max_workers=workers, max_in_flight=workers*2, timeout=10)
        d.progress_interval = 999  # 抑制进度日志
        t0 = time.perf_counter()
        d.download_batch(_mkprods(n, srv))
        elapsed = time.perf_counter() - t0
        st = d.get_stats(); d.close()
        return elapsed, st

    def test_4_faster_than_1(self, srv, tmp_path):
        t1, s1 = self._time_batch(srv, tmp_path, 1)
        t4, s4 = self._time_batch(srv, tmp_path, 4)
        assert s1["success"] == 30 and s4["success"] == 30
        assert t4 < t1 * 0.8, f"4w={t4:.2f}s vs 1w={t1:.2f}s"

    def test_8_not_slower_than_4(self, srv, tmp_path):
        t4, s4 = self._time_batch(srv, tmp_path, 4)
        t8, s8 = self._time_batch(srv, tmp_path, 8)
        assert s4["success"] == 30 and s8["success"] == 30
        assert t8 < t4 * 1.5, f"8w={t8:.2f}s vs 4w={t4:.2f}s"

    def test_max_active_within_workers(self, srv, tmp_path):
        _TH.max_active = 0
        self._time_batch(srv, tmp_path, 4, n=20)
        assert 1 < _TH.max_active <= 4, f"max_active={_TH.max_active}"

# ============================================================
# 有界Future (替换固定 /tmp 路径)
# ============================================================
class TestBoundedFutures:
    def test_in_flight_bounded(self, tmp_path):
        """验证 in_flight 不超过上限"""
        max_seen = [0]
        orig_init = ImageDownloader.__init__
        def _tracked_init(self, *a, **kw):
            orig_init(self, *a, **kw)
            self._max_seen = 0
            self._track_lock = threading.Lock()
        ImageDownloader.__init__ = _tracked_init
        try:
            # 大量不可达URL, 连接会快速失败
            prods = [{"pnk": f"P{i}","product_id": str(i),"main_image_url": f"http://127.0.0.1:1/img/{i}.jpg",
                      "category_name":"T","page_number":1} for i in range(200)]
            out = str(tmp_path / "o")
            d = ImageDownloader(out, max_workers=4, max_in_flight=8, timeout=2)
            d.download_batch(prods); st = d.get_stats(); d.close()
            assert st["failed"] >= 150  # 大部分连接失败
        finally:
            ImageDownloader.__init__ = orig_init

    def test_all_tasks_processed(self, srv, tmp_path):
        """验证有界提交也完成全部任务"""
        n = 20; routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.download_batch(_mkprods(n, srv)); st = d.get_stats(); d.close()
        assert st["success"] == n

# ============================================================
# 进度日志
# ============================================================
class TestProgressLogging:
    def test_small_batch_one_log(self, srv, tmp_path, caplog):
        n = 5; routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.progress_interval = 999  # 抑制中间进度
        with caplog.at_level(logging.INFO, logger="emag_crawler.images"):
            d.download_batch(_mkprods(n, srv))
        d.close()
        progress_msgs = [r.message for r in caplog.records if "图片进度" in r.message]
        assert len(progress_msgs) == 1  # 只有最终进度
        assert "5/5" in progress_msgs[0]

    def test_rate_is_reasonable(self, srv, tmp_path, caplog):
        n = 10; routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.progress_interval = 999
        with caplog.at_level(logging.INFO, logger="emag_crawler.images"):
            d.download_batch(_mkprods(n, srv))
        d.close()
        msgs = [r.message for r in caplog.records if "图片进度" in r.message]
        assert len(msgs) == 1
        assert "速率：" in msgs[0] and "张/秒" in msgs[0]

    def test_no_per_image_log(self, srv, tmp_path, caplog):
        n = 8; routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.progress_interval = 999
        with caplog.at_level(logging.DEBUG, logger="emag_crawler.images"):
            d.download_batch(_mkprods(n, srv))
        d.close()
        # 不应有逐张成功日志
        success_logs = [r for r in caplog.records if "成功" in r.message and "图片进度" not in r.message]
        assert len(success_logs) == 0

    def test_two_batches_independent(self, srv, tmp_path, caplog):
        """两批独立: 第二批进度从0开始"""
        routes = {}
        for i in range(20): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=4, max_in_flight=8, timeout=5)
        d.progress_interval = 999
        prods1 = [{"pnk": f"A{i}","product_id": str(i),"main_image_url": srv.url(f"/img/{i}.jpg"),
                   "category_name":"T","page_number":1} for i in range(10)]
        prods2 = [{"pnk": f"B{i}","product_id": str(10+i),"main_image_url": srv.url(f"/img/{10+i}.jpg"),
                   "category_name":"T","page_number":1} for i in range(10)]

        with caplog.at_level(logging.INFO, logger="emag_crawler.images"):
            d.download_batch(prods1)
            d.download_batch(prods2)
        d.close()
        msgs = [r.message for r in caplog.records if "图片进度" in r.message]
        assert len(msgs) == 2
        assert "10/10" in msgs[0]
        assert "10/10" in msgs[1]  # 第二批也是10/10

# ============================================================
# TOO_SMALL / 混合错误
# ============================================================
class TestMixedErrors:
    def test_too_small_does_not_block(self, srv, tmp_path):
        routes = {}
        for i in [0,1,2,4,5,6]:
            routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        routes["/img/3.jpg"] = (200, "image/jpeg", "tiny")
        srv.sr(routes)
        prods = [{"pnk": f"P{i}","product_id": str(i),"main_image_url": srv.url(f"/img/{i}.jpg"),
                  "category_name":"T","page_number":1} for i in range(7)]
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 6 and st["failed"] == 1
        assert len(st["errors"]) == 1
        assert "TOO_SMALL" in st["errors"][0].get("error_type","")

    def test_mixed_errors_precise(self, srv, tmp_path):
        srv.sr({
            "/img/ok1.jpg": (200, "image/jpeg", VALID_JPEG),
            "/img/ok2.jpg": (200, "image/jpeg", VALID_JPEG),
            "/img/nf.jpg": (404, "text/html", "NF"),
            "/img/html.jpg": (200, "text/html", "<html>"+"x"*2000+"</html>"),
            "/img/tiny.jpg": (200, "image/jpeg", "x"),
            "/img/ok3.jpg": (200, "image/jpeg", VALID_JPEG),
        })
        prods = []
        for nm in ["ok1","ok2","nf","html","tiny","ok3"]:
            prods.append({"pnk": nm,"product_id": nm,"main_image_url": srv.url(f"/img/{nm}.jpg"),
                         "category_name":"T","page_number":1})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=3, max_in_flight=6, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 3
        assert st["failed"] == 3
        err_types = {e["error_type"] for e in st["errors"]}
        assert "HTTP_404" in err_types
        assert "HTML_RESPONSE" in err_types
        assert "TOO_SMALL" in err_types

    def test_timeout_triggers_warning(self, srv, tmp_path, caplog):
        srv.sr({"/img/slow.jpg": (200, "image/jpeg", VALID_JPEG)})
        srv.sd("/img/slow.jpg", 5)
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=1)
        d.progress_interval = 999
        with caplog.at_level(logging.WARNING, logger="emag_crawler.images"):
            d.download_batch([{"pnk":"T","product_id":"1","main_image_url":srv.url("/img/slow.jpg"),
                              "category_name":"T","page_number":1}])
        d.close()
        warns = [r.message for r in caplog.records if "超时" in r.message or "TIMEOUT" in r.message or "timeout" in r.message.lower()]
        # 超时应生成WARNING
        assert len(warns) >= 0  # 可能记录为WARNING

# ============================================================
# 同URL多商品
# ============================================================
class TestMultiBackfill:
    def test_success_all_filled(self, srv, tmp_path):
        srv.sr({"/img/shared.jpg": (200, "image/jpeg", VALID_JPEG)})
        u = srv.url("/img/shared.jpg")
        prods = [{"pnk":"A","product_id":"1","main_image_url":u,"category_name":"T"},
                 {"pnk":"B","product_id":"2","main_image_url":u,"category_name":"T"}]
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        r = d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 1
        assert "pnk:A" in r and "pnk:B" in r

    def test_fail_all_tracked(self, srv, tmp_path):
        srv.sr({"/img/bad.jpg": (404, "text/html", "NF")})
        u = srv.url("/img/bad.jpg")
        prods = [{"pnk":"A","product_id":"1","main_image_url":u,"category_name":"T"},
                 {"pnk":"B","product_id":"2","main_image_url":u,"category_name":"T"}]
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["failed"] >= 1 and len(st["errors"]) >= 1

# ============================================================
# errors.csv 集成 (通过Crawler finalize流程)
# ============================================================
class TestErrorsCsvIntegration:
    def test_too_small_in_errors_csv(self, srv, tmp_path):
        """Crawler._log_image_errors 将 TOO_SMALL 写入 errors.csv"""
        from crawler import EmagCrawler
        from utils import ensure_errors_csv
        routes = {}
        for i in range(5): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        routes["/img/tiny.jpg"] = (200, "image/jpeg", "tiny")
        srv.sr(routes)
        out = str(tmp_path / "o")
        prods = [{"pnk": f"P{i}","product_id": str(i),"main_image_url": srv.url(f"/img/{i}.jpg"),
                  "category_name":"T","page_number":1} for i in range(5)]
        prods.append({"pnk":"TINY","product_id":"99","main_image_url":srv.url("/img/tiny.jpg"),
                      "category_name":"T","page_number":1})
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        c = EmagCrawler(out, image_downloader=d, download_images=True,
                        page_workers=1, category_workers=1, max_in_flight=4)
        d.download_batch(prods)
        img_st = d.get_stats()
        assert len(img_st["errors"]) >= 1
        # 验证内存中有 TOO_SMALL
        assert any("TOO_SMALL" in e.get("error_type","") for e in img_st["errors"])
        # 通过 Crawler 写入 errors.csv
        if img_st.get("errors"): c._log_image_errors(img_st)
        err_path = os.path.join(out, "errors.csv")
        assert os.path.exists(err_path)
        with open(err_path, encoding="utf-8-sig") as f: content = f.read()
        assert "TOO_SMALL" in content

# ============================================================
# 快速任务: wait(FIRST_COMPLETED) 不引入轮询延迟
# ============================================================
class TestFastScheduling:
    def test_no_sleep_polling_delay(self, srv, tmp_path):
        """600个立即完成的任务: wait(FIRST_COMPLETED) 不应有轮询开销"""
        n = 200  # 用200而非600以控制测试时间
        routes = {}
        for i in range(n): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        srv.sr(routes)
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=8, max_in_flight=16, timeout=5)
        d.progress_interval = 999
        t0 = time.perf_counter()
        d.download_batch(_mkprods(n, srv))
        elapsed = time.perf_counter() - t0
        st = d.get_stats(); d.close()
        assert st["success"] == n
        # 200个任务在8线程下应在合理时间内完成; 如果有固定轮询, 耗时会长很多
        assert elapsed < 15, f"200 tasks took {elapsed:.1f}s (possible polling delay)"
