"""
爬虫核心 V2.0.3: 纯HTTP, 真正Session复用, 按页码顺序提交, --all-pages=20
"""
import hashlib
import json
import logging
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from datetime import datetime, timezone
from threading import Lock, Semaphore, Event
from typing import Optional
from urllib.parse import urljoin

from scrapling.fetchers import FetcherSession

from models import ProductItem
from parser import parse_product_listing, extract_next_page, page_has_products
from image_downloader import ImageDownloader
from exporters import Exporters
from utils import (
    detect_waf_block, WafBlockError, get_product_key,
    write_errors_csv, write_atomic_json, ensure_errors_csv,
    EXIT_CAPTCHA, EXIT_NETWORK_ERROR,
)

logger = logging.getLogger("emag_crawler.crawler")

# --all-pages 每个类目最大页数
ALL_PAGES_LIMIT = 20


class CategoryStats:
    """单个类目的抓取统计"""

    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.requested_pages = 0
        self.success_pages = 0
        self.failed_pages = 0
        self.total_records = 0
        self.unique_products = 0
        self.image_success = 0
        self.image_failed = 0
        self.start_time = time.time()
        self.end_time = 0.0

    @property
    def elapsed(self) -> float:
        return self.end_time - self.start_time if self.end_time else 0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "url": self.url,
            "requested_pages": self.requested_pages,
            "success_pages": self.success_pages,
            "failed_pages": self.failed_pages,
            "total_records": self.total_records,
            "unique_products": self.unique_products,
            "image_success": self.image_success,
            "image_failed": self.image_failed,
            "elapsed_seconds": round(self.elapsed, 2),
        }


class EmagCrawler:
    """eMAG 商品列表爬虫 V2.0.3 — 纯 HTTP, 真正 Session 复用"""

    def __init__(
        self,
        output_dir: str,
        image_downloader: Optional[ImageDownloader] = None,
        page_workers: int = 3,
        category_workers: int = 2,
        max_in_flight: int = 8,
        download_images: bool = True,
        log_level: str = "INFO",
        all_pages: bool = False,
    ):
        self.output_dir = output_dir
        self.download_images = download_images
        self.image_downloader = image_downloader
        self.page_workers = page_workers
        self.category_workers = category_workers
        self.all_pages = all_pages

        self.global_semaphore = Semaphore(max_in_flight)
        self.exporters = Exporters(output_dir)
        self.stats: dict[str, CategoryStats] = {}
        self._stats_lock = Lock()

        self.errors_file = os.path.join(output_dir, "errors.csv")
        self._error_lock = Lock()
        self._error_header_written = False

        self.start_time = time.time()
        self._waf_stop = Event()

        # 每线程 Session: (manager, client)
        self._thread_local = threading.local()
        self._session_config = {
            "impersonate": "chrome136",
            "stealthy_headers": True,
            "timeout": 30,
            "retries": 3,
            "retry_delay": 1,
        }
        # Session 注册表: [(manager, client), ...]
        self._all_sessions: list[tuple] = []
        self._sessions_lock = Lock()

        # 页面去重: 按类目 URL 隔离
        self._cat_page_hashes: dict[str, set] = {}
        self._hash_lock = Lock()

    # ================================================================
    # Session 管理 (真正复用底层客户端)
    # ================================================================
    def _get_client(self):
        """获取当前线程的 HTTP 客户端 (复用底层 Session)"""
        if not hasattr(self._thread_local, 'client'):
            mgr = FetcherSession(**self._session_config)
            try:
                client = mgr.__enter__()
            except Exception:
                # 初始化失败不注册
                raise
            self._thread_local.mgr = mgr
            self._thread_local.client = client
            with self._sessions_lock:
                self._all_sessions.append((mgr, client))
        return self._thread_local.client

    def _close_all_sessions(self):
        """安全关闭所有 Session"""
        with self._sessions_lock:
            for mgr, client in list(self._all_sessions):
                try:
                    mgr.__exit__(None, None, None)
                except Exception:
                    pass
            self._all_sessions.clear()

    # ================================================================
    # 页面获取
    # ================================================================
    def _fetch_page(self, url: str, name: str = "", page_num: int = 0) -> tuple[Optional[str], int]:
        """使用当前线程复用的 HTTP 客户端获取页面"""
        if self._waf_stop.is_set():
            return None, 0
        with self.global_semaphore:
            try:
                client = self._get_client()
                page = client.get(url)
                return page.html_content, page.status
            except Exception as e:
                logger.error(f"HTTP 请求失败 [{url}]: {e}")
                return None, 0

    # ================================================================
    # 页面去重 (按类目 URL 隔离)
    # ================================================================
    def _cat_key(self, base_url: str) -> str:
        return base_url.lower().rstrip("/")

    def _check_and_add_hash(self, cat_url: str, html: str) -> bool:
        """检查并登记页面哈希。返回 True 表示重复"""
        h = hashlib.md5(html.encode()).hexdigest()
        ck = self._cat_key(cat_url)
        with self._hash_lock:
            if ck not in self._cat_page_hashes:
                self._cat_page_hashes[ck] = set()
            if h in self._cat_page_hashes[ck]:
                return True
            self._cat_page_hashes[ck].add(h)
            return False

    # ================================================================
    # 类目抓取 (有界并发 + 按页码顺序提交)
    # ================================================================
    def crawl_category(
        self, name: str, url: str, max_pages: Optional[int] = None
    ) -> CategoryStats:
        stats = CategoryStats(name, url)
        with self._stats_lock:
            self.stats[name] = stats

        if max_pages is not None:
            hard_limit = max_pages
        elif self.all_pages:
            hard_limit = ALL_PAGES_LIMIT
        else:
            hard_limit = 1

        limit_str = f"{hard_limit} 页" if hard_limit else "1"
        logger.info(f"[{name}] 开始抓取: {url} (页数限制: {limit_str})")

        # --- 首页 ---
        first_html, first_status = self._fetch_page(url, name=name, page_num=1)
        stats.requested_pages += 1

        waf = detect_waf_block(first_html or "", first_status, url, category=name, page_num=1)
        if waf:
            self._handle_waf_block(waf, name, 1, url, first_html or "")
            stats.end_time = time.time()
            return stats

        if first_status != 200 or not first_html:
            stats.failed_pages += 1
            self._log_error(name, 1, url, "HTTP_ERROR", first_status, f"HTTP {first_status}")
            stats.end_time = time.time()
            return stats

        if not page_has_products(first_html):
            logger.info(f"[{name}] 首页无商品")
            stats.end_time = time.time()
            return stats

        self._check_and_add_hash(url, first_html)

        products = parse_product_listing(first_html, category_name=name,
                                         category_url=url, page_url=url, page_number=1)
        self.exporters.add_products(products)
        stats.success_pages += 1
        stats.total_records += len(products)
        logger.info(f"[{name}] 第1页: {len(products)} 个商品")

        next_url = extract_next_page(first_html, url)
        if not next_url or hard_limit <= 1:
            stats.end_time = time.time()
            return stats

        # 从 next_url 推导页码模板
        import re as _re
        url_template = None
        m = _re.match(r'(.*?/p)(\d+)(/c.*)', next_url)
        if m:
            url_template = (m.group(1), m.group(3))

        # --- 后续页面: 并发获取, 按页码顺序提交 ---
        pending = list(range(2, hard_limit + 1))
        completed: dict[int, tuple] = {}  # page_num -> result
        in_flight: dict[Future, int] = {}
        next_idx = 0
        stopped = False
        lock = Lock()

        def _submit_one(executor, page_num):
            page_url = self._make_page_url(url_template, page_num) if url_template else None
            if not page_url:
                return None
            fut = executor.submit(self._fetch_and_parse, name, url, page_num, page_url)
            return fut

        with ThreadPoolExecutor(max_workers=self.page_workers) as executor:
            # 初始填满
            while next_idx < len(pending) and len(in_flight) < self.page_workers:
                if self._waf_stop.is_set():
                    stopped = True
                    break
                pn = pending[next_idx]
                fut = _submit_one(executor, pn)
                if fut is None:
                    stopped = True
                    break
                in_flight[fut] = pn
                stats.requested_pages += 1
                next_idx += 1

            # 收集结果 + 补充新任务
            next_expected = 2
            while in_flight and not stopped:
                done_futs = []
                for fut in list(in_flight.keys()):
                    if fut.done():
                        done_futs.append(fut)

                if not done_futs:
                    time.sleep(0.01)
                    continue

                for fut in done_futs:
                    pn = in_flight.pop(fut)
                    try:
                        result = fut.result()
                    except Exception as e:
                        completed[pn] = ("error", str(e), None)
                        continue
                    completed[pn] = result

                # 按顺序处理已完成的结果
                with lock:
                    while next_expected in completed and not stopped:
                        raw_result = completed.pop(next_expected)

                        if raw_result[0] == "error":
                            stats.failed_pages += 1
                            self._log_error(name, next_expected, "", "FUTURE_ERROR",
                                           detail=str(raw_result[1])[:500])
                            stopped = True
                            break

                        # 顺序处理: 登记哈希 + 判断重复
                        products_list, status, waf_err = self._process_ordered_result(url, raw_result)

                        if waf_err:
                            self._handle_waf_block(waf_err, name, next_expected,
                                                   self._make_page_url(url_template, next_expected) or "", "")
                            self._waf_stop.set()
                            stopped = True
                            break

                        if status == 200 and products_list:
                            self.exporters.add_products(products_list)
                            stats.success_pages += 1
                            stats.total_records += len(products_list)
                            logger.info(f"[{name}] 第{next_expected}页: {len(products_list)} 个商品")
                        else:
                            stats.failed_pages += 1
                            if status != 200:
                                logger.warning(f"[{name}] 第{next_expected}页 HTTP {status}, 停止翻页")
                            stopped = True
                            break

                        next_expected += 1

                # 补充新任务
                while (not stopped and not self._waf_stop.is_set()
                       and next_idx < len(pending)
                       and len(in_flight) < self.page_workers):
                    pn = pending[next_idx]
                    fut = _submit_one(executor, pn)
                    if fut is None:
                        stopped = True
                        break
                    in_flight[fut] = pn
                    stats.requested_pages += 1
                    next_idx += 1

            # 取消未完成的任务
            for fut in list(in_flight.keys()):
                fut.cancel()

        stats.end_time = time.time()
        logger.info(f"[{name}] 完成: 成功 {stats.success_pages}/{stats.requested_pages} 页, "
                    f"商品 {stats.total_records} 条, 耗时 {stats.elapsed:.1f}s")
        return stats

    def _make_page_url(self, template: Optional[tuple], page_num: int) -> Optional[str]:
        if not template:
            return None
        return f"{template[0]}{page_num}{template[1]}"

    def _fetch_and_parse(self, name: str, base_url: str, page_num: int, url: str) -> tuple:
        """获取并解析单个页面 (在线程池中执行)。
        页面哈希在此处计算但不登记 (由顺序处理阶段登记)。
        返回格式: (products_or_None, http_status, waf_or_None, html_hash_or_None)
        """
        html, status = self._fetch_page(url, name=name, page_num=page_num)

        waf = detect_waf_block(html or "", status, url, category=name, page_num=page_num)
        if waf:
            return (None, status, waf, None)

        if status != 200 or not html:
            return (None, status, None, None)

        if not page_has_products(html):
            return ([], 200, None, None)

        h = hashlib.md5(html.encode()).hexdigest()

        products = parse_product_listing(html, category_name=name, category_url=base_url,
                                         page_url=url, page_number=page_num)
        return (products, 200, None, h)

    def _process_ordered_result(self, base_url: str, result: tuple):
        """按顺序处理结果时登记哈希, 返回规范化的三元组。
        如果页面哈希已存在(重复), 返回 ([], 200, None)"""
        products, status, waf, h = result
        if h:
            ck = self._cat_key(base_url)
            with self._hash_lock:
                if ck not in self._cat_page_hashes:
                    self._cat_page_hashes[ck] = set()
                if h in self._cat_page_hashes[ck]:
                    # 重复页面
                    return ([], 200, None)
                self._cat_page_hashes[ck].add(h)
        return (products, status, waf)

    # ================================================================
    # 所有类目调度
    # ================================================================
    def crawl_all_categories(self, categories: list[dict],
                             max_pages: Optional[int] = None) -> dict:
        if not categories:
            logger.warning("没有启用的类目")
            return {}
        logger.info(f"共 {len(categories)} 个类目, 并发数: {self.category_workers}")
        results = {}
        if self.category_workers <= 1:
            for cat in categories:
                if self._waf_stop.is_set():
                    break
                results[cat["name"]] = self.crawl_category(cat["name"], cat["url"], max_pages)
        else:
            with ThreadPoolExecutor(max_workers=self.category_workers) as executor:
                futures = {}
                for cat in categories:
                    fut = executor.submit(self.crawl_category, cat["name"], cat["url"], max_pages)
                    futures[fut] = cat["name"]
                for fut in as_completed(futures):
                    if self._waf_stop.is_set():
                        for f in futures:
                            f.cancel()
                    name = futures[fut]
                    try:
                        results[name] = fut.result()
                    except Exception as e:
                        logger.error(f"类目 [{name}] 异常: {e}")
        return results

    # ================================================================
    # WAF 阻断
    # ================================================================
    def _handle_waf_block(self, waf: WafBlockError, name: str, page_num: int,
                          url: str, html: str):
        self._waf_stop.set()
        saved_count = self.exporters.get_product_count()
        timestamp = datetime.now(timezone.utc).isoformat()
        self._log_error(name, page_num, url, f"WAF_{waf.block_type}",
                       waf.status_code, detail=waf.evidence)
        diag_dir = os.path.join(self.output_dir, "diagnostics")
        os.makedirs(diag_dir, exist_ok=True)
        diagnostic = {
            "status": "waf_blocked", "timestamp": timestamp,
            "category": name, "page": page_num, "url": url,
            "http_status": waf.status_code, "block_type": waf.block_type,
            "evidence": waf.evidence, "products_saved": saved_count,
        }
        diag_path = os.path.join(diag_dir, "captcha_diagnostic.json")
        with open(diag_path, "w", encoding="utf-8") as f:
            json.dump(diagnostic, f, ensure_ascii=False, indent=2)
        if html:
            import re as _re
            sanitized = html
            sanitized = _re.sub(r'apiKey["\']?\s*[:=]\s*["\'][^"\']+["\']',
                              'apiKey="[REDACTED]"', sanitized)
            sanitized = _re.sub(r'cookieValue=([^&\s"\'<>]+)',
                              'cookieValue=[REDACTED]', sanitized)
            html_path = os.path.join(diag_dir, "captcha_response.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(sanitized)
        sys_mod = __import__('sys')
        print("\n" + "!" * 60, file=sys_mod.stderr)
        print("  检测到eMAG验证码、WAF或访问限制，任务已停止。", file=sys_mod.stderr)
        print("  请手动处理访问验证或等待限制解除后重新执行。", file=sys_mod.stderr)
        print("!" * 60, file=sys_mod.stderr)
        for key, val in [("HTTP 状态码", waf.status_code), ("类目", name),
                         ("页码", page_num), ("URL", url),
                         ("阻断类型", waf.block_type), ("发生时间", timestamp),
                         ("已保存商品数", saved_count), ("输出目录", self.output_dir),
                         ("诊断文件", diag_path)]:
            print(f"  {key}: {val}", file=sys_mod.stderr)
        print("!" * 60 + "\n", file=sys_mod.stderr)

    # ================================================================
    # 图片下载
    # ================================================================
    def download_images_for_products(self) -> dict[str, str]:
        if not self.image_downloader:
            return {}
        all_products = self.exporters.get_products_sorted()
        return self.image_downloader.download_batch(all_products)

    # ================================================================
    # 错误记录 (统一 errors.csv 表头)
    # ================================================================
    ERROR_FIELDNAMES = ["时间", "类目", "页码", "商品键", "URL", "错误类型", "HTTP状态码", "重试次数", "错误详情"]

    def _log_error(self, name: str, page: int, url: str, error_type: str,
                   http_status: int = 0, retries: int = 0, detail: str = "",
                   product_key: str = ""):
        error_data = {
            "时间": datetime.now(timezone.utc).isoformat(),
            "类目": name, "页码": page, "商品键": product_key, "URL": url,
            "错误类型": error_type, "HTTP状态码": http_status,
            "重试次数": retries, "错误详情": detail,
        }
        with self._error_lock:
            write_errors_csv(self.errors_file, error_data,
                           write_header=not self._error_header_written,
                           fieldnames=self.ERROR_FIELDNAMES)
            if not self._error_header_written:
                self._error_header_written = True

    def _log_image_errors(self, img_stats: dict):
        """将图片下载错误写入 errors.csv"""
        for err in img_stats.get("errors", []):
            self._log_error(
                name=err.get("category", ""),
                page=err.get("page", 0),
                url=err.get("image_url", err.get("url", "")),
                error_type=err.get("error_type", "IMAGE_ERROR"),
                http_status=err.get("http_status", 0),
                detail=err.get("error_detail", str(err)),
                product_key=err.get("product_key", ""),
            )

    # ================================================================
    # 完成导出
    # ================================================================
    def finalize(self) -> dict:
        total_elapsed = time.time() - self.start_time
        self._close_all_sessions()

        image_stats = {"success": 0, "failed": 0, "errors": []}
        path_map: dict[str, str] = {}
        if self.download_images and self.image_downloader:
            path_map = self.download_images_for_products()
            image_stats = self.image_downloader.get_stats()

        # 图片错误写入 errors.csv
        if image_stats.get("errors"):
            self._log_image_errors(image_stats)

        # 回填图片路径
        if path_map:
            with self.exporters._lock:
                for item in self.exporters._products:
                    key = get_product_key(item)
                    if key in path_map:
                        item["main_image_local_path"] = path_map[key]

        sorted_prods = self.exporters.get_products_sorted()
        total_records = len(sorted_prods)
        unique_keys = {get_product_key(item) for item in sorted_prods}

        with self._stats_lock:
            for name, stats in self.stats.items():
                img_ok = 0
                img_fail = 0
                cat_keys = set()
                cat_count = 0
                for item in sorted_prods:
                    if item.get("category_name") == name:
                        cat_count += 1
                        cat_keys.add(get_product_key(item))
                        if item.get("main_image_local_path"):
                            img_ok += 1
                        elif item.get("main_image_url"):
                            img_fail += 1
                stats.total_records = cat_count
                stats.unique_products = len(cat_keys)
                stats.image_success = img_ok
                stats.image_failed = img_fail

        waf_detected = self._waf_stop.is_set()
        ensure_errors_csv(self.errors_file, self.ERROR_FIELDNAMES)
        self.exporters.finalize()

        summary = {
            "version": "2.0.3",
            "status": "waf_blocked" if waf_detected else "completed",
            "start_time": datetime.fromtimestamp(self.start_time, tz=timezone.utc).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(total_elapsed, 2),
            "categories": [s.to_dict() for s in self.stats.values()],
            "totals": {
                "total_records": total_records,
                "unique_products": len(unique_keys),
                "image_download_success": image_stats["success"],
                "image_download_failed": image_stats["failed"],
                "success_pages": sum(s.success_pages for s in self.stats.values()),
                "failed_pages": sum(s.failed_pages for s in self.stats.values()),
            },
        }
        summary_path = os.path.join(self.output_dir, "run_summary.json")
        write_atomic_json(summary_path, summary)
        logger.info(f"汇总已写入: {summary_path}")
        return summary
