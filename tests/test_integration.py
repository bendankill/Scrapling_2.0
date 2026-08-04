"""
集成测试 V2.1.2: Session复用, 图片错误, 分页, 耗时输出, 唯一统计, WAF退出码
"""
import json, os, sys, threading, time, subprocess, signal, http.server, socketserver, urllib.parse, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crawler import EmagCrawler, PageResult, ALL_PAGES_LIMIT
from image_downloader import ImageDownloader, ImageDownloadError
from utils import (detect_waf_block, WafBlockError, get_product_key, write_atomic_json,
    EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR, EXIT_CAPTCHA, EXIT_INTERRUPT, RunStatus)

PC = """<div class="card-item card-standard js-product-data"
 data-product-id="{}" data-name="P{}" data-position="{}"
 data-url="https://www.emag.ro/test/pd/PNK{}/"><p class="product-new-price">{},99Lei</p></div>"""
def _mp(n, cnt, hn=True, sid=0):
    nl = f'<link rel="next" href="/test/p{n+1}/c">' if hn else ''
    cs = "".join(PC.format(sid+i,sid+i,i,sid+i,(sid+i)*10) for i in range(1,cnt+1))
    return f"<html><head>{nl}</head><body><h1>P{n}</h1>{cs}</body></html>"

P1, P2, P3 = _mp(1,5), _mp(2,3,sid=10), _mp(3,2,hn=False,sid=20)

class _TH(http.server.BaseHTTPRequestHandler):
    routes={}; rc=0; lk=threading.Lock(); dm={}
    @classmethod
    def ra(cls): cls.routes={}; cls.rc=0; cls.dm={}
    def do_GET(self):
        with _TH.lk: _TH.rc+=1
        p=urllib.parse.urlparse(self.path).path.rstrip("/"); d=_TH.dm.get(p,0)
        if d>0: time.sleep(d)
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
    def sd(s,p,d): _TH.dm[p]=d

@pytest.fixture
def srv():
    s=LS(); s.start(); time.sleep(0.03)
    try: yield s
    finally: s.stop()

def _mc(s,ps): return [{"name":f"C{p.split('/')[1]}","url":s.url(p),"enabled":True} for p in ps]

# ============================================================
# 基础回归
# ============================================================
class TestRegression:
    def test_1p(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); s=c.finalize()
        assert s["totals"]["total_records"]==5

    def test_2p(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1),"/test/p2/c":(200,"text/html",P2)})
        out=str(tmp_path/"o"); c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=2); s=c.finalize()
        assert s["totals"]["total_records"]==8

    def test_waf_403(self,srv,tmp_path):
        srv.sr({"/test/c":(403,"text/html","X")}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); s=c.finalize()
        assert s["status"]=="waf_blocked"; assert c.get_exit_code()==3

# ============================================================
# 状态和退出码
# ============================================================
class TestStatusAndExit:
    def test_network_error(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1),"/test/p2/c":(500,"text/html","E")})
        out=str(tmp_path/"o"); c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=2); s=c.finalize()
        assert s["status"]=="network_error"; assert c.get_exit_code()==2

    def test_waf_exit_3(self,srv,tmp_path):
        srv.sr({"/test/c":(403,"text/html","X")}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        assert c.get_exit_code()==3

    def test_normal_exit_0(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); s=c.finalize()
        assert s["status"]=="completed"; assert c.get_exit_code()==0

# ============================================================
# Session 复用
# ============================================================
class TestSessionReuse:
    def test_same_thread(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        cids=[]
        og=c._get_client
        def tr(): cl=og(); cids.append(id(cl)); return cl
        c._get_client=tr
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        assert len(cids)>=1 and len(set(cids))==1

    def test_close_all(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        with c._sessions_lock: assert len(c._all_sessions)==0

# ============================================================
# 图片错误
# ============================================================
class TestImageErrors:
    def _mk(self,url,srv): return {"pnk":"T1","product_id":"1","main_image_url":srv.url(url),"category_name":"T","page_number":1}
    def test_404(self,srv,tmp_path):
        srv.sr({"/img/e.jpg":(404,"text/html","NF")}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([self._mk("/img/e.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1 and len(st["errors"])>=1
    def test_timeout(self,srv,tmp_path):
        srv.sr({"/img/s.jpg":(200,"image/jpeg","\xff\xd8\xff\xe0"+"x"*2000)}); srv.sd("/img/s.jpg",5)
        out=str(tmp_path/"o"); d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=1)
        d.download_batch([self._mk("/img/s.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1
    def test_small_valid_saved(self,srv,tmp_path):
        """V2.1.3: 有效小图片应保存"""
        from io import BytesIO; from PIL import Image
        buf = BytesIO(); Image.new('RGB',(4,4),color='red').save(buf,'JPEG')
        srv.sr({"/img/t.jpg":(200,"image/jpeg",buf.getvalue())}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([self._mk("/img/t.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["success"]>=1
    def test_html(self,srv,tmp_path):
        body="<html><body>"+"x"*2000+"</body></html>"
        srv.sr({"/img/f.jpg":(200,"text/html",body)}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([self._mk("/img/f.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1
    def test_unknown_fmt(self,srv,tmp_path):
        srv.sr({"/img/u.bin":(200,"application/octet-stream","\x00\x01\x02\x03"+"\x00"*2000)}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([self._mk("/img/u.bin",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1
    def test_redirect(self,srv,tmp_path):
        from io import BytesIO; from PIL import Image
        buf = BytesIO(); Image.new('RGB',(8,8),color='green').save(buf,'JPEG')
        srv.sr({"/img/r.jpg":(200,"image/jpeg",buf.getvalue())}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        r=d.download_batch([self._mk("/img/r.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["success"]>=1
    def test_same_url_multi(self,srv,tmp_path):
        from io import BytesIO; from PIL import Image
        buf = BytesIO(); Image.new('RGB',(8,8),color='blue').save(buf,'JPEG')
        srv.sr({"/img/s.jpg":(200,"image/jpeg",buf.getvalue())}); out=str(tmp_path/"o")
        u=srv.url("/img/s.jpg"); d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        ps=[{"pnk":"A","product_id":"1","main_image_url":u,"category_name":"T","page_number":1},
            {"pnk":"B","product_id":"2","main_image_url":u,"category_name":"T","page_number":1}]
        r=d.download_batch(ps); st=d.get_stats(); d.close()
        assert st["success"]>=1 and any("pnk:A" in k or "pnk:B" in k for k in r)

# ============================================================
# extract_total_pages
# ============================================================
class TestExtractTotalPages:
    etp = staticmethod(__import__('parser').extract_total_pages)
    def test_1_din_20(self):
        assert self.etp('<html><body><div class="pagination"><span>1 din 20</span></div></body></html>')==20
    def test_numeric(self):
        assert self.etp('<html><body><div class="pagination"><a>1</a><a>2</a><a>3</a></div></body></html>')==3
    def test_single(self): assert self.etp("<html></html>") is None
    def test_price_not_confused(self):
        assert self.etp('<html><body><p class="product-new-price">45,99Lei</p></body></html>') is None

# ============================================================
# 页数限制
# ============================================================
class TestPageLimits:
    def test_all_pages_20(self,srv,tmp_path):
        routes={"/test/c":(200,"text/html",_mp(1,1))}
        for i in range(2,22): routes[f"/test/p{i}/c"]=(200,"text/html",_mp(i,1,hn=i<21,sid=i-1))
        srv.sr(routes); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=3,category_workers=1,max_in_flight=4,all_pages=True)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=None); s=c.finalize()
        assert s["totals"]["success_pages"]==20

    def test_pages_10_actual_3(self,srv,tmp_path):
        routes={"/test/c":(200,"text/html",_mp(1,2))}
        for i in range(2,4): routes[f"/test/p{i}/c"]=(200,"text/html",_mp(i,2,hn=i<3,sid=(i-1)*2))
        srv.sr(routes); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=10); s=c.finalize()
        assert s["totals"]["success_pages"]==3; assert s["totals"]["total_records"]==6

# ============================================================
# 唯一统计
# ============================================================
class TestUniqueCounting:
    def test_same_page_dup(self,srv,tmp_path):
        html="<html><body>"+PC.format("1","A",1,"PNKA",10)+PC.format("1","A",2,"PNKA",10)+"</body></html>"
        srv.sr({"/test/c":(200,"text/html",html)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); s=c.finalize()
        assert s["totals"]["total_records"]==2; assert s["totals"]["unique_products"]==1
        j=json.load(open(os.path.join(out,"products.json"),encoding="utf-8")); assert len(j)==2

    def test_cross_page_dup(self,srv,tmp_path):
        p=PC.format("1","X",1,"SAMEPNK",10)
        srv.sr({"/test/c":(200,"text/html",f"<html><head><link rel='next' href='/test/p2/c'></head><body>{p}</body></html>"),
                "/test/p2/c":(200,"text/html",f"<html><body>{p}</body></html>")})
        out=str(tmp_path/"o"); c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=2); s=c.finalize()
        assert s["totals"]["total_records"]==2; assert s["totals"]["unique_products"]==1

# ============================================================
# 耗时输出 + 无checkpoint文件 (直接调Crawler)
# ============================================================
class TestDurationAndNoResume:
    def test_duration_format(self,srv,tmp_path):
        """验证 _fmt_duration 输出格式"""
        from main import _fmt_duration
        s = _fmt_duration(135.327)
        assert "00:02:15.327" in s and "135.327" in s
        s2 = _fmt_duration(3.582)
        assert "00:00:03.582" in s2

    def test_normal_completion_no_checkpoint(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); s=c.finalize()
        assert not os.path.exists(os.path.join(out,"checkpoint.json"))
        assert not os.path.exists(os.path.join(out,"resume.bat"))

    def test_waf_no_checkpoint(self,srv,tmp_path):
        srv.sr({"/test/c":(403,"text/html","X")}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        assert not os.path.exists(os.path.join(out,"checkpoint.json"))

# ============================================================
# 退出码
# ============================================================
class TestExitCodes:
    def test_codes(self):
        assert RunStatus.COMPLETED.exit_code==0; assert RunStatus.NETWORK_ERROR.exit_code==2
        assert RunStatus.WAF_BLOCKED.exit_code==3; assert RunStatus.INTERRUPTED.exit_code==130
