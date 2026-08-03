"""
爬虫核心 V2.0.2：纯 HTTP 页面获取、有界并发分页、每线程Session复用、WAF全局终止
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


class CategoryStats:
    """单个类目的抓取统计"""

    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.requested_pages = 0     # 真实发出的请求次数
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
    """eMAG 商品列表爬虫 V2.0.2 — 纯 HTTP 模式"""

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

        # 全局并发控制 Semaphore (页面+图片共享)
        self.global_semaphore = Semaphore(max_in_flight)

        self.exporters = Exporters(output_dir)
        self.stats: dict[str, CategoryStats] = {}
        self._stats_lock = Lock()

        self.errors_file = os.path.join(output_dir, "errors.csv")
        self._error_lock = Lock()
        self._error_header_written = False

        self.start_time = time.time()

        # 全局 WAF 终止标志
        self._waf_stop = Event()

        # 每线程 Session 存储 (threading.local)
        self._thread_local = threading.local()
        self._session_config = {
            "impersonate": "chrome136",
            "stealthy_headers": True,
            "timeout": 30,
            "retries": 3,
            "retry_delay": 1,
        }

        # 已创建的 sessions 列表 (用于关闭)
        self._all_sessions: list = []
        self._sessions_lock = Lock()

        # 页面去重: 按类目隔离 (category_name -> set of page hashes)
        self._cat_page_hashes: dict[str, set] = {}
        self._hash_lock = Lock()

        # 图片下载结果缓存: {image_url: local_path}
        self._image_result_cache: dict[str, str] = {}
        self._img_cache_lock = Lock()

    # ================================================================
    # 每线程 Session 管理
    # ================================================================
    def _get_session(self):
        """获取当前线程的 FetcherSession (线程本地存储, 复用)"""
        if not hasattr(self._thread_local, 'session'):
            s = FetcherSession(**self._session_config)
            self._thread_local.session = s
            with self._sessions_lock:
                self._all_sessions.append(s)
        return self._thread_local.session

    def _close_all_sessions(self):
        """关闭所有已创建的 Sessions"""
        with self._sessions_lock:
            for s in self._all_sessions:
                try:
                    if hasattr(s, '_session') and s._session:
                        s._session.close()
                except Exception:
                    pass
            self._all_sessions.clear()

    # ================================================================
    # 页面获取
    # ================================================================
    def _fetch_page(self, url: str, name: str = "", page_num: int = 0) -> tuple[Optional[str], int]:
        """使用当前线程复用的 FetcherSession 获取页面"""
        if self._waf_stop.is_set():
            return None, 0

        with self.global_semaphore:
            try:
                session = self._get_session()
                with session as s:
                    page = s.get(url)
                return page.html_content, page.status
            except Exception as e:
                logger.error(f"HTTP 请求失败 [{url}]: {e}")
                return None, 0

    # ================================================================
    # 页面去重 (按类目隔离)
    # ================================================================
    def _is_duplicate_page(self, category_name: str, html: str) -> bool:
        """检查页面是否在同类目中重复"""
        h = hashlib.md5(html.encode()).hexdigest()
        with self._hash_lock:
            if category_name not in self._cat_page_hashes:
                self._cat_page_hashes[category_name] = set()
            if h in self._cat_page_hashes[category_name]:
                return True
            self._cat_page_hashes[category_name].add(h)
            return False

    # ================================================================
    # 类目抓取 (有界并发分页)
    # ================================================================
    def crawl_category(
        self, name: str, url: str, max_pages: Optional[int] = None
    ) -> CategoryStats:
        """
        抓取单个类目。
        max_pages=None + all_pages=True → 持续翻页直到最后一页。
        """
        stats = CategoryStats(name, url)
        with self._stats_lock:
            self.stats[name] = stats

        limit_str = str(max_pages) if max_pages else ("全部" if self.all_pages else "1")
        logger.info(f"[{name}] 开始抓取: {url} (页数限制: {limit_str})")

        # --- 首页 ---
        first_html, first_status = self._fetch_page(url, name=name, page_num=1)
        stats.requested_pages += 1

        # 检查 WAF 阻断 (403/429/511 统一处理)
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

        # 记录首页哈希
        self._is_duplicate_page(name, first_html)

        products = parse_product_listing(first_html, category_name=name,
                                         category_url=url, page_url=url, page_number=1)
        self.exporters.add_products(products)
        stats.success_pages += 1
        stats.total_records += len(products)
        logger.info(f"[{name}] 第1页: {len(products)} 个商品")

        next_url = extract_next_page(first_html, url)
        if not next_url:
            logger.info(f"[{name}] 只有1页")
            stats.end_time = time.time()
            return stats

        # --- 后续页面: 有界并发调度 ---
        if max_pages is not None:
            hard_limit = max_pages
        else:
            hard_limit = 999  # all_pages 安全上限 (eMAG 实际 < 200)

        if hard_limit <= 1:
            stats.end_time = time.time()
            return stats

        # 从首页的下一页推导页码模板
        import re as _re
        url_template = None
        m = _re.match(r'(.*?/p)(\d+)(/c.*)', next_url)
        if m:
            url_template = (m.group(1), m.group(3))  # (prefix, suffix)

        pending_pages = list(range(2, hard_limit + 1))
        in_flight: dict[Future, tuple[int, str]] = {}
        next_page_idx = 0
        stopped = False

        with ThreadPoolExecutor(max_workers=self.page_workers) as executor:
            # 初始提交: 填满 page_workers 个任务
            while next_page_idx < len(pending_pages) and len(in_flight) < self.page_workers:
                if self._waf_stop.is_set():
                    stopped = True
                    break
                page_num = pending_pages[next_page_idx]
                page_url = self._make_page_url(url_template, page_num) if url_template else None
                if not page_url:
                    stopped = True
                    break
                fut = executor.submit(self._fetch_and_parse_page, name, url, page_num, page_url)
                in_flight[fut] = (page_num, page_url)
                stats.requested_pages += 1
                next_page_idx += 1

            # 动态补充: 完成一个, 补充一个
            while in_flight and not stopped:
                for fut in as_completed(list(in_flight.keys())):
                    page_num, page_url = in_flight.pop(fut)
                    try:
                        result = fut.result()
                    except Exception as e:
                        stats.failed_pages += 1
                        self._log_error(name, page_num, page_url, type(e).__name__, detail=str(e)[:500])
                        stopped = True
                        break

                    products_list, status, waf_err = result

                    if waf_err:
                        self._handle_waf_block(waf_err, name, page_num, page_url, "")
                        self._waf_stop.set()
                        stopped = True
                        break

                    if status == 200 and products_list:
                        self.exporters.add_products(products_list)
                        stats.success_pages += 1
                        stats.total_records += len(products_list)
                        logger.info(f"[{name}] 第{page_num}页: {len(products_list)} 个商品")
                    else:
                        stats.failed_pages += 1
                        if status != 200:
                            logger.warning(f"[{name}] 第{page_num}页 HTTP {status}, 停止翻页")
                        stopped = True
                        break

                    # 补充新任务
                    if not stopped and next_page_idx < len(pending_pages) and not self._waf_stop.is_set():
                        next_num = pending_pages[next_page_idx]
                        next_page_url = self._make_page_url(url_template, next_num) if url_template else None
                        if next_page_url:
                            fut2 = executor.submit(self._fetch_and_parse_page, name, url, next_num, next_page_url)
                            in_flight[fut2] = (next_num, next_page_url)
                            stats.requested_pages += 1
                            next_page_idx += 1

                    break  # 只处理一个完成的任务, 回到外层循环

            # 取消剩余未开始的任务
            if stopped or self._waf_stop.is_set():
                for fut in list(in_flight.keys()):
                    fut.cancel()

        stats.end_time = time.time()
        logger.info(f"[{name}] 完成: 成功 {stats.success_pages}/{stats.requested_pages} 页, "
                    f"商品 {stats.total_records} 条, 耗时 {stats.elapsed:.1f}s")
        return stats

    def _make_page_url(self, template: Optional[tuple], page_num: int) -> Optional[str]:
        """根据 URL 模板生成页码 URL"""
        if not template:
            return None
        prefix, suffix = template
        return f"{prefix}{page_num}{suffix}"

    def _fetch_and_parse_page(self, name: str, base_url: str, page_num: int,
                              url: str) -> tuple:
        """获取并解析单个页面 (线程池任务)"""
        html, status = self._fetch_page(url, name=name, page_num=page_num)

        waf = detect_waf_block(html or "", status, url, category=name, page_num=page_num)
        if waf:
            return (None, status, waf)

        if status != 200 or not html:
            return (None, status, None)

        if not page_has_products(html):
            return ([], 200, None)

        if self._is_duplicate_page(name, html):
            logger.info(f"[{name}] 第{page_num}页内容与同类目前页重复, 停止")
            return ([], 200, None)

        products = parse_product_listing(html, category_name=name, category_url=base_url,
                                         page_url=url, page_number=page_num)
        return (products, 200, None)

    # ================================================================
    # 所有类目调度
    # ================================================================
    def crawl_all_categories(self, categories: list[dict],
                             max_pages: Optional[int] = None) -> dict:
        """并发抓取所有类目"""
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
    # WAF 阻断处理
    # ================================================================
    def _handle_waf_block(self, waf: WafBlockError, name: str, page_num: int,
                          url: str, html: str):
        """处理 WAF 阻断: 设置全局停止, 打印提示, 保存诊断"""
        self._waf_stop.set()

        saved_count = self.exporters.get_product_count()
        timestamp = datetime.now(timezone.utc).isoformat()

        # 写入 errors.csv
        self._log_error(name, page_num, url, f"WAF_{waf.block_type}",
                       waf.status_code, detail=waf.evidence)

        # 诊断目录
        diag_dir = os.path.join(self.output_dir, "diagnostics")
        os.makedirs(diag_dir, exist_ok=True)

        diagnostic = {
            "status": "waf_blocked",
            "timestamp": timestamp,
            "category": name, "page": page_num, "url": url,
            "http_status": waf.status_code,
            "block_type": waf.block_type,
            "evidence": waf.evidence,
            "products_saved": saved_count,
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

        # 醒目的中文提示
        sys_mod = __import__('sys')
        print("\n" + "!" * 60, file=sys_mod.stderr)
        print("  检测到eMAG验证码、WAF或访问限制，任务已停止。", file=sys_mod.stderr)
        print("  请手动处理访问验证或等待限制解除后重新执行。", file=sys_mod.stderr)
        print("!" * 60, file=sys_mod.stderr)
        print(f"  HTTP 状态码: {waf.status_code}", file=sys_mod.stderr)
        print(f"  类目: {name}", file=sys_mod.stderr)
        print(f"  页码: {page_num}", file=sys_mod.stderr)
        print(f"  URL: {url}", file=sys_mod.stderr)
        print(f"  阻断类型: {waf.block_type}", file=sys_mod.stderr)
        print(f"  发生时间: {timestamp}", file=sys_mod.stderr)
        print(f"  已保存商品数: {saved_count}", file=sys_mod.stderr)
        print(f"  输出目录: {self.output_dir}", file=sys_mod.stderr)
        print(f"  诊断文件: {diag_path}", file=sys_mod.stderr)
        print("!" * 60 + "\n", file=sys_mod.stderr)

    # ================================================================
    # 图片下载
    # ================================================================
    def download_images_for_products(self) -> dict[str, str]:
        """为全局唯一商品下载主图, 返回 {product_key: local_path}"""
        if not self.image_downloader:
            return {}
        all_products = self.exporters.get_products_sorted()
        return self.image_downloader.download_batch(all_products)

    # ================================================================
    # 错误记录
    # ================================================================
    def _log_error(self, name: str, page: int, url: str, error_type: str,
                   http_status: int = 0, retries: int = 0, detail: str = ""):
        error_data = {
            "时间": datetime.now(timezone.utc).isoformat(),
            "类目": name, "页码": page, "URL": url,
            "错误类型": error_type, "HTTP状态码": http_status,
            "重试次数": retries, "错误详情": detail,
        }
        with self._error_lock:
            write_errors_csv(self.errors_file, error_data,
                           write_header=not self._error_header_written)
            if not self._error_header_written:
                self._error_header_written = True

    # ================================================================
    # 完成导出
    # ================================================================
    def finalize(self) -> dict:
        """完成抓取, 导出所有文件并返回汇总"""
        total_elapsed = time.time() - self.start_time

        # 关闭所有 Sessions
        self._close_all_sessions()

        # 下载图片
        image_stats = {"success": 0, "failed": 0}
        path_map: dict[str, str] = {}
        if self.download_images and self.image_downloader:
            path_map = self.download_images_for_products()
            image_stats = self.image_downloader.get_stats()

        # 回填图片本地路径到商品 (使用统一 product_key)
        if path_map:
            with self.exporters._lock:
                for item in self.exporters._products:
                    key = get_product_key(item)
                    if key in path_map:
                        item["main_image_local_path"] = path_map[key]

        # 统计
        sorted_prods = self.exporters.get_products_sorted()
        total_records = len(sorted_prods)
        unique_keys = set()
        for item in sorted_prods:
            unique_keys.add(get_product_key(item))

        # 每类目标统计
        with self._stats_lock:
            for name, stats in self.stats.items():
                cat_keys = set()
                cat_count = 0
                for item in sorted_prods:
                    if item.get("category_name") == name:
                        cat_count += 1
                        cat_keys.add(get_product_key(item))
                stats.total_records = cat_count
                stats.unique_products = len(cat_keys)
                # 每类目的图片统计 (通过商品本地路径)
                img_ok = 0
                img_fail = 0
                for item in sorted_prods:
                    if item.get("category_name") == name:
                        if item.get("main_image_local_path"):
                            img_ok += 1
                        elif item.get("main_image_url"):
                            img_fail += 1
                stats.image_success = img_ok
                stats.image_failed = img_fail

        waf_detected = self._waf_stop.is_set()

        # 确保 errors.csv 存在
        ensure_errors_csv(self.errors_file)

        # 导出
        self.exporters.finalize()

        summary = {
            "version": "2.0.2",
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
