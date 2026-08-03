"""
爬虫核心：纯 HTTP 页面获取、分页遍历、并发控制、Captcha 检测
"""
import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError
from datetime import datetime, timezone
from threading import Lock, Semaphore, Event
from typing import Optional
from urllib.parse import urljoin

from scrapling.fetchers import Fetcher, FetcherSession

from models import ProductItem
from parser import parse_product_listing, extract_next_page, page_has_products
from image_downloader import ImageDownloader
from exporters import Exporters
from utils import (
    detect_captcha, CaptchaRequiredError, get_product_key,
    write_errors_csv, write_atomic_json,
    EXIT_CAPTCHA,
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
            "name": self.name,
            "url": self.url,
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
    """eMAG 商品列表爬虫 — 纯 HTTP 模式"""

    def __init__(
        self,
        output_dir: str,
        image_downloader: Optional[ImageDownloader] = None,
        page_workers: int = 4,
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

        # 全局并发控制: 页面+图片共享同一个 Semaphore
        self.global_semaphore = Semaphore(max_in_flight)

        self.exporters = Exporters(output_dir)
        self.stats: dict[str, CategoryStats] = {}
        self._stats_lock = Lock()

        self.errors_file = os.path.join(output_dir, "errors.csv")
        self._error_header_written = False
        self._error_lock = Lock()

        self.start_time = time.time()

        # Captcha 终止标志
        self._captcha_stop = Event()

        # FetcherSession 配置 (每次创建新会话以支持并发)
        self._session_config = {
            "impersonate": "chrome136",
            "stealthy_headers": True,
            "timeout": 30,
            "retries": 3,
            "retry_delay": 1,
        }

    def crawl_category(
        self, name: str, url: str, max_pages: Optional[int] = None
    ) -> CategoryStats:
        """
        顺序抓取单个类目（支持页面并发）。
        如果 max_pages 为 None 且 all_pages 为 True, 持续翻页直到无下一页。
        """
        stats = CategoryStats(name, url)
        with self._stats_lock:
            self.stats[name] = stats

        logger.info(f"[{name}] 开始抓取: {url} (页数: {max_pages or '全部'})")

        # 第一步: 获取首页, 确认可访问且无验证码
        first_html, first_status = self._fetch_page(url, name=name, page_num=1)
        stats.requested_pages += 1

        if first_status != 200 or not first_html:
            stats.failed_pages += 1
            self._log_error(name, 1, url, "HTTP_ERROR", first_status, detail=f"HTTP {first_status}")
            stats.end_time = time.time()
            return stats

        # Captcha 检测
        captcha = detect_captcha(first_html, first_status, url)
        if captcha:
            self._handle_captcha(captcha, name, 1, url, first_html)
            stats.end_time = time.time()
            return stats

        # 检查商品
        if not page_has_products(first_html):
            logger.info(f"[{name}] 第1页没有商品")
            stats.end_time = time.time()
            return stats

        # 解析首页商品
        products = parse_product_listing(
            first_html, category_name=name, category_url=url,
            page_url=url, page_number=1,
        )
        self.exporters.add_products(products)
        stats.success_pages += 1
        stats.total_records += len(products)
        logger.info(f"[{name}] 第1页: {len(products)} 个商品")

        # 提取下一页
        next_url = extract_next_page(first_html, url)
        if not next_url:
            logger.info(f"[{name}] 只有1页")
            stats.end_time = time.time()
            return stats

        # 第二步: 生成后续页面URL列表 (用于并发)
        if max_pages is not None:
            remaining = max_pages - 1
            if remaining <= 0:
                stats.end_time = time.time()
                return stats
        else:
            remaining = 99  # 全量模式下的安全上限 (eMAG 最多 ~100 页)

        page_urls = self._build_page_urls(name, url, next_url, remaining)

        if not page_urls:
            stats.end_time = time.time()
            return stats

        # 第三步: 并发获取其余页面
        if self.page_workers > 1 and len(page_urls) > 1:
            self._crawl_pages_concurrent(name, url, page_urls, stats)
        else:
            self._crawl_pages_sequential(name, url, page_urls, stats)

        stats.end_time = time.time()
        logger.info(
            f"[{name}] 完成: 成功 {stats.success_pages}/{stats.requested_pages} 页, "
            f"商品 {stats.total_records} 条, 耗时 {stats.elapsed:.1f}s"
        )
        return stats

    def _build_page_urls(
        self, name: str, base_url: str, next_url_template: str, remaining: int
    ) -> list[tuple[int, str]]:
        """根据首页的下一页URL模板，生成后续页面URL列表"""
        import re
        urls = []

        # 从 next_url 中提取页码模式: /mouse/p2/c
        match = re.match(r'(.*?/p)(\d+)(/c.*)', next_url_template)
        if match:
            prefix, start_page, suffix = match.group(1), int(match.group(2)), match.group(3)
            for i in range(remaining):
                page_num = start_page + i
                urls.append((page_num, f"{prefix}{page_num}{suffix}"))
        else:
            # 不能从模板生成，使用简单规则
            current = next_url_template
            for page_num in range(2, remaining + 2):
                urls.append((page_num, current))
                break  # 无法可靠生成后续URL

        return urls[:remaining]

    def _crawl_pages_concurrent(
        self, name: str, base_url: str, page_urls: list[tuple[int, str]], stats: CategoryStats
    ):
        """并发获取页面"""
        visited = {base_url}

        with ThreadPoolExecutor(max_workers=self.page_workers) as executor:
            futures = {}
            for page_num, url in page_urls:
                if self._captcha_stop.is_set():
                    break
                future = executor.submit(self._fetch_and_parse_page, name, base_url, page_num, url, visited)
                futures[future] = (page_num, url)
                stats.requested_pages += 1

            for future in as_completed(futures):
                if self._captcha_stop.is_set():
                    # 取消剩余任务
                    for f in futures:
                        f.cancel()
                    break

                page_num, url = futures[future]
                try:
                    products, status, captcha_err = future.result()
                    if captcha_err:
                        self._handle_captcha(captcha_err, name, page_num, url, "")
                        self._captcha_stop.set()
                        break

                    if status == 200 and products:
                        self.exporters.add_products(products)
                        stats.success_pages += 1
                        stats.total_records += len(products)
                        visited.add(url)
                        logger.info(f"[{name}] 第{page_num}页: {len(products)} 个商品")
                    else:
                        stats.failed_pages += 1
                        if products is None and status != 200:
                            logger.warning(f"[{name}] 第{page_num}页获取失败 (HTTP {status})")
                            break  # HTTP 错误，停止翻页
                        elif not products:
                            logger.info(f"[{name}] 第{page_num}页无商品，停止翻页")
                            break

                except Exception as e:
                    stats.failed_pages += 1
                    self._log_error(name, page_num, url, type(e).__name__, detail=str(e)[:500])
                    logger.error(f"[{name}] 第{page_num}页异常: {e}")

    def _crawl_pages_sequential(
        self, name: str, base_url: str, page_urls: list[tuple[int, str]], stats: CategoryStats
    ):
        """顺序获取页面"""
        for page_num, url in page_urls:
            if self._captcha_stop.is_set():
                break

            stats.requested_pages += 1
            products, status, captcha_err = self._fetch_and_parse_page(name, base_url, page_num, url, set())

            if captcha_err:
                self._handle_captcha(captcha_err, name, page_num, url, "")
                self._captcha_stop.set()
                break

            if status == 200 and products:
                self.exporters.add_products(products)
                stats.success_pages += 1
                stats.total_records += len(products)
                logger.info(f"[{name}] 第{page_num}页: {len(products)} 个商品")
            else:
                stats.failed_pages += 1
                if status != 200:
                    break
                if not products:
                    break

    def _fetch_and_parse_page(
        self, name: str, base_url: str, page_num: int, url: str, visited: set
    ) -> tuple:
        """获取并解析单个页面 (供并发使用)"""
        if url in visited:
            return ([], 200, None)

        html, status = self._fetch_page(url, name=name, page_num=page_num)
        if status != 200 or not html:
            return (None, status, None)

        captcha = detect_captcha(html, status, url)
        if captcha:
            captcha.category = name
            captcha.page = page_num
            captcha.url = url
            return (None, status, captcha)

        if not page_has_products(html):
            return ([], 200, None)

        # 检查内容重复
        html_hash = hashlib.md5(html.encode()).hexdigest()
        if hasattr(self, '_last_hash') and html_hash == self._last_hash:
            return ([], 200, None)
        self._last_hash = html_hash

        products = parse_product_listing(
            html, category_name=name, category_url=base_url,
            page_url=url, page_number=page_num,
        )
        return (products, 200, None)

    def crawl_all_categories(
        self, categories: list[dict], max_pages: Optional[int] = None
    ) -> dict:
        """并发抓取所有类目"""
        if not categories:
            logger.warning("没有启用的类目")
            return {}

        logger.info(f"共 {len(categories)} 个类目, 并发数: {self.category_workers}")

        results = {}
        if self.category_workers <= 1:
            for cat in categories:
                if self._captcha_stop.is_set():
                    break
                results[cat["name"]] = self.crawl_category(cat["name"], cat["url"], max_pages)
        else:
            with ThreadPoolExecutor(max_workers=self.category_workers) as executor:
                futures = {}
                for cat in categories:
                    future = executor.submit(
                        self.crawl_category, cat["name"], cat["url"], max_pages
                    )
                    futures[future] = cat["name"]

                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results[name] = future.result()
                    except Exception as e:
                        logger.error(f"类目 [{name}] 异常: {e}")

        return results

    def download_images_for_products(self) -> dict[str, str]:
        """为全局唯一商品下载主图"""
        if not self.image_downloader:
            return {}

        all_products = self.exporters.get_products_sorted()
        return self.image_downloader.download_batch(all_products)

    def _fetch_page(self, url: str, name: str = "", page_num: int = 0) -> tuple[Optional[str], int]:
        """使用 Scrapling 纯 HTTP FetcherSession 获取页面 (每次新建会话以支持并发)"""
        with self.global_semaphore:
            try:
                with FetcherSession(**self._session_config) as s:
                    page = s.get(url)
                return page.html_content, page.status
            except Exception as e:
                logger.error(f"HTTP 请求失败 [{url}]: {e}")
                return None, 0

    def _handle_captcha(
        self, captcha: CaptchaRequiredError, name: str, page_num: int, url: str, html: str
    ):
        """处理验证码检测，保存诊断信息，设置终止标志"""
        self._captcha_stop.set()

        # 保存诊断信息
        diag_dir = os.path.join(self.output_dir, "diagnostics")
        os.makedirs(diag_dir, exist_ok=True)

        diagnostic = {
            "status": "captcha_required",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": name,
            "page": page_num,
            "url": url,
            "http_status": captcha.status_code,
            "captcha_type": captcha.captcha_type,
            "evidence": captcha.evidence,
        }
        diag_path = os.path.join(diag_dir, "captcha_diagnostic.json")
        with open(diag_path, "w", encoding="utf-8") as f:
            json.dump(diagnostic, f, ensure_ascii=False, indent=2)

        # 保存脱敏的响应 HTML (去掉 Cookie/Token)
        if html:
            sanitized = html
            # 移除敏感头
            import re
            sanitized = re.sub(r'apiKey["\']?\s*[:=]\s*["\'][^"\']+["\']', 'apiKey="[REDACTED]"', sanitized)
            sanitized = re.sub(r'cookieValue=([^&\s"\'<>]+)', 'cookieValue=[REDACTED]', sanitized)
            html_path = os.path.join(diag_dir, "captcha_response.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(sanitized)

        # 醒目的中文提示
        saved_count = self.exporters.get_product_count()
        print("\n" + "!" * 60, file=__import__('sys').stderr)
        print("  检测到验证码或网站人工验证！", file=__import__('sys').stderr)
        print("!" * 60, file=__import__('sys').stderr)
        print(f"  HTTP 状态码: {captcha.status_code}", file=__import__('sys').stderr)
        print(f"  类目: {name}", file=__import__('sys').stderr)
        print(f"  页码: {page_num}", file=__import__('sys').stderr)
        print(f"  URL: {url}", file=__import__('sys').stderr)
        print(f"  检测类型: {captcha.captcha_type}", file=__import__('sys').stderr)
        print(f"  已保存商品数: {saved_count}", file=__import__('sys').stderr)
        print(f"  输出目录: {self.output_dir}", file=__import__('sys').stderr)
        print(f"  诊断文件: {diag_path}", file=__import__('sys').stderr)
        print("", file=__import__('sys').stderr)
        print("  程序已停止，没有继续抓取。", file=__import__('sys').stderr)
        print("  请使用正常浏览器在同一网络环境下打开上述URL，", file=__import__('sys').stderr)
        print("  完成人工验证后，再重新执行脚本。", file=__import__('sys').stderr)
        print("  如果重新执行仍然出现验证码，说明当前纯HTTP方式无法继续，", file=__import__('sys').stderr)
        print("  请不要反复高频重试。", file=__import__('sys').stderr)
        print("!" * 60 + "\n", file=__import__('sys').stderr)

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

    def finalize(self) -> dict:
        """完成抓取，导出所有文件并返回汇总数据"""
        total_elapsed = time.time() - self.start_time

        # 完成导出, 关闭资源
        try:
            if self.image_downloader:
                pass  # closed in main.py
        except Exception:
            pass

        # 下载图片
        image_stats = {"success": 0, "failed": 0}
        path_map: dict[str, str] = {}
        if self.download_images and self.image_downloader:
            path_map = self.download_images_for_products()
            image_stats = self.image_downloader.get_stats()

        # 回填图片路径到已保存商品 (使用统一 product_key)
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
            k = get_product_key(item)
            unique_keys.add(k)

        # 各类目标统计
        with self._stats_lock:
            for name, stats in self.stats.items():
                cat_keys = set()
                cat_count = 0
                for item in sorted_prods:
                    if item.get("category_name") == name:
                        cat_count += 1
                        k = get_product_key(item)
                        cat_keys.add(k)
                stats.unique_products = len(cat_keys)
                # 各类目的图片统计 (暂不拆分, 只记录全局)
                stats.image_success = 0
                stats.image_failed = 0
                if name in self.stats:
                    pass

        # 判断是否验证码终止
        captcha_detected = self._captcha_stop.is_set()

        # 导出
        self.exporters.finalize()

        summary = {
            "version": "2.0.1",
            "status": "captcha_required" if captcha_detected else "completed",
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
