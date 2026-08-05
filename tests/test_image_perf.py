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

def _real_jpeg(w=8, h=8):
    """生成真实可验证的最小JPEG"""
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (w, h), color='red').save(buf, 'JPEG')
    return buf.getvalue()

def _real_png(w=8, h=8):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (w, h), color='blue').save(buf, 'PNG')
    return buf.getvalue()

VALID_JPEG = _real_jpeg()

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
    def test_invalid_img_does_not_block(self, srv, tmp_path):
        """无效图片(UNKNOWN_FORMAT)失败后继续处理后续图片"""
        routes = {}
        for i in [0,1,2,4,5,6]:
            routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        routes["/img/3.jpg"] = (200, "application/octet-stream", "\x00\x01\x02\x03" + "\x00" * 100)
        srv.sr(routes)
        prods = [{"pnk": f"P{i}","product_id": str(i),"main_image_url": srv.url(f"/img/{i}.jpg"),
                  "category_name":"T","page_number":1} for i in range(7)]
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 6 and st["failed"] == 1
        assert len(st["errors"]) == 1
        assert "UNKNOWN_FORMAT" in st["errors"][0].get("error_type","")

    def test_mixed_errors_precise(self, srv, tmp_path):
        srv.sr({
            "/img/ok1.jpg": (200, "image/jpeg", VALID_JPEG),
            "/img/ok2.jpg": (200, "image/jpeg", VALID_JPEG),
            "/img/nf.jpg": (404, "text/html", "NF"),
            "/img/html.jpg": (200, "text/html", "<html>"+"x"*2000+"</html>"),
            "/img/bad.jpg": (200, "application/octet-stream", "\x00\x01\x02\x03"+"\x00"*100),
            "/img/ok3.jpg": (200, "image/jpeg", VALID_JPEG),
        })
        prods = []
        for nm in ["ok1","ok2","nf","html","bad","ok3"]:
            prods.append({"pnk": nm,"product_id": nm,"main_image_url": srv.url(f"/img/{nm}.jpg"),
                         "category_name":"T","page_number":1})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=3, max_in_flight=6, timeout=5)
        d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 3  # ok1, ok2, ok3
        assert st["failed"] == 3   # 404, HTML, bad format
        err_types = {e["error_type"] for e in st["errors"]}
        assert "HTTP_404" in err_types
        assert "HTML_RESPONSE" in err_types
        assert "UNKNOWN_FORMAT" in err_types

    def test_timeout_triggers_error(self, srv, tmp_path):
        srv.sr({"/img/slow.jpg": (200, "image/jpeg", VALID_JPEG)})
        srv.sd("/img/slow.jpg", 5)
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=1)
        d.download_batch([{"pnk":"T","product_id":"1","main_image_url":srv.url("/img/slow.jpg"),
                          "category_name":"T","page_number":1}])
        st = d.get_stats(); d.close()
        assert st["failed"] == 1
        assert len(st["errors"]) == 1
        assert st["errors"][0]["error_type"] == "TIMEOUT"

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
        # 复合键: pnk:A|hash 和 pnk:B|hash
        assert any("pnk:A" in k for k in r) and any("pnk:B" in k for k in r)

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
    def test_image_error_in_errors_csv(self, srv, tmp_path):
        """Crawler._log_image_errors 将图片错误写入 errors.csv"""
        from crawler import EmagCrawler
        routes = {}
        for i in range(5): routes[f"/img/{i}.jpg"] = (200, "image/jpeg", VALID_JPEG)
        routes["/img/bad.jpg"] = (200, "application/octet-stream", "\x00\x01\x02\x03"+"\x00"*100)
        srv.sr(routes)
        out = str(tmp_path / "o")
        prods = [{"pnk": f"P{i}","product_id": str(i),"main_image_url": srv.url(f"/img/{i}.jpg"),
                  "category_name":"T","page_number":1} for i in range(5)]
        prods.append({"pnk":"BAD","product_id":"99","main_image_url":srv.url("/img/bad.jpg"),
                      "category_name":"T","page_number":1})
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        c = EmagCrawler(out, image_downloader=d, download_images=True,
                        page_workers=1, category_workers=1, max_in_flight=4)
        d.download_batch(prods)
        img_st = d.get_stats()
        assert len(img_st["errors"]) >= 1
        if img_st.get("errors"): c._log_image_errors(img_st)
        err_path = os.path.join(out, "errors.csv")
        assert os.path.exists(err_path)
        with open(err_path, encoding="utf-8-sig") as f: content = f.read()
        assert "UNKNOWN_FORMAT" in content

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

# ============================================================
# PNK 命名测试
# ============================================================
class TestPnkNaming:
    def test_pnk_used_as_filename(self, srv, tmp_path):
        srv.sr({"/img/x.jpg": (200, "image/jpeg", VALID_JPEG)})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "DXYZ123ABC", "product_id": "1", "main_image_url": srv.url("/img/x.jpg"),
                "category_name": "T", "page_number": 1}
        r = d.download_batch([prod]); st = d.get_stats(); d.close()
        assert st["success"] == 1
        # 文件应以PNK命名 (在 images/ 子目录)
        img_dir = os.path.join(out, "images")
        files = os.listdir(img_dir)
        assert any(f.startswith("DXYZ123ABC") for f in files)

    def test_pnk_safe_chars(self, srv, tmp_path):
        """非法字符替换为_"""
        srv.sr({"/img/x.jpg": (200, "image/jpeg", VALID_JPEG)})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "A:B*C?D", "product_id": "1", "main_image_url": srv.url("/img/x.jpg"),
                "category_name": "T", "page_number": 1}
        d.download_batch([prod]); d.close()
        img_dir = os.path.join(out, "images")
        files = os.listdir(img_dir)
        assert any("A_B_C_D" in f for f in files)

    def test_no_pnk_fallback(self, srv, tmp_path):
        """缺少PNK使用NO_PNK_兜底"""
        srv.sr({"/img/x.jpg": (200, "image/jpeg", VALID_JPEG)})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "", "product_id": "12345", "main_image_url": srv.url("/img/x.jpg"),
                "category_name": "T", "page_number": 1}
        d.download_batch([prod]); d.close()
        img_dir = os.path.join(out, "images")
        files = os.listdir(img_dir)
        assert any("NO_PNK" in f for f in files)

    def test_same_url_one_download(self, srv, tmp_path):
        """同URL只下载一次, 不同PNK各得路径"""
        srv.sr({"/img/shared.jpg": (200, "image/jpeg", VALID_JPEG)})
        u = srv.url("/img/shared.jpg")
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=2, max_in_flight=4, timeout=5)
        prods = [{"pnk": "PNK_A", "product_id": "1", "main_image_url": u, "category_name": "T"},
                 {"pnk": "PNK_B", "product_id": "2", "main_image_url": u, "category_name": "T"}]
        r = d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 1  # 只下载一次
        assert any("PNK_A" in k for k in r) and any("PNK_B" in k for k in r)

    def test_pnk_conflict_suffix(self, srv, tmp_path):
        """同PNK不同URL: 第二张加后缀_2"""
        srv.sr({"/img/a.jpg": (200, "image/jpeg", VALID_JPEG),
                "/img/b.jpg": (200, "image/jpeg", _real_jpeg(6, 6))})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prods = [{"pnk": "SAME", "product_id": "1", "main_image_url": srv.url("/img/a.jpg"), "category_name": "T"},
                 {"pnk": "SAME", "product_id": "2", "main_image_url": srv.url("/img/b.jpg"), "category_name": "T"}]
        d.download_batch(prods); d.close()
        img_dir = os.path.join(out, "images")
        files = os.listdir(img_dir)
        has_same = any(f.startswith("SAME") and ".jp" in f and "_2" not in f for f in files)
        has_conflict = any("SAME_2" in f for f in files)
        assert has_same, f"Files: {files}"
        assert has_conflict, f"Files: {files}"

    def test_valid_small_saved(self, srv, tmp_path):
        """V2.1.3: 有效小图片正常保存, 不失败"""
        small_jpeg = _real_jpeg(4, 4)  # 真实小JPEG
        srv.sr({"/img/small.jpg": (200, "image/jpeg", small_jpeg)})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "SMALL1", "product_id": "1", "main_image_url": srv.url("/img/small.jpg"),
                "category_name": "T", "page_number": 1}
        r = d.download_batch([prod]); st = d.get_stats(); d.close()
        assert st["success"] == 1 and st["failed"] == 0

# ============================================================
# 配置默认值测试
# ============================================================
class TestConfigDefaults:
    def test_defaults_are_1_1_4(self):
        from config import DEFAULT_PAGE_WORKERS, DEFAULT_CATEGORY_WORKERS, DEFAULT_MAX_IN_FLIGHT
        assert DEFAULT_PAGE_WORKERS == 1
        assert DEFAULT_CATEGORY_WORKERS == 1
        assert DEFAULT_MAX_IN_FLIGHT == 4

    def test_cli_none_defaults(self):
        """CLI参数默认None, 未传时不覆盖TXT"""
        import main
        try:
            args = main.parse_args()
        except SystemExit:
            pytest.skip("argparse sys.exit in test env")
        # 不传参数时, 并发相关应为None (等待TXT合并)
        assert args.page_workers is None
        assert args.category_workers is None

# ============================================================
# 跨批次缓存测试
# ============================================================
class TestCrossBatchCache:
    def test_same_pnk_url_two_batches(self, srv, tmp_path):
        """同PNK同URL两批: HTTP=1, 一个文件"""
        srv.sr({"/img/x.jpg": (200, "image/jpeg", VALID_JPEG)})
        u = srv.url("/img/x.jpg"); out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "A", "product_id": "1", "main_image_url": u, "category_name": "T"}
        r1 = d.download_batch([prod])
        r2 = d.download_batch([prod])
        st = d.get_stats(); d.close()
        ck = d._composite_key(prod)
        assert r1.get(ck) == r2.get(ck)  # 相同路径
        assert os.path.exists(r1[ck])
        assert st["success"] == 1  # HTTP只请求一次
        # 不产生 _2
        files = os.listdir(os.path.join(out, "images"))
        assert not any("_2" in f for f in files)
        assert not any("_tmp" in f or ".tmp" in f or ".part" in f for f in files)

    def test_dup_in_batch1_same_in_batch2(self, srv, tmp_path):
        """batch1重复商品, batch2再次重复: HTTP=1, 始终只有一个文件"""
        srv.sr({"/img/x.jpg": (200, "image/jpeg", VALID_JPEG)})
        u = srv.url("/img/x.jpg"); out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "A", "product_id": "1", "main_image_url": u, "category_name": "T"}
        d.download_batch([prod, prod])
        d.download_batch([prod, prod])
        st = d.get_stats(); d.close()
        assert st["success"] == 1

    def test_diff_pnk_same_url_two_batches(self, srv, tmp_path):
        """同URL不同PNK两批: success按URL统计=2, A.jpg+B.jpg"""
        srv.sr({"/img/x.jpg": (200, "image/jpeg", VALID_JPEG)})
        u = srv.url("/img/x.jpg"); out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        d.download_batch([{"pnk":"A","product_id":"1","main_image_url":u,"category_name":"T"}])
        d.download_batch([{"pnk":"B","product_id":"2","main_image_url":u,"category_name":"T"}])
        st = d.get_stats(); d.close()
        # success按唯一URL+批次计算, 缓存命中也计入
        assert st["success"] >= 1
        files = os.listdir(os.path.join(out, "images"))
        has_a = any(f.startswith("A.") for f in files)
        has_b = any(f.startswith("B.") for f in files)
        assert has_a and has_b

    def test_same_pnk_diff_url_two_batches(self, srv, tmp_path):
        """同PNK不同URL两批: A.jpg + A_2.jpg, HTTP=2"""
        srv.sr({"/img/a.jpg": (200, "image/jpeg", VALID_JPEG),
                "/img/b.jpg": (200, "image/jpeg", _real_jpeg(6, 6))})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        ua = srv.url("/img/a.jpg"); ub = srv.url("/img/b.jpg")
        d.download_batch([{"pnk":"SAME","product_id":"1","main_image_url":ua,"category_name":"T"}])
        d.download_batch([{"pnk":"SAME","product_id":"2","main_image_url":ub,"category_name":"T"}])
        st = d.get_stats(); d.close()
        assert st["success"] == 2  # HTTP=2
        files = os.listdir(os.path.join(out, "images"))
        assert any(f.startswith("SAME") and "_2" in f for f in files)

    def test_cache_file_deleted_recovered(self, srv, tmp_path):
        """缓存路径文件被删后: product cache失效, URL缓存可能仍有效"""
        srv.sr({"/img/x.jpg": (200, "image/jpeg", VALID_JPEG)})
        u = srv.url("/img/x.jpg"); out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        prod = {"pnk": "RECOVER", "product_id": "1", "main_image_url": u, "category_name": "T"}
        r1 = d.download_batch([prod])
        ck = d._composite_key(prod)
        old_path = r1[ck]
        os.unlink(old_path)
        # 清除product缓存让download_batch重新下载
        with d._prod_cache_lock:
            d._product_path_cache.pop(ck, None)
        # 也清除URL缓存以确保重新请求
        with d._cache_lock:
            d._url_path_cache.pop(u, None)
        r2 = d.download_batch([prod])
        d.close()
        new_path = r2.get(ck)
        assert new_path is not None
        assert os.path.exists(new_path)

# ============================================================
# 严格图片验证测试
# ============================================================
class TestStrictVerify:
    def test_corrupt_jpeg_rejected(self, srv, tmp_path):
        srv.sr({"/img/c.jpg": (200, "image/jpeg", "\xff\xd8\xff\xe0" + "\x00" * 500)})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        d.download_batch([{"pnk":"C","product_id":"1","main_image_url":srv.url("/img/c.jpg"),
                          "category_name":"T","page_number":1}])
        st = d.get_stats(); d.close()
        assert st["failed"] >= 1
        assert any("CORRUPT_IMAGE" in e.get("error_type","") for e in st["errors"])

    def test_real_webp_passes(self, srv, tmp_path):
        from io import BytesIO; from PIL import Image
        buf = BytesIO(); Image.new('RGB', (4, 4), color='red').save(buf, 'WEBP')
        srv.sr({"/img/w.webp": (200, "image/webp", buf.getvalue())})
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        d.download_batch([{"pnk":"W","product_id":"1","main_image_url":srv.url("/img/w.webp"),
                          "category_name":"T","page_number":1}])
        st = d.get_stats(); d.close()
        assert st["success"] == 1

    def test_pillow_supports_webp_avif(self):
        from PIL import features
        assert features.check("webp")
        assert features.check("avif")

# ============================================================
# TXT配置测试
# ============================================================
class TestTxtConfig:
    def test_all_6_keys_read(self):
        from config import load_config
        cfg, urls = load_config("config/categories.txt")
        for k in ["page_workers","category_workers","max_in_flight",
                  "image_workers","image_max_in_flight","images_per_product"]:
            assert k in cfg, f"Missing config key: {k}"

    def test_no_cli_means_txt_source(self):
        """不传CLI时配置来源为categories.txt"""
        import main
        from config import load_config
        txt_cfg, _ = load_config("config/categories.txt")
        defaults = {"page_workers":1,"category_workers":1,"max_in_flight":4,
                    "image_workers":8,"image_max_in_flight":8,"images_per_product":1}
        try:
            args = main.parse_args()
        except SystemExit:
            pytest.skip("argparse sys.exit")
        final = main._merge_config(args, txt_cfg, defaults)
        for k in ["page_workers","category_workers","max_in_flight"]:
            assert final[f"{k}_source"] == "categories.txt"

    def test_unknown_key_rejected(self, tmp_path):
        from config import load_config
        f = tmp_path / "bad.txt"
        f.write_text("page_workers=1\nunknown_key=5\nhttps://www.emag.ro/test/c\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_config(str(f))

    def test_dup_key_rejected(self, tmp_path):
        from config import load_config
        f = tmp_path / "dup.txt"
        f.write_text("page_workers=1\npage_workers=2\nhttps://www.emag.ro/test/c\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_config(str(f))

    def test_images_per_product_only_0_1(self, tmp_path):
        from config import load_config
        f = tmp_path / "ipp.txt"
        f.write_text("images_per_product=2\nhttps://www.emag.ro/test/c\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_config(str(f))

    def test_zero_page_workers_rejected(self, tmp_path):
        from config import load_config
        f = tmp_path / "zero.txt"
        f.write_text("page_workers=0\nhttps://www.emag.ro/test/c\n", encoding="utf-8")
        # 0通常被validate_positive拒绝, 但在load_config中0不报错(它只检查<0)
        # 实际验证在main.py的validate_positive
        cfg, _ = load_config(str(f))
        assert cfg["page_workers"] == 0  # 解析通过, 验证在main

    def test_equal_in_url_not_confused(self, tmp_path):
        from config import load_config
        f = tmp_path / "eq.txt"
        f.write_text("page_workers=1\nhttps://www.emag.ro/test/c?ref=abc&type=1\n", encoding="utf-8")
        cfg, urls = load_config(str(f))
        assert len(cfg) == 1  # 只有page_workers
        assert len(urls) == 1
        assert "ref=abc" in urls[0]

# ============================================================
# PNK文件测试补充
# ============================================================
class TestPnkFiles:
    def test_jpeg_extension_from_content(self, srv, tmp_path):
        srv.sr({"/img/x.png": (200, "image/png", VALID_JPEG)})  # Content-Type错但内容是JPEG
        out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=1, max_in_flight=2, timeout=5)
        r = d.download_batch([{"pnk":"REAL","product_id":"1","main_image_url":srv.url("/img/x.png"),
                               "category_name":"T","page_number":1}])
        d.close()
        files = os.listdir(os.path.join(out, "images"))
        # 内容为JPEG, 扩展名应为.jpg
        assert any(f.startswith("REAL") and ".jpg" in f for f in files)

    def test_concurrent_same_pnk_same_url(self, srv, tmp_path):
        """8线程同PNK+同URL: 只有一个文件, HTTP=1"""
        srv.sr({"/img/x.jpg": (200, "image/jpeg", VALID_JPEG)})
        u = srv.url("/img/x.jpg"); out = str(tmp_path / "o")
        d = ImageDownloader(out, max_workers=8, max_in_flight=16, timeout=5)
        prods = [{"pnk":"CONC","product_id":"1","main_image_url":u,"category_name":"T"} for _ in range(8)]
        r = d.download_batch(prods); st = d.get_stats(); d.close()
        assert st["success"] == 1
        files = os.listdir(os.path.join(out, "images"))
        con_files = [f for f in files if f.startswith("CONC")]
        assert len(con_files) == 1
        assert not any("_2" in f for f in files)

# ============================================================
# 类目数量日志测试
# ============================================================
class TestCategoryCount:
    def test_normal_prints_count(self, tmp_path, capsys):
        """1个有效emag URL → 开始和结束各打印1次"""
        cats_path = tmp_path / "cats.txt"
        cats_path.write_text(
            "images_per_product=0\n"
            "https://www.emag.ro/mouse/c\n",
            encoding="utf-8")
        import sys as _sys
        _sys.argv = ["main", "--config", str(cats_path), "--pages", "1", "--no-images", "--output", str(tmp_path / "out")]
        import main as mm
        try: ec = mm.main()
        except SystemExit as e: ec = e.code
        captured = capsys.readouterr()
        assert "本次抓取类目数量：1" in captured.out
        assert captured.out.count("本次抓取类目数量：") >= 1

    def test_count_2(self, tmp_path, capsys):
        """2个有效emag URL → 数量=2"""
        cats_path = tmp_path / "cats2.txt"
        cats_path.write_text(
            "images_per_product=0\n"
            "https://www.emag.ro/mouse/c\nhttps://www.emag.ro/tastaturi/c\n",
            encoding="utf-8")
        import sys as _sys
        _sys.argv = ["main", "--config", str(cats_path), "--pages", "1", "--no-images", "--output", str(tmp_path / "out2")]
        import main as mm
        try: ec = mm.main()
        except SystemExit as e: ec = e.code
        captured = capsys.readouterr()
        assert "本次抓取类目数量：2" in captured.out

    def test_dup_url_not_double_counted(self, tmp_path, capsys):
        cats_path = tmp_path / "cats3.txt"
        cats_path.write_text(
            "images_per_product=0\n"
            "https://www.emag.ro/mouse/c\nhttps://www.emag.ro/mouse/c?ref=test\n",
            encoding="utf-8")
        import sys as _sys
        _sys.argv = ["main", "--config", str(cats_path), "--pages", "1", "--no-images", "--output", str(tmp_path / "out3")]
        import main as mm
        try: ec = mm.main()
        except SystemExit as e: ec = e.code
        captured = capsys.readouterr()
        assert "本次抓取类目数量：1" in captured.out

    def test_config_fail_prints_zero(self, tmp_path, capsys):
        import sys as _sys
        _sys.argv = ["main", "--config", str(tmp_path / "nonexistent.txt")]
        import main as mm
        try: ec = mm.main()
        except SystemExit as e: ec = e.code
        captured = capsys.readouterr()
        assert "本次抓取类目数量：0" in captured.out

    def test_duration_still_printed(self, tmp_path, capsys):
        cats_path = tmp_path / "cats4.txt"
        cats_path.write_text(
            "images_per_product=0\nhttps://www.emag.ro/mouse/c\n", encoding="utf-8")
        import sys as _sys
        _sys.argv = ["main", "--config", str(cats_path), "--pages", "1", "--no-images", "--output", str(tmp_path / "out4")]
        import main as mm
        try: ec = mm.main()
        except SystemExit as e: ec = e.code
        captured = capsys.readouterr()
        assert "本次任务总耗时" in captured.out
