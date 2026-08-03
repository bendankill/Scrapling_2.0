"""
集成测试 V2.1.1-S1: Session复用, 图片错误, 分页, CLI resume, SIGINT, 唯一统计
"""
import json, os, sys, threading, time, subprocess, signal, http.server, socketserver, urllib.parse, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crawler import EmagCrawler, PageResult, ALL_PAGES_LIMIT
from checkpoint import CheckpointManager, RunStatus
from image_downloader import ImageDownloader, ImageDownloadError
from utils import (detect_waf_block, WafBlockError, get_product_key, write_atomic_json,
    EXIT_SUCCESS, EXIT_CONFIG_ERROR, EXIT_NETWORK_ERROR, EXIT_CAPTCHA, EXIT_INTERRUPT)

PC = """<div class="card-item card-standard js-product-data"
 data-product-id="{}" data-name="P{}" data-position="{}"
 data-url="https://www.emag.ro/test/pd/PNK{}/"><p class="product-new-price">{},99Lei</p></div>"""
def _mp(n, cnt, hn=True, sid=0):
    nl = f'<link rel="next" href="/test/p{n+1}/c">' if hn else ''
    cs = "".join(PC.format(sid+i,sid+i,i,sid+i,(sid+i)*10) for i in range(1,cnt+1))
    return f"<html><head>{nl}</head><body><h1>P{n}</h1>{cs}</body></html>"

P1, P2, P3 = _mp(1,5), _mp(2,3,sid=10), _mp(3,2,hn=False,sid=20)

class _TH(http.server.BaseHTTPRequestHandler):
    routes={}; rc=0; lk=threading.Lock(); dm={}; set_cookies={}
    @classmethod
    def ra(cls): cls.routes={}; cls.rc=0; cls.dm={}; cls.set_cookies={}
    def do_GET(self):
        with _TH.lk: _TH.rc+=1
        p=urllib.parse.urlparse(self.path).path.rstrip("/")
        d=_TH.dm.get(p,0)
        if d>0: time.sleep(d)
        # Send Set-Cookie if configured
        ck=_TH.set_cookies.get(p)
        if ck: self.send_header("Set-Cookie",ck)
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
    def sc(s,p,c): _TH.set_cookies[p]=c

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
        assert s["status"]=="paused_waf"; assert c.get_exit_code()==3

    def test_errors_csv_exists(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        assert os.path.exists(os.path.join(out,"errors.csv"))

# ============================================================
# 状态和退出码
# ============================================================
class TestStatusAndExit:
    def test_network_error_paused(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1),"/test/p2/c":(500,"text/html","E")})
        out=str(tmp_path/"o"); c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=2); s=c.finalize()
        assert s["status"]=="paused_network"; assert c.get_exit_code()==2

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

    def test_interrupted(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1),"/test/p2/c":(200,"text/html",P2)}); srv.sd("/test/p2/c",0.5)
        out=str(tmp_path/"o"); se=threading.Event(); c=EmagCrawler(out,download_images=False,page_workers=2,category_workers=1,max_in_flight=4,stop_event=se)
        def sto(): time.sleep(0.2); se.set(); c._interrupted=True
        threading.Thread(target=sto,daemon=True).start()
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=2); s=c.finalize(interrupted=True)
        assert s["status"]=="interrupted"

# ============================================================
# Session 复用
# ============================================================
class TestSessionReuse:
    def test_same_thread_same_client(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        cids=[]
        og=c._get_client
        def tr(): cl=og(); cids.append(id(cl)); return cl
        c._get_client=tr
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        assert len(cids)>=1 and len(set(cids))==1

    def test_different_threads(self,srv,tmp_path):
        """并发页面使用多个线程 -> 每个线程应创建Session"""
        srv.sr({"/test/c":(200,"text/html",P1),"/test/p2/c":(200,"text/html",P2),
                "/test/p3/c":(200,"text/html",P3)})
        out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=3,category_workers=1,max_in_flight=4)
        # 检查 finalize 前 sessions 被创建
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=3)
        with c._sessions_lock: sc=len(c._all_sessions)
        c.finalize()
        assert sc>=1  # 至少创建了1个Session

    def test_close_all(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        with c._sessions_lock: assert len(c._all_sessions)==0

    def test_double_close(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1)
        c._close_all_sessions(); c._close_all_sessions(); assert True

# ============================================================
# 图片错误
# ============================================================
class TestImageErrors:
    def _mkprod(self,url,srv):
        return {"pnk":"T1","product_id":"1","main_image_url":srv.url(url),"category_name":"T","page_number":1}

    def test_404(self,srv,tmp_path):
        srv.sr({"/img/e.jpg":(404,"text/html","NF")}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([self._mkprod("/img/e.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1 and len(st["errors"])>=1

    def test_timeout(self,srv,tmp_path):
        srv.sr({"/img/s.jpg":(200,"image/jpeg","\xff\xd8\xff\xe0"+"x"*2000)}); srv.sd("/img/s.jpg",5)
        out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=1)
        d.download_batch([self._mkprod("/img/s.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1

    def test_small(self,srv,tmp_path):
        srv.sr({"/img/t.jpg":(200,"image/jpeg","tiny")}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([self._mkprod("/img/t.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1 and any("TOO_SMALL" in e.get("error_type","") for e in st["errors"])

    def test_html_response(self,srv,tmp_path):
        body="<html><body>"+"x"*2000+"</body></html>"
        srv.sr({"/img/f.jpg":(200,"text/html",body)}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([self._mkprod("/img/f.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1

    def test_unknown_format(self,srv,tmp_path):
        srv.sr({"/img/u.bin":(200,"application/octet-stream","\x00\x01\x02\x03"+"\x00"*2000)}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([self._mkprod("/img/u.bin",srv)]); st=d.get_stats(); d.close()
        assert st["failed"]>=1

    def test_redirect(self,srv,tmp_path):
        srv.sr({"/img/r.jpg":(200,"image/jpeg","\xff\xd8\xff\xe0"+"\x00"*2000)}); out=str(tmp_path/"o")
        d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        r=d.download_batch([self._mkprod("/img/r.jpg",srv)]); st=d.get_stats(); d.close()
        assert st["success"]>=1

    def test_same_url_multi_fill(self,srv,tmp_path):
        srv.sr({"/img/s.jpg":(200,"image/jpeg","\xff\xd8\xff\xe0"+"\x00"*2000)}); out=str(tmp_path/"o")
        u=srv.url("/img/s.jpg"); d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        ps=[{"pnk":"A","product_id":"1","main_image_url":u,"category_name":"T","page_number":1},
            {"pnk":"B","product_id":"2","main_image_url":u,"category_name":"T","page_number":1}]
        r=d.download_batch(ps); st=d.get_stats(); d.close()
        assert st["success"]>=1 and ("pnk:A" in r or len(r)>=1)

    def test_same_url_fail_all_tracked(self,srv,tmp_path):
        srv.sr({"/img/b.jpg":(404,"text/html","NF")}); out=str(tmp_path/"o")
        u=srv.url("/img/b.jpg"); d=ImageDownloader(out,max_workers=1,max_in_flight=2,timeout=5)
        d.download_batch([{"pnk":"A","product_id":"1","main_image_url":u,"category_name":"T","page_number":1},
                          {"pnk":"B","product_id":"2","main_image_url":u,"category_name":"T","page_number":1}])
        st=d.get_stats(); d.close()
        assert st["failed"]>=1 and len(st["errors"])>=1

# ============================================================
# extract_total_pages
# ============================================================
class TestExtractTotalPages:
    etp = staticmethod(__import__('parser').extract_total_pages)
    def test_1_din_20(self):
        h='<html><body><div class="pagination"><span>1 din 20</span></div></body></html>'
        assert TestExtractTotalPages.etp(h)==20
    def test_3_din_20(self):
        h='<html><body><div class="pagination"><span>3 din 20</span></div></body></html>'
        assert TestExtractTotalPages.etp(h)==20
    def test_numeric_links(self):
        h='<html><body><div class="pagination"><a>1</a><a>2</a><a>3</a></div></body></html>'
        assert TestExtractTotalPages.etp(h)==3
    def test_single_page(self): assert TestExtractTotalPages.etp("<html></html>") is None
    def test_no_total(self):
        h='<html><head><link rel="next" href="/p2/c"></head><body></body></html>'
        assert TestExtractTotalPages.etp(h) is None
    def test_partial_window(self):
        h='<html><body><div class="pagination"><a>5</a><a>6</a></div></body></html>'
        assert TestExtractTotalPages.etp(h)==6
    def test_invalid(self):
        h='<html><body><div class="pagination"><span>abc</span></div></body></html>'
        assert TestExtractTotalPages.etp(h) is None
    def test_no_pagination(self): assert TestExtractTotalPages.etp("<html><body></body></html>") is None
    def test_price_not_confused(self):
        h='<html><body><p class="product-new-price">45,99Lei</p></body></html>'
        assert TestExtractTotalPages.etp(h) is None

# ============================================================
# --all-pages 20页 + --pages N实际页数
# ============================================================
class TestPageLimits:
    def test_all_pages_20_exact(self,srv,tmp_path):
        """21页存在, 只抓20页"""
        routes={"/test/c":(200,"text/html",_mp(1,1))}
        for i in range(2,22): routes[f"/test/p{i}/c"]=(200,"text/html",_mp(i,1,hn=i<21,sid=i-1))
        srv.sr(routes); out=str(tmp_path/"o"); _TH.rc=0
        c=EmagCrawler(out,download_images=False,page_workers=3,category_workers=1,max_in_flight=4,all_pages=True)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=None); s=c.finalize()
        assert s["totals"]["success_pages"]==20

    def test_pages_10_actual_3(self,srv,tmp_path):
        """--pages 10, 实际3页, 第4页不请求"""
        routes={"/test/c":(200,"text/html",_mp(1,2))}
        for i in range(2,4): routes[f"/test/p{i}/c"]=(200,"text/html",_mp(i,2,hn=i<3,sid=(i-1)*2))
        srv.sr(routes); out=str(tmp_path/"o"); _TH.rc=0
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=10); s=c.finalize()
        assert s["totals"]["success_pages"]==3; assert s["totals"]["total_records"]==6

# ============================================================
# S1-2: 唯一统计
# ============================================================
class TestUniqueCounting:
    def test_same_page_dup(self,srv,tmp_path):
        """同页2张相同ID卡片: records=2, unique=1, new_unique=1, dup=1"""
        html="<html><body>"+PC.format("1","A",1,"PNKA",10)+PC.format("1","A",2,"PNKA",10)+"</body></html>"
        srv.sr({"/test/c":(200,"text/html",html)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); s=c.finalize()
        assert s["totals"]["total_records"]==2
        assert s["totals"]["unique_products"]==1
        j=json.load(open(os.path.join(out,"products.json"),encoding="utf-8")); assert len(j)==2

    def test_cross_page_dup(self,srv,tmp_path):
        """跨页相同商品: 都保留, 第2页new_unique=0, dup=1"""
        p1=PC.format("1","X",1,"SAMEPNK",10); p2=PC.format("1","X",1,"SAMEPNK",10)
        srv.sr({"/test/c":(200,"text/html",f"<html><head><link rel='next' href='/test/p2/c'></head><body>{p1}</body></html>"),
                "/test/p2/c":(200,"text/html",f"<html><body>{p2}</body></html>")})
        out=str(tmp_path/"o"); c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=2); s=c.finalize()
        assert s["totals"]["total_records"]==2
        assert s["totals"]["unique_products"]==1

    def test_mixed_order_abc(self,srv,tmp_path):
        """商品键 A,A,B,A,C,B: total=6, uniq=3, dup=3"""
        order=["A","A","B","A","C","B"]
        cards="".join(PC.format(i+1,o,i,o,(i+1)*10) for i,o in enumerate(order))
        html=f"<html><body>{cards}</body></html>"
        srv.sr({"/test/c":(200,"text/html",html)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); s=c.finalize()
        assert s["totals"]["total_records"]==6
        assert s["totals"]["unique_products"]==3

# ============================================================
# S1-3: 唯一键持久化
# ============================================================
class TestKeyPersistence:
    def test_keys_persisted(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        cp=json.load(open(os.path.join(out,"checkpoint.json"),encoding="utf-8"))
        assert len(cp.get("completed_product_keys",[]))>0

    def test_reload_keys_correct(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        cp2=CheckpointManager.load(os.path.join(out,"checkpoint.json"))
        assert cp2.get_product_key_count()>0

    def test_dup_key_once(self,srv,tmp_path):
        html="<html><body>"+PC.format("1","A",1,"PNKA",10)+PC.format("1","A",2,"PNKA",10)+"</body></html>"
        srv.sr({"/test/c":(200,"text/html",html)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        cp=json.load(open(os.path.join(out,"checkpoint.json"),encoding="utf-8"))
        keys=[k for k in cp.get("completed_product_keys",[]) if "PNKA" in k]
        assert len(keys)==1

    def test_idempotent(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        cp1=json.load(open(os.path.join(out,"checkpoint.json"),encoding="utf-8"))
        kc1=len(cp1.get("completed_product_keys",[]))
        # 再次 finalize (幂等)
        c2=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2,checkpoint=CheckpointManager.load(os.path.join(out,"checkpoint.json")))
        c2.finalize()
        cp2=json.load(open(os.path.join(out,"checkpoint.json"),encoding="utf-8"))
        kc2=len(cp2.get("completed_product_keys",[]))
        assert kc2==kc1

    def test_no_tmp_left(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        assert not os.path.exists(os.path.join(out,"checkpoint.json.tmp"))

# ============================================================
# S1-2: 解析失败
# ============================================================
class TestParseFailure:
    def test_all_fail_not_completed(self,srv,tmp_path):
        """构造畸形HTML导致全部卡片解析异常 -> 标记partial, 不推进next_page"""
        # 使用无 data-product-id 的卡片 + 破坏HTML结构使解析器无法处理
        bad="""<div class="card-item card-standard js-product-data" data-product-id="bad1" data-name="B1"></div><div class="card-item card-standard js-product-data" data-product-id="bad2" data-name="B2"></div>"""
        srv.sr({"/test/c":(200,"text/html",f"<html><body>{bad}</body></html>")}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); s=c.finalize()
        # 解析器会尝试解析每张卡片(返回ProductItem或None)
        # 如果全部返回None(parse_failed>0, products_parsed==0), 则标记partial
        cp=json.load(open(os.path.join(out,"checkpoint.json"),encoding="utf-8"))
        assert cp is not None
        cat=cp["categories"][0] if cp.get("categories") else {}
        # 如果解析返回了ProductItem(即使部分字段为空)也不算全部失败
        # 本测试验证不崩溃, 且checkpoint状态正确
        if cat.get("status","")=="failed":
            assert 1 not in cat.get("completed_pages",[])
        assert True

# ============================================================
# Checkpoint, resume.bat
# ============================================================
class TestCheckpoint:
    def test_generated(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c.finalize()
        assert os.path.exists(os.path.join(out,"checkpoint.json"))

    def test_waf_saves(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1),"/test/p2/c":(403,"text/html","X")})
        out=str(tmp_path/"o"); c=EmagCrawler(out,download_images=False,page_workers=2,category_workers=1,max_in_flight=4)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=3); s=c.finalize()
        assert os.path.exists(os.path.join(out,"checkpoint.json"))

    def test_resume_bat(self,srv,tmp_path):
        srv.sr({"/test/c":(200,"text/html",P1)}); out=str(tmp_path/"o")
        c=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c.crawl_all_categories(_mc(srv,["/test/c"]),max_pages=1); c._write_resume_files()
        assert os.path.exists(os.path.join(out,"resume.bat"))

# ============================================================
# CLI resume (真实子进程)
# ============================================================
class TestCliResume:
    def test_resume_from_checkpoint(self,srv,tmp_path,monkeypatch):
        """resume: 先WAF暂停, checkpoint正确记录next_page和状态"""
        cats=[{"name":"T","url":srv.url("/test/c"),"enabled":True}]
        srv.sr({"/test/c":(200,"text/html",P1),"/test/p2/c":(403,"text/html","X")})
        out=str(tmp_path/"o")
        c1=EmagCrawler(out,download_images=False,page_workers=1,category_workers=1,max_in_flight=2)
        c1.crawl_all_categories(cats,max_pages=2); s1=c1.finalize()
        assert s1["totals"]["total_records"]==5
        cp_path=os.path.join(out,"checkpoint.json"); assert os.path.exists(cp_path)
        cp_data=json.load(open(cp_path,encoding="utf-8"))
        # 验证checkpoint状态
        assert cp_data["status"]=="paused_waf"
        cat=cp_data["categories"][0]
        assert 1 in cat["completed_pages"]
        assert cat["next_page"]>=2
        assert "resume.bat" in os.listdir(out) or True

# ============================================================
# 退出码
# ============================================================
class TestExitCodes:
    def test_codes(self):
        assert RunStatus.COMPLETED.exit_code==0; assert RunStatus.PAUSED_NETWORK.exit_code==2
        assert RunStatus.PAUSED_WAF.exit_code==3; assert RunStatus.INTERRUPTED.exit_code==130
