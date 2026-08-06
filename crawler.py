"""
爬虫核心 V2.1.2: 纯HTTP, 顺序提交, 全部卡片保留
"""
import hashlib, json, logging, os, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock, Semaphore, Event
from typing import Optional
from scrapling.fetchers import FetcherSession
from models import ProductItem
from parser import parse_product_listing, extract_next_page, extract_total_pages, page_has_products
from image_downloader import ImageDownloader
from exporters import Exporters
from utils import (detect_waf_block, WafBlockError, get_product_key,
    write_errors_csv, write_atomic_json, ensure_errors_csv, RunStatus)

logger = logging.getLogger("emag_crawler.crawler")
ALL_PAGES_LIMIT = 20


@dataclass
class PageResult:
    page_number: int = 0; page_url: str = ""; http_status: int = 0
    cards_found: int = 0; products_parsed: int = 0; parse_failed: int = 0
    duplicates: int = 0; new_unique_products: int = 0
    next_url: str = ""; has_next: bool = False; is_last_page: bool = False
    products: list = field(default_factory=list)
    all_products: list = field(default_factory=list)
    product_keys: list = field(default_factory=list)
    parse_errors: list = field(default_factory=list)
    html_hash: str = ""; waf_error = None; total_pages: Optional[int] = None
    analysis_failed: bool = False
    fatal_error_type: str = ""
    fatal_error_detail: str = ""


class CategoryStats:
    def __init__(self, name, url):
        self.name = name; self.url = url
        self.requested_pages = 0; self.success_pages = 0; self.failed_pages = 0
        self.total_records = 0; self.unique_products = 0
        self.image_success = 0; self.image_failed = 0
        self.start_time = time.time(); self.end_time = 0.0
        self.cards_found = 0; self.products_parsed = 0; self.parse_failed = 0
        self.duplicates = 0; self.new_unique = 0; self.stop_reason = ""
    @property
    def elapsed(self): return self.end_time - self.start_time if self.end_time else 0
    def to_dict(self):
        return {"name": self.name, "url": self.url,
            "requested_pages": self.requested_pages, "success_pages": self.success_pages,
            "failed_pages": self.failed_pages, "total_records": self.total_records,
            "unique_products": self.unique_products, "image_success": self.image_success,
            "image_failed": self.image_failed, "elapsed_seconds": round(self.elapsed, 2),
            "stop_reason": self.stop_reason, "cards_found": self.cards_found,
            "products_parsed": self.products_parsed, "parse_failed": self.parse_failed,
            "duplicates": self.duplicates, "new_unique": self.new_unique}


class EmagCrawler:
    def __init__(self, output_dir, image_downloader=None, page_workers=1,
                 category_workers=1, max_in_flight=4, download_images=True,
                 all_pages=False, stop_event=None):
        self.output_dir = output_dir
        self.download_images = download_images
        self.image_downloader = image_downloader
        self.page_workers = page_workers
        self.category_workers = category_workers
        self.all_pages = all_pages
        self._run_status = RunStatus.RUNNING
        self.global_semaphore = Semaphore(max_in_flight)
        self.exporters = Exporters(output_dir)
        self.stats: dict[str, CategoryStats] = {}
        self._stats_lock = Lock()
        self.errors_file = os.path.join(output_dir, "errors.csv")
        self._error_lock = Lock(); self._error_header_written = False
        self.start_time = time.time()
        self._stop_event = stop_event or Event()
        self._interrupted = False
        self._thread_local = threading.local()
        self._session_config = {"impersonate": "chrome136", "stealthy_headers": True,
                                "timeout": 30, "retries": 3, "retry_delay": 1}
        self._all_sessions: list = []; self._sessions_lock = Lock()
        self._cat_page_hashes: dict[str, set] = {}; self._hash_lock = Lock()
        # 运行内唯一商品键集合 (替代已删除的 checkpoint)
        self._product_keys: set = set()
        self._keys_lock = Lock()
        os.makedirs(output_dir, exist_ok=True)

    # ---- Session ----
    def _get_client(self):
        if not hasattr(self._thread_local, 'client'):
            mgr = FetcherSession(**self._session_config)
            client = mgr.__enter__()
            self._thread_local.mgr = mgr; self._thread_local.client = client
            with self._sessions_lock: self._all_sessions.append((mgr, client))
        return self._thread_local.client

    def _close_all_sessions(self):
        with self._sessions_lock:
            for mgr, client in list(self._all_sessions):
                try: mgr.__exit__(None, None, None)
                except Exception: pass
            self._all_sessions.clear()

    # ---- 原子产品键 (运行内) ----
    def _check_and_add_product_keys(self, keys: list) -> tuple:
        """原子操作: 检查并添加产品键。返回 (new_keys, dup_count)"""
        new, dup = [], 0
        with self._keys_lock:
            for k in keys:
                if k in self._product_keys: dup += 1
                else: self._product_keys.add(k); new.append(k)
        return new, dup

    # ---- 页面获取 ----
    def _fetch_page(self, url):
        if self._stop_event.is_set(): return None, 0
        with self.global_semaphore:
            try:
                page = self._get_client().get(url)
                return page.html_content, page.status
            except Exception as e:
                logger.error(f"HTTP [{url}]: {e}")
                return None, 0

    # ---- 页面去重 ----
    def _cat_key(self, base_url): return base_url.lower().rstrip("/")
    def _check_and_add_hash(self, cat_url, html):
        h = hashlib.md5(html.encode()).hexdigest(); ck = self._cat_key(cat_url)
        with self._hash_lock:
            if ck not in self._cat_page_hashes: self._cat_page_hashes[ck] = set()
            if h in self._cat_page_hashes[ck]: return True
            self._cat_page_hashes[ck].add(h); return False

    # ---- 单页解析 (V2.1.4-final: 一次DOM解析) ----
    def _fetch_and_parse_page(self, name, base_url, page_num, page_url) -> PageResult:
        pr = PageResult(page_number=page_num, page_url=page_url)
        html, st = self._fetch_page(page_url)
        pr.http_status = st

        # 403/429/511: 不解析DOM, 直接WAF
        if st in (403, 429, 511):
            waf = detect_waf_block(html or "", st, page_url, category=name, page_num=page_num, soup=None)
            pr.waf_error = waf; return pr
        if st != 200 or not html: return pr

        # HTTP 200: 一次性解析HTML
        soup = self._parse_html_once(html)
        if soup is None:
            pr.analysis_failed = True
            pr.fatal_error_type = "PAGE_ANALYSIS_ERROR"
            pr.fatal_error_detail = "DOM parse failed"
            self._log_error(name, page_num, page_url, "PAGE_ANALYSIS_ERROR", detail="DOM parse failed")
            # DOM解析失败: 用原始HTML简单检查强WAF标题
            if "emag captcha" in html.lower():
                pr.waf_error = WafBlockError(200, name, page_num, page_url,
                    "STRONG_WAF_EVIDENCE", "eMAG Captcha title (text)")
            return pr

        # 强WAF证据检查(使用soup, 仅检查标题)
        waf3 = detect_waf_block(html, st, page_url, category=name, page_num=page_num, soup=soup)
        if waf3: pr.waf_error = waf3; return pr

        # 元数据(复用soup)
        pr.next_url = _extract_next_page_soup(soup, page_url) or ""
        pr.has_next = bool(pr.next_url)
        pr.total_pages = _extract_total_pages_soup(soup)
        pr.html_hash = hashlib.md5(html.encode()).hexdigest()

        # 商品解析(复用soup)
        from utils import _page_has_valid_product_soup
        has_products = _page_has_valid_product_soup(soup)
        if not has_products:
            # 无有效商品: 空类目或最后一页
            pr.is_last_page = True; return pr
        products, parse_errors = self._parse_products_soup(soup, name, base_url, page_url, page_num)
        pr.cards_found = len(products) + len(parse_errors)
        pr.products_parsed = len(products); pr.parse_failed = len(parse_errors)
        pr.parse_errors = parse_errors; pr.all_products = list(products)
        return pr

    @staticmethod
    def _parse_html_once(html: str):
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html, "lxml")
        except Exception:
            return None

    def _parse_products_soup(self, soup, name, base_url, page_url, page_num):
        cards = soup.select(".card-item.card-standard.js-product-data")
        if not cards:
            cards = soup.select("[data-product-id]")
            cards = [c for c in cards if c.get("data-product-id")]
        products, errors = [], []
        for idx, card in enumerate(cards):
            try:
                from parser import _parse_product_card
                p = _parse_product_card(card, name, base_url, page_url, page_num)
                if p: products.append(p)
                else:
                    pos = card.get("data-position", str(idx+1)); pid = card.get("data-product-id","")
                    errors.append({"position": pos, "product_id": pid, "error_type": "PARSE_FAILED", "error_detail": "解析返回None"})
            except Exception as e:
                pos = card.get("data-position", str(idx+1)); pid = card.get("data-product-id","")
                errors.append({"position": pos, "product_id": pid, "error_type": type(e).__name__, "error_detail": str(e)[:200]})
        return products, errors

    # ---- 类目抓取 ----
    def crawl_category(self, name, url, max_pages=None):
        stats = CategoryStats(name, url)
        with self._stats_lock: self.stats[name] = stats
        hard_limit = max_pages if max_pages is not None else (ALL_PAGES_LIMIT if self.all_pages else 1)
        logger.info(f"[{name}] 开始: {url} (限制: {hard_limit} 页)")

        # --- 首页 ---
        pr = self._fetch_and_parse_page(name, url, 1, url); stats.requested_pages += 1
        if pr.waf_error:
            self._handle_stop(RunStatus.WAF_BLOCKED, name, 1, pr.page_url, pr.waf_error); return stats
        if pr.analysis_failed:
            stats.failed_pages += 1; stats.stop_reason = "page_analysis_error"
            self._run_status = RunStatus.NETWORK_ERROR; return stats
        if pr.http_status != 200:
            stats.failed_pages += 1
            self._handle_stop(RunStatus.NETWORK_ERROR, name, 1, url, detail=f"HTTP {pr.http_status}"); return stats
        if pr.cards_found == 0 and pr.is_last_page:
            stats.stop_reason = "empty_category"; return stats
        if pr.cards_found > 0 and pr.products_parsed == 0 and pr.parse_failed > 0:
            self._commit_page_errors(name, url, pr)
            stats.stop_reason = "parse_error"
            self._run_status = RunStatus.NETWORK_ERROR; return stats
        self._commit_page(name, url, pr, stats)
        effective = min(hard_limit, pr.total_pages) if pr.total_pages else hard_limit
        if hard_limit <= 1 or not pr.has_next:
            stats.stop_reason = "no_next_page" if not pr.has_next else "requested_limit_reached"; return stats

        # --- 并发后续页 ---
        import re as _re
        url_template = None
        if pr.has_next:
            m = _re.match(r'(.*?/p)(\d+)(/c.*)', pr.next_url)
            if m: url_template = (m.group(1), m.group(3))
        start_page = 2; pending = list(range(start_page, effective + 1))
        completed_buf: dict[int, PageResult] = {}; in_flight: dict[Future, int] = {}
        next_idx, stopped = 0, False; lock = Lock()

        with ThreadPoolExecutor(max_workers=self.page_workers) as ex:
            while next_idx < len(pending) and len(in_flight) < self.page_workers:
                if self._stop_event.is_set(): stopped = True; break
                pn = pending[next_idx]
                pu = f"{url_template[0]}{pn}{url_template[1]}" if url_template else None
                if not pu: stopped = True; break
                fut = ex.submit(self._fetch_and_parse_page, name, url, pn, pu)
                in_flight[fut] = pn; stats.requested_pages += 1; next_idx += 1

            next_expected = start_page
            while in_flight and not stopped:
                done = [f for f in in_flight if f.done()]
                if not done: time.sleep(0.01); continue
                for fut in done:
                    pn = in_flight.pop(fut)
                    try: completed_buf[pn] = fut.result()
                    except Exception as e: completed_buf[pn] = PageResult(page_number=pn, http_status=0)

                with lock:
                    while next_expected in completed_buf and not stopped:
                        pr = completed_buf.pop(next_expected)
                        if pr.waf_error:
                            stats.stop_reason = "waf_blocked"
                            self._handle_stop(RunStatus.WAF_BLOCKED, name, next_expected, pr.page_url, pr.waf_error)
                            self._stop_event.set(); stopped = True; break
                        if pr.analysis_failed:
                            stats.failed_pages += 1; stats.stop_reason = "page_analysis_error"
                            self._run_status = RunStatus.NETWORK_ERROR; stopped = True; break
                        if pr.http_status != 200:
                            stats.failed_pages += 1; stats.stop_reason = "network_error"
                            self._handle_stop(RunStatus.NETWORK_ERROR, name, next_expected, pr.page_url, detail=f"HTTP {pr.http_status}")
                            stopped = True; break
                        if pr.cards_found == 0: stopped = True; break
                        if pr.cards_found > 0 and pr.products_parsed == 0 and pr.parse_failed > 0:
                            self._commit_page_errors(name, url, pr)
                            self._run_status = RunStatus.NETWORK_ERROR; stopped = True; break
                        self._commit_page(name, url, pr, stats)
                        if not pr.has_next or pr.is_last_page:
                            stats.stop_reason = "actual_last_page_reached"; stopped = True; break
                        next_expected += 1

                while (not stopped and not self._stop_event.is_set()
                       and next_idx < len(pending) and len(in_flight) < self.page_workers):
                    pn = pending[next_idx]
                    pu = f"{url_template[0]}{pn}{url_template[1]}" if url_template else None
                    if not pu: stopped = True; break
                    fut = ex.submit(self._fetch_and_parse_page, name, url, pn, pu)
                    in_flight[fut] = pn; stats.requested_pages += 1; next_idx += 1

            for fut in list(in_flight.keys()): fut.cancel()

        if not stats.stop_reason:
            stats.stop_reason = "requested_limit_reached"
        stats.end_time = time.time()
        return stats

    def _commit_page(self, name, base_url, pr: PageResult, stats: CategoryStats):
        self._check_and_add_hash(base_url, pr.html_hash) if pr.html_hash else None
        for err in pr.parse_errors:
            self._log_error(name, pr.page_number, pr.page_url, err.get("error_type","PARSE_FAILED"),
                          detail=err.get("error_detail",""), product_key=err.get("product_id",""))

        all_prods = pr.all_products
        new_products, dup_count, added_keys = [], 0, []
        if all_prods:
            all_keys = [get_product_key(p.to_dict()) for p in all_prods]
            added_keys, dup_count = self._check_and_add_product_keys(all_keys)
            added_set = set(added_keys); seen_new = set()
            for p in all_prods:
                k = get_product_key(p.to_dict())
                if k in added_set and k not in seen_new: new_products.append(p); seen_new.add(k)
        pr.new_unique_products = len(added_keys); pr.duplicates = dup_count
        pr.products = new_products; pr.product_keys = added_keys
        for p in all_prods: self.exporters.add_product(p)

        stats.success_pages += 1; stats.total_records += len(all_prods)
        stats.cards_found += pr.cards_found; stats.products_parsed += pr.products_parsed
        stats.parse_failed += pr.parse_failed; stats.duplicates += dup_count
        stats.new_unique += len(added_keys)
        logger.info(f"[{name}] P{pr.page_number}: cards={pr.cards_found} "
                    f"parsed={pr.products_parsed} fail={pr.parse_failed} "
                    f"dup={dup_count} new={len(added_keys)}")

    def _commit_page_errors(self, name, base_url, pr: PageResult):
        for err in pr.parse_errors:
            self._log_error(name, pr.page_number, pr.page_url, err.get("error_type","PARSE_FAILED"),
                          detail=err.get("error_detail",""), product_key=err.get("product_id",""))

    def _make_page_url(self, t, n): return f"{t[0]}{n}{t[1]}" if t else None

    # ---- 类目调度 ----
    def crawl_all_categories(self, categories, max_pages=None):
        if not categories: return {}
        self._target_cat_count = len(categories)
        self._completed_cat_urls: set = set()
        self._cat_urls_lock = Lock()
        for cat in categories:
            if self._stop_event.is_set(): break
            if self._run_status.is_stopped: break
            self.crawl_category(cat["name"], cat["url"], max_pages)
            # 判断该类目是否正常完成
            stats = self.stats.get(cat["name"])
            if stats and stats.stop_reason in (
                "requested_limit_reached", "actual_last_page_reached",
                "no_next_page", "empty_category"
            ):
                with self._cat_urls_lock:
                    self._completed_cat_urls.add(cat["url"])
            if self._run_status.is_stopped: break
        return {}

    # ---- 停止处理 (V2.1.2: 无断点恢复提示) ----
    def _handle_stop(self, status: RunStatus, name, page_num, url, waf=None, detail=""):
        self._stop_event.set(); self._run_status = status
        ts = datetime.now(timezone.utc).isoformat()
        if waf:
            self._log_error(name, page_num, url, f"WAF_{waf.block_type}", waf.status_code, detail=waf.evidence)
            diag_dir = os.path.join(self.output_dir, "diagnostics")
            os.makedirs(diag_dir, exist_ok=True)
            write_atomic_json(os.path.join(diag_dir, "captcha_diagnostic.json"),
                {"status": "waf_blocked", "timestamp": ts, "category": name,
                 "page": page_num, "url": url, "http_status": waf.status_code,
                 "block_type": waf.block_type, "evidence": waf.evidence})
        else:
            self._log_error(name, page_num, url, "NETWORK_ERROR", detail=detail)

        s = __import__('sys')
        if status == RunStatus.WAF_BLOCKED:
            print(f"\n{'!'*60}\n  检测到WAF/验证码，任务已终止。\n"
                  f"  当前版本不支持断点续抓，请重新执行原抓取命令。\n{'!'*60}", file=s.stderr)
        else:
            print(f"\n{'!'*60}\n  网络错误，任务已终止。\n"
                  f"  当前版本不支持断点续抓，请重新执行原抓取命令。\n{'!'*60}", file=s.stderr)
        if waf: print(f"  HTTP状态码: {waf.status_code}", file=s.stderr)
        else: print(f"  详情: {detail}", file=s.stderr)
        print(f"  类目: {name}  页码: {page_num}", file=s.stderr)

    # ---- 错误 ----
    ERROR_FIELDNAMES = ["时间","类目","页码","商品键","URL","错误类型","HTTP状态码","重试次数","错误详情"]
    def _log_error(self, name, page, url, error_type, http_status=0, retries=0, detail="", product_key=""):
        d = {"时间": datetime.now(timezone.utc).isoformat(), "类目": name, "页码": page,
             "商品键": product_key, "URL": url, "错误类型": error_type,
             "HTTP状态码": http_status, "重试次数": retries, "错误详情": detail}
        with self._error_lock:
            write_errors_csv(self.errors_file, d, write_header=not self._error_header_written,
                           fieldnames=self.ERROR_FIELDNAMES)
            if not self._error_header_written: self._error_header_written = True

    def _log_image_errors(self, img_stats):
        for err in img_stats.get("errors", []):
            self._log_error(name=err.get("category",""), page=err.get("page",0),
                url=err.get("image_url", err.get("url","")),
                error_type=err.get("error_type","IMAGE_ERROR"), http_status=err.get("http_status",0),
                detail=err.get("error_detail",str(err)), product_key=err.get("product_key",""))

    def download_images_for_products(self):
        if not self.image_downloader: return {}
        return self.image_downloader.download_batch(self.exporters.get_products_sorted())

    # ---- finalize ----
    def finalize(self, interrupted=False):
        total_elapsed = time.time() - self.start_time
        self._close_all_sessions()

        image_stats = {"success": 0, "failed": 0, "errors": []}
        path_map = {}
        if self.download_images and self.image_downloader and not self._stop_event.is_set():
            path_map = self.download_images_for_products()
            image_stats = self.image_downloader.get_stats()
        if image_stats.get("errors"): self._log_image_errors(image_stats)

        if path_map:
            with self.exporters._lock:
                for item in self.exporters._products:
                    # V2.1.3: 复合键 {product_key}|{url_hash} 区分同PNK不同URL
                    ck = f"{get_product_key(item)}|{hashlib.md5((item.get('main_image_url') or '').encode()).hexdigest()[:8]}"
                    if ck in path_map: item["main_image_local_path"] = path_map[ck]
                    elif get_product_key(item) in path_map: item["main_image_local_path"] = path_map[get_product_key(item)]

        sorted_prods = self.exporters.get_products_sorted()
        total_records = len(sorted_prods)
        unique_keys = {get_product_key(i) for i in sorted_prods}

        with self._stats_lock:
            for name, stats in self.stats.items():
                img_ok = 0; img_fail = 0; cat_keys = set(); cat_count = 0
                for item in sorted_prods:
                    if item.get("category_name") == name:
                        cat_count += 1; cat_keys.add(get_product_key(item))
                        if item.get("main_image_local_path"): img_ok += 1
                        elif item.get("main_image_url"): img_fail += 1
                stats.total_records = cat_count; stats.unique_products = len(cat_keys)
                stats.image_success = img_ok; stats.image_failed = img_fail

        ensure_errors_csv(self.errors_file, self.ERROR_FIELDNAMES)
        self.exporters.finalize()

        if interrupted:
            self._run_status = RunStatus.INTERRUPTED
        elif not self._run_status.is_stopped:
            self._run_status = RunStatus.COMPLETED

        status = self._run_status
        target_cats = getattr(self, '_target_cat_count', 0)
        completed_cats = len(getattr(self, '_completed_cat_urls', set()))
        # 每个类目标记是否完成
        cat_dicts = []
        for s in self.stats.values():
            d = s.to_dict()
            d["completed"] = s.stop_reason in (
                "requested_limit_reached", "actual_last_page_reached",
                "no_next_page", "empty_category")
            cat_dicts.append(d)
        summary = {
            "version": "2.1.4", "status": status.value,
            "start_time": datetime.fromtimestamp(self.start_time, tz=timezone.utc).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(total_elapsed, 2),
            "categories": cat_dicts,
            "totals": {"total_records": total_records, "unique_products": len(unique_keys),
                "image_download_success": image_stats["success"],
                "image_download_failed": image_stats["failed"],
                "success_pages": sum(s.success_pages for s in self.stats.values()),
                "failed_pages": sum(s.failed_pages for s in self.stats.values()),
                "target_categories": target_cats,
                "completed_categories": completed_cats},
        }
        write_atomic_json(os.path.join(self.output_dir, "run_summary.json"), summary)
        return summary

    def get_exit_code(self):
        return (RunStatus.INTERRUPTED if self._interrupted else self._run_status).exit_code


# ============================================================
# V2.1.4-final: Soup helpers (单次DOM解析)
# ============================================================
def _extract_next_page_soup(soup, current_url: str) -> str:
    from urllib.parse import urljoin
    nl = soup.select_one('link[rel="next"]')
    if nl and nl.get("href"): return urljoin(current_url, nl.get("href"))
    for a in soup.select('[class*="pagination"] a'):
        if "urmatoare" in a.get_text(strip=True).lower():
            href = a.get("href")
            if href and href != "javascript:void(0)": return urljoin(current_url, href)
    return None

def _extract_total_pages_soup(soup):
    import re
    max_page = 0
    for item in soup.select('[class*="pagination"] a, [class*="pagination"] span'):
        text = item.get_text(strip=True)
        m = re.search(r'(\d+)\s*din\s*(\d+)', text)
        if m:
            total = int(m.group(2))
            if total > max_page: max_page = total
        try:
            n = int(text)
            if n > max_page: max_page = n
        except ValueError: pass
    return max_page if max_page > 0 else None
