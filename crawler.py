"""
爬虫核心 V2.1.2: 纯HTTP, 顺序提交, 全部卡片保留
"""
import hashlib, json, logging, os, time, threading
import re
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock, Semaphore, Event
from typing import Optional
from scrapling.fetchers import FetcherSession
from category_hierarchy import (
    CategoryEvidenceDecision,
    CategoryEvidenceStatus,
    CategoryPathEvidence,
    extract_page_category_decision,
    normalize_category_url,
)
from models import ProductItem
from parser import _parse_product_card, select_product_cards
from image_downloader import ImageDownloader
from exporters import Exporters
from utils import (detect_waf_block, get_product_key,
    write_errors_csv, write_atomic_json, ensure_errors_csv, RunStatus,
    get_visible_page_text)

logger = logging.getLogger("emag_crawler.crawler")
ALL_PAGES_LIMIT = 20
EMPTY_CATEGORY_MARKERS = (
    "niciun produs", "nu am gasit", "nu am găsit",
)
CATEGORY_UNAVAILABLE_MARKERS = (
    "categoria nu mai este disponibila",
    "categoria nu mai este disponibilă",
    "pagina nu mai este disponibila",
    "pagina nu mai este disponibilă",
    "aceasta categorie nu exista",
    "această categorie nu există",
)

_SENSITIVE_DIAGNOSTIC_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^;,\s]+(?:\s+[^;,\s]+)?)"),
    re.compile(r"(?i)(cookie\s*[:=]\s*)([^;,\s]+)"),
    re.compile(r"(?i)((?:access[_-]?)?token\s*[:=]\s*)([^;,\s]+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^;,\s]+)"),
)


def _sanitize_diagnostic_text(value) -> str:
    text = str(value or "")
    for pattern in _SENSITIVE_DIAGNOSTIC_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


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
    final_url: str = ""
    redirect_chain: list = field(default_factory=list)
    content_type: str = ""
    content_length: int = 0
    page_title: str = ""
    empty_evidence: str = ""
    waf_evidence: str = ""
    terminal_reason: str = ""
    diagnostic_paths: list = field(default_factory=list)
    category_levels: list = field(default_factory=list)
    category_level_source: str = ""
    category_level_reliability: int = 0
    category_levels_from_cache: bool = False
    category_evidence_decision: Optional[CategoryEvidenceDecision] = None


@dataclass
class FetchResult:
    html: Optional[str] = None
    status: int = 0
    request_url: str = ""
    final_url: str = ""
    redirect_chain: list = field(default_factory=list)
    content_type: str = ""
    content_length: int = 0
    fetched_at: str = ""
    error_type: str = ""
    error_detail: str = ""
    request_call_started: bool = False
    response_received: bool = False
    session_generation: int = 0


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
        self._session_generation = 0
        self._cat_page_hashes: dict[str, set] = {}; self._hash_lock = Lock()
        # 运行内唯一商品键集合 (替代已删除的 checkpoint)
        self._product_keys: set = set()
        self._keys_lock = Lock()
        self._category_level_locks: dict[str, Lock] = {}
        self._category_level_locks_guard = Lock()
        self._category_level_upgrade_checks: dict[str, int] = {}
        self._category_level_observed_pages: dict[str, set[int]] = {}
        self._category_level_max_upgrade_checks = 3
        os.makedirs(output_dir, exist_ok=True)

    # ---- Session ----
    def _validated_session_retries(self) -> int:
        value = self._session_config.get("retries")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                "Scrapling FetcherSession retries must be an integer >= 1 "
                f"(total request attempts); got {value!r}")
        return value

    def _get_client(self):
        self._validated_session_retries()
        with self._sessions_lock:
            generation = self._session_generation
            manager = getattr(self._thread_local, "mgr", None)
            client = getattr(self._thread_local, "client", None)
            local_generation = getattr(
                self._thread_local, "session_generation", None)
            if (client is not None and local_generation == generation and
                    self._session_is_active(manager, client)):
                return client

            manager = FetcherSession(**self._session_config)
            client = manager.__enter__()
            if not self._session_is_active(manager, client):
                try:
                    manager.__exit__(None, None, None)
                finally:
                    raise RuntimeError("FetcherSession did not enter an active state")
            self._thread_local.mgr = manager
            self._thread_local.client = client
            self._thread_local.session_generation = generation
            self._all_sessions.append((manager, client))
            return client

    @staticmethod
    def _session_is_active(manager, client) -> bool:
        if manager is None or client is None:
            return False
        alive = getattr(manager, "_is_alive", None)
        if alive is not None:
            return bool(alive)
        closed = getattr(client, "closed", None)
        if closed is not None:
            return not bool(closed)
        return True

    def _session_diagnostic_snapshot(self) -> dict:
        manager = getattr(self._thread_local, "mgr", None)
        client = getattr(self._thread_local, "client", None)
        local_generation = getattr(
            self._thread_local, "session_generation", None)
        with self._sessions_lock:
            generation = self._session_generation
        current_generation = local_generation == generation
        return {
            "manager_entered": manager is not None and current_generation,
            "client_stored": client is not None and current_generation,
            "session_generation": generation,
            "client_generation": local_generation,
            "session_active": (current_generation and
                               self._session_is_active(manager, client)),
        }

    def _validate_client_before_request(self, client) -> None:
        manager = getattr(self._thread_local, "mgr", None)
        if not self._session_is_active(manager, client):
            raise RuntimeError("FetcherSession is not active before client.get()")

    def _close_all_sessions(self):
        with self._sessions_lock:
            sessions = list(self._all_sessions)
            self._all_sessions.clear()
            self._session_generation += 1
        for mgr, _client in sessions:
            try: mgr.__exit__(None, None, None)
            except Exception: pass

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
        fetched_at = datetime.now(timezone.utc).isoformat()
        if self._stop_event.is_set():
            return FetchResult(request_url=url, final_url=url,
                               fetched_at=fetched_at)
        request_call_started = False
        response_received = False
        client = None
        with self.global_semaphore:
            try:
                client = self._get_client()
                self._validate_client_before_request(client)
                # This means client.get() is now being invoked; it does not
                # claim that a network packet reached the remote host.
                request_call_started = True
                page = client.get(url)
                response_received = True
                html = page.html_content or ""
                final_url = str(getattr(page, "url", "") or
                                getattr(page, "response_url", "") or url)
                history = []
                for item in (getattr(page, "history", None) or []):
                    history.append({
                        "status": int(getattr(item, "status_code", 0) or
                                      getattr(item, "status", 0) or 0),
                        "url": str(getattr(item, "url", "") or ""),
                    })
                headers = getattr(page, "headers", {}) or {}
                try:
                    content_type = (headers.get("content-type", "") or
                                    headers.get("Content-Type", ""))
                except Exception:
                    content_type = ""
                return FetchResult(
                    html=html,
                    status=int(getattr(page, "status", 0) or 0),
                    request_url=url,
                    final_url=final_url,
                    redirect_chain=history,
                    content_type=content_type,
                    content_length=len(html.encode("utf-8")),
                    fetched_at=fetched_at,
                    request_call_started=request_call_started,
                    response_received=response_received,
                    session_generation=self._session_diagnostic_snapshot()[
                        "session_generation"],
                )
            except Exception as e:
                retries = self._session_config.get("retries")
                session = self._session_diagnostic_snapshot()
                safe_error = _sanitize_diagnostic_text(e)
                if not request_call_started:
                    if isinstance(e, ValueError):
                        phase = "configuration_validation"
                    elif client is None:
                        phase = "session_creation"
                    else:
                        phase = "before_client_get"
                elif not response_received:
                    phase = "inside_client_get"
                else:
                    phase = "response_processing"
                detail = (
                    f"{type(e).__name__}: {safe_error}; retries={retries!r}; "
                    f"phase={phase}; "
                    f"manager_entered={session['manager_entered']}; "
                    f"client_stored={session['client_stored']}; "
                    f"session_generation={session['session_generation']}; "
                    f"client_generation={session['client_generation']!r}; "
                    f"session_active={session['session_active']}; "
                    f"thread_id={threading.get_ident()}; "
                    f"request_call_started={request_call_started}; "
                    f"response_received={response_received}"
                )
                logger.error(f"HTTP [{url}]: {detail}")
                return FetchResult(request_url=url, final_url=url,
                                   fetched_at=fetched_at,
                                   error_type=type(e).__name__,
                                   error_detail=detail,
                                   request_call_started=request_call_started,
                                   response_received=response_received,
                                   session_generation=session[
                                       "session_generation"])

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
        fetched = self._fetch_page(page_url)
        html, st = fetched.html, fetched.status
        pr.http_status = st
        pr.final_url = fetched.final_url or page_url
        pr.redirect_chain = fetched.redirect_chain
        pr.content_type = fetched.content_type
        pr.content_length = fetched.content_length
        if fetched.error_type:
            pr.fatal_error_type = fetched.error_type
            pr.fatal_error_detail = fetched.error_detail

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
            return pr

        title_node = soup.select_one("title")
        pr.page_title = title_node.get_text(" ", strip=True) if title_node else ""

        # 标题是HTTP 200阶段唯一允许在商品解析前判断的WAF证据。
        title_waf = detect_waf_block(
            html, st, page_url, category=name, page_num=page_num,
            soup=soup, check_title=True, check_body=False)
        if title_waf:
            pr.waf_error = title_waf
            pr.waf_evidence = title_waf.evidence
            return pr

        # 元数据(复用soup)
        pr.next_url = _extract_next_page_soup(soup, page_url) or ""
        pr.has_next = bool(pr.next_url)
        pr.total_pages = _extract_total_pages_soup(soup)
        pr.html_hash = hashlib.md5(html.encode()).hexdigest()

        # S0-1修复: 直接解析商品, 不使用前置预判
        products, parse_errors = self._parse_products_soup(soup, name, base_url, page_url, page_num)
        pr.cards_found = len(products) + len(parse_errors)
        pr.products_parsed = len(products); pr.parse_failed = len(parse_errors)
        pr.parse_errors = parse_errors; pr.all_products = list(products)

        # 商品解析后再检查真正可见的验证码UI/正文；脚本和隐藏祖先均被排除。
        body_waf = detect_waf_block(
            html, st, page_url, category=name, page_num=page_num,
            soup=soup, check_title=False, check_body=True,
            has_products=bool(products))
        if body_waf:
            pr.waf_error = body_waf
            pr.waf_evidence = body_waf.evidence
            return pr

        if products:
            evidence, from_cache = self._get_or_extract_category_evidence(
                soup, name, base_url, page_number=page_num)
            if evidence:
                pr.category_levels = list(evidence.levels)
                pr.category_level_source = evidence.source
                pr.category_level_reliability = evidence.reliability
                pr.category_levels_from_cache = from_cache
            pr.category_evidence_decision = getattr(
                self._thread_local, "last_category_evidence_decision", None)
            return pr

        if parse_errors:
            # 调用方会先逐卡写入 errors.csv，再追加页面级错误。
            pr.analysis_failed = True
            pr.fatal_error_type = "ALL_PARSE_FAILED"
            pr.fatal_error_detail = f"All {len(parse_errors)} cards failed to parse"
            return pr

        # 无候选卡片：只接受明确的可见DOM证据。
        page_text = get_visible_page_text(soup).lower()
        pr.empty_evidence = _find_page_marker(page_text, EMPTY_CATEGORY_MARKERS)
        if pr.empty_evidence:
            pr.is_last_page = True
            pr.terminal_reason = "empty_category"
            return pr

        unavailable = _find_page_marker(page_text, CATEGORY_UNAVAILABLE_MARKERS)
        if unavailable:
            pr.empty_evidence = unavailable
            pr.is_last_page = True
            pr.terminal_reason = "category_unavailable"
            return pr

        pr.analysis_failed = True
        pr.fatal_error_type = "UNKNOWN_HTTP200_PAGE"
        pr.fatal_error_detail = "No products, no empty-category, no unavailable-category, no visible WAF evidence"
        pr.diagnostic_paths = self._save_unknown_http200_diagnostic(
            name, pr, html)
        return pr

    @staticmethod
    def _parse_html_once(html: str):
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html, "lxml")
        except Exception:
            return None

    def _parse_products_soup(self, soup, name, base_url, page_url, page_num):
        cards = select_product_cards(soup)
        products, errors = [], []
        for idx, card in enumerate(cards):
            try:
                p = _parse_product_card(card, name, base_url, page_url, page_num)
                if p: products.append(p)
                else:
                    pos = card.get("data-position", str(idx+1)); pid = card.get("data-product-id","")
                    fav = card.select_one(".add-to-favorites")
                    pid = pid or (fav.get("data-productid", "") if fav else "")
                    errors.append({"position": pos, "product_id": pid,
                        "url": card.get("data-url", "") or page_url,
                        "error_type": "PARSE_FAILED", "error_detail": "解析返回None"})
            except Exception as e:
                pos = card.get("data-position", str(idx+1)); pid = card.get("data-product-id","")
                fav = card.select_one(".add-to-favorites")
                pid = pid or (fav.get("data-productid", "") if fav else "")
                errors.append({"position": pos, "product_id": pid,
                    "url": card.get("data-url", "") or page_url,
                    "error_type": type(e).__name__, "error_detail": str(e)[:500]})
        return products, errors

    def _get_or_extract_category_evidence(
        self,
        soup,
        category_name: str,
        category_url: str,
        page_number: int | None = None,
    ) -> tuple[Optional[CategoryPathEvidence], bool]:
        """Observe at most logical pages 1-3; every observed page is resolved once."""
        cache_key = normalize_category_url(category_url)
        with self._category_level_locks_guard:
            category_lock = self._category_level_locks.setdefault(
                cache_key, Lock())
        with category_lock:
            cached = self.exporters.get_category_level_evidence(category_url)
            checks = self._category_level_upgrade_checks.get(cache_key, 0)
            observed = self._category_level_observed_pages.setdefault(
                cache_key, set())
            if page_number is not None:
                if page_number < 1 or page_number > self._category_level_max_upgrade_checks:
                    self._thread_local.last_category_evidence_decision = None
                    return cached, True
                if page_number in observed:
                    self._thread_local.last_category_evidence_decision = None
                    return cached, True
                observation_id = page_number
            else:
                if checks >= self._category_level_max_upgrade_checks:
                    self._thread_local.last_category_evidence_decision = None
                    return cached, True
                observation_id = checks + 1
            if checks >= self._category_level_max_upgrade_checks:
                self._thread_local.last_category_evidence_decision = None
                return cached, True
            observed.add(observation_id)
            self._category_level_upgrade_checks[cache_key] = checks + 1
            decision = extract_page_category_decision(
                soup, category_name, category_url)
            self._thread_local.last_category_evidence_decision = decision
            self.exporters.register_category_decision(category_url, decision)
            return self.exporters.get_category_level_evidence(category_url), False

    def _save_unknown_http200_diagnostic(self, name, pr: PageResult,
                                         html: str) -> list[str]:
        """异常时保存原始HTML和不含认证信息的结构化诊断。"""
        diag_dir = os.path.join(self.output_dir, "diagnostics")
        os.makedirs(diag_dir, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        suffix = (pr.html_hash or hashlib.md5(html.encode("utf-8")).hexdigest())[:10]
        base = f"unknown_http200_page_{stamp}_{suffix}"
        html_path = os.path.join(diag_dir, base + ".html")
        json_path = os.path.join(diag_dir, base + ".json")

        tmp_html = html_path + ".tmp"
        try:
            with open(tmp_html, "w", encoding="utf-8") as f:
                f.write(html)
            os.replace(tmp_html, html_path)
        finally:
            if os.path.exists(tmp_html):
                os.remove(tmp_html)

        write_atomic_json(json_path, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": name,
            "page": pr.page_number,
            "request_url": pr.page_url,
            "final_url": pr.final_url or pr.page_url,
            "redirect_chain": pr.redirect_chain,
            "http_status": pr.http_status,
            "content_type": pr.content_type,
            "content_length": pr.content_length,
            "page_title": pr.page_title,
            "html_hash": pr.html_hash,
            "candidate_cards": pr.cards_found,
            "parsed_products": pr.products_parsed,
            "parse_failed": pr.parse_failed,
            "empty_evidence": pr.empty_evidence,
            "waf_evidence": pr.waf_evidence,
            "fatal_error_type": pr.fatal_error_type,
            "fatal_error_detail": pr.fatal_error_detail,
        })
        return [os.path.abspath(html_path), os.path.abspath(json_path)]

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
            stats.failed_pages += 1
            stats.stop_reason = pr.fatal_error_type or "page_analysis_error"
            self._commit_page_errors(name, url, pr)
            self._log_error(name, 1, url, stats.stop_reason, detail=pr.fatal_error_detail)
            self._report_analysis_failure(name, pr)
            self._run_status = RunStatus.NETWORK_ERROR; return stats
        if pr.http_status != 200:
            stats.failed_pages += 1
            self._handle_stop(RunStatus.NETWORK_ERROR, name, 1, url, detail=f"HTTP {pr.http_status}"); return stats
        if pr.cards_found == 0 and pr.is_last_page:
            self._commit_page(name, url, pr, stats)
            stats.stop_reason = pr.terminal_reason or "empty_category"; return stats
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
                            stats.failed_pages += 1; stats.stop_reason = pr.fatal_error_type or "page_analysis_error"
                            self._commit_page_errors(name, url, pr)
                            self._log_error(name, next_expected, pr.page_url, stats.stop_reason, detail=pr.fatal_error_detail)
                            self._report_analysis_failure(name, pr)
                            self._run_status = RunStatus.NETWORK_ERROR; stopped = True; break
                        if pr.http_status != 200:
                            stats.failed_pages += 1; stats.stop_reason = "network_error"
                            self._handle_stop(RunStatus.NETWORK_ERROR, name, next_expected, pr.page_url, detail=f"HTTP {pr.http_status}")
                            stopped = True; break
                        if pr.cards_found == 0 and pr.is_last_page:
                            self._commit_page(name, url, pr, stats)
                            stats.stop_reason = "actual_last_page_reached"
                            stopped = True; break
                        if pr.cards_found == 0:
                            stats.failed_pages += 1; stats.stop_reason = "UNKNOWN_HTTP200_PAGE"
                            self._run_status = RunStatus.NETWORK_ERROR; stopped = True; break
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
            self._log_error(name, pr.page_number,
                err.get("url") or pr.page_url,
                err.get("error_type","PARSE_FAILED"),
                detail=err.get("error_detail",""),
                product_key=err.get("product_id",""),
                position=err.get("position", ""))

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
            self._log_error(name, pr.page_number,
                err.get("url") or pr.page_url,
                err.get("error_type","PARSE_FAILED"),
                detail=err.get("error_detail",""),
                product_key=err.get("product_id",""),
                position=err.get("position", ""))

    @staticmethod
    def _report_analysis_failure(name: str, pr: PageResult):
        s = __import__('sys')
        print(f"\n[页面分析失败] 错误类型: {pr.fatal_error_type}", file=s.stderr)
        print(f"  错误详情: {pr.fatal_error_detail}", file=s.stderr)
        print(f"  类目: {name}  页码: {pr.page_number}", file=s.stderr)
        for path in pr.diagnostic_paths:
            print(f"  诊断文件: {path}", file=s.stderr)

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
                "no_next_page", "empty_category", "category_unavailable"
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
    ERROR_FIELDNAMES = ["时间","类目","页码","卡片位置","商品键","URL","错误类型","HTTP状态码","重试次数","错误详情"]
    def _log_error(self, name, page, url, error_type, http_status=0, retries=0,
                   detail="", product_key="", position=""):
        d = {"时间": datetime.now(timezone.utc).isoformat(), "类目": name, "页码": page,
             "卡片位置": position,
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
                "no_next_page", "empty_category", "category_unavailable")
            cat_dicts.append(d)
        summary = {
            "version": "2.2.1", "status": status.value,
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


def _find_page_marker(page_text: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        if marker in page_text:
            return marker
    return ""
