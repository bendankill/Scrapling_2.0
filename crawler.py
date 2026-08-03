"""
爬虫核心：页面获取、分页遍历、并发控制
"""
import json
import logging
import os
import re
import time
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock, Semaphore
from typing import Optional
from urllib.parse import urljoin

from scrapling.fetchers import StealthyFetcher

from models import ProductItem
from parser import parse_product_listing, extract_next_page, page_has_products
from image_downloader import ImageDownloader
from exporters import Exporters
from utils import write_jsonl, write_errors_csv

logger = logging.getLogger("emag_crawler.crawler")

# User-Agent for image downloads
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"


class CategoryStats:
    """单个类目的抓取统计"""

    def __init__(self, name: str):
        self.name = name
        self.requested_pages = 0
        self.success_pages = 0
        self.failed_pages = 0
        self.total_records = 0
        self.unique_products = 0
        self.image_success = 0
        self.image_failed = 0
        self.start_time = 0.0
        self.end_time = 0.0

    @property
    def elapsed(self) -> float:
        return self.end_time - self.start_time if self.end_time else 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
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
    """eMAG 商品列表爬虫"""

    def __init__(
        self,
        output_dir: str,
        image_downloader: Optional[ImageDownloader] = None,
        page_workers: int = 4,
        category_workers: int = 2,
        max_in_flight: int = 8,
        download_images: bool = True,
        log_level: str = "INFO",
    ):
        self.output_dir = output_dir
        self.download_images = download_images
        self.image_downloader = image_downloader
        self.page_workers = page_workers
        self.category_workers = category_workers
        self.global_semaphore = Semaphore(max_in_flight)

        # 导出器
        self.exporters = Exporters(output_dir)

        # 统计
        self.stats: dict[str, CategoryStats] = {}
        self._stats_lock = Lock()

        # 已访问 URL（防重复）
        self._visited_urls: set = set()
        self._url_lock = Lock()

        # 图片路径映射: {pnk_or_id: local_path}
        self._image_paths: dict = {}

        # 错误日志
        self.errors_file = os.path.join(output_dir, "errors.csv")
        self._error_header_written = False

        # 启动时间
        self.start_time = time.time()

    def crawl_category(
        self,
        name: str,
        url: str,
        max_pages: Optional[int] = None,
    ) -> CategoryStats:
        """
        抓取单个类目

        Args:
            name: 类目名称
            url: 类目列表页 URL
            max_pages: 最大抓取页数（None 表示不限）

        Returns:
            CategoryStats 统计信息
        """
        stats = CategoryStats(name)
        stats.start_time = time.time()

        with self._stats_lock:
            self.stats[name] = stats

        logger.info(f"[{name}] 开始抓取: {url} (页数限制: {max_pages or '不限'})")
        stats.requested_pages = max_pages or 0

        current_url = url
        visited_in_category: set = set()
        prev_html_hash = ""

        for page_num in range(1, (max_pages or 999999) + 1):
            # 检查是否超过页数限制
            if max_pages and page_num > max_pages:
                logger.info(f"[{name}] 已达到页数限制 ({max_pages} 页)，停止")
                break

            # 检查是否重复 URL
            with self._url_lock:
                if current_url in self._visited_urls:
                    logger.info(f"[{name}] 第 {page_num} 页 URL 已访问过，停止")
                    break

            logger.info(f"[{name}] 正在抓取第 {page_num} 页: {current_url}")

            try:
                # 获取页面
                with self.global_semaphore:
                    html, http_status = self._fetch_page(current_url)

                if http_status != 200 or not html:
                    stats.failed_pages += 1
                    self._log_error(
                        name=name,
                        page=page_num,
                        url=current_url,
                        error_type="HTTP_ERROR",
                        http_status=http_status,
                        detail=f"HTTP {http_status}",
                    )
                    logger.warning(f"[{name}] 第 {page_num} 页获取失败 (HTTP {http_status})")
                    break

                # 检查页面内容是否重复
                import hashlib
                html_hash = hashlib.md5(html.encode()).hexdigest()
                if html_hash == prev_html_hash:
                    logger.info(f"[{name}] 第 {page_num} 页内容与上一页相同，停止")
                    break
                prev_html_hash = html_hash

                # 检查是否有商品
                if not page_has_products(html):
                    logger.info(f"[{name}] 第 {page_num} 页没有商品，停止")
                    break

                # 解析商品
                products = parse_product_listing(
                    html,
                    category_name=name,
                    category_url=url,
                    page_url=current_url,
                    page_number=page_num,
                )

                if not products:
                    logger.info(f"[{name}] 第 {page_num} 页解析到 0 个商品，停止")
                    break

                # 写入导出（同时写入 JSONL）
                self.exporters.add_products(products)

                stats.success_pages += 1
                stats.total_records += len(products)

                logger.info(f"[{name}] 第 {page_num} 页: 提取 {len(products)} 个商品")

                # 标记已访问
                with self._url_lock:
                    self._visited_urls.add(current_url)
                visited_in_category.add(current_url)

                # 提取下一页
                next_url = extract_next_page(html, current_url)
                if not next_url:
                    logger.info(f"[{name}] 没有下一页，类目抓取完成")
                    break

                current_url = next_url

            except Exception as e:
                stats.failed_pages += 1
                self._log_error(
                    name=name,
                    page=page_num,
                    url=current_url,
                    error_type=type(e).__name__,
                    detail=str(e)[:500],
                )
                logger.error(f"[{name}] 第 {page_num} 页抓取异常: {e}")
                break

        stats.end_time = time.time()
        logger.info(
            f"[{name}] 类目抓取完成: "
            f"成功 {stats.success_pages}/{stats.requested_pages or '?'} 页, "
            f"商品 {stats.total_records} 条, "
            f"耗时 {stats.elapsed:.1f}s"
        )
        return stats

    def crawl_all_categories(
        self,
        categories: list[dict],
        max_pages: Optional[int] = None,
    ) -> dict:
        """并发抓取所有启用的类目"""
        enabled = [c for c in categories if c.get("enabled", True)]
        if not enabled:
            logger.warning("没有启用的类目")
            return {}

        logger.info(f"共 {len(enabled)} 个启用类目，类目并发数: {self.category_workers}")

        results = {}
        if self.category_workers <= 1:
            # 单线程顺序抓取
            for cat in enabled:
                results[cat["name"]] = self.crawl_category(
                    cat["name"], cat["url"], max_pages
                )
        else:
            # 并发抓取各类目
            with ThreadPoolExecutor(max_workers=self.category_workers) as executor:
                futures = {}
                for cat in enabled:
                    future = executor.submit(
                        self.crawl_category, cat["name"], cat["url"], max_pages
                    )
                    futures[future] = cat["name"]

                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results[name] = future.result()
                    except Exception as e:
                        logger.error(f"类目 [{name}] 抓取失败: {e}")

        return results

    def download_images_for_products(self) -> dict:
        """为全局唯一商品下载主图"""
        if not self.image_downloader:
            return {}

        # 收集所有商品中的唯一图片 URL
        products = []
        # 从已有的 CSV buffer 收集
        seen_pnks = set()
        for item in self.exporters._csv_buffer:
            pnk = item.get("pnk", "") or item.get("product_id", "")
            img_url = item.get("main_image_url", "")
            if pnk and pnk not in seen_pnks and img_url:
                seen_pnks.add(pnk)
                # 构造临时 ProductItem
                p = ProductItem(
                    pnk=pnk,
                    product_id=item.get("product_id", ""),
                    main_image_url=img_url,
                    title=item.get("title", ""),
                )
                products.append(p)

        logger.info(f"准备下载 {len(products)} 个唯一商品的图片")
        return self.image_downloader.download_batch(products)

    def _fetch_page(self, url: str) -> tuple[Optional[str], int]:
        """
        使用 Scrapling StealthyFetcher 获取页面
        返回 (html_content, http_status)
        """
        try:
            page = StealthyFetcher.fetch(url, timeout=60000)
            return page.html_content, page.status
        except Exception as e:
            logger.error(f"StealthyFetcher 获取页面失败 [{url}]: {e}")
            return None, 0

    def _log_error(
        self,
        name: str,
        page: int,
        url: str,
        error_type: str,
        http_status: int = 0,
        retries: int = 0,
        detail: str = "",
    ) -> None:
        """记录错误到 errors.csv"""
        error_data = {
            "时间": datetime.now(timezone.utc).isoformat(),
            "类目": name,
            "页码": page,
            "URL": url,
            "错误类型": error_type,
            "HTTP状态码": http_status,
            "重试次数": retries,
            "错误详情": detail,
        }

        write_errors_csv(
            self.errors_file,
            error_data,
            write_header=not self._error_header_written,
        )
        if not self._error_header_written:
            self._error_header_written = True

    def finalize(self) -> dict:
        """完成抓取，导出所有文件并返回汇总数据"""
        total_elapsed = time.time() - self.start_time

        # 下载图片
        image_stats = {"success": 0, "failed": 0}
        if self.download_images and self.image_downloader:
            path_map = self.download_images_for_products()
            image_stats = self.image_downloader.get_stats()

            # 更新商品的本地路径
            for item in self.exporters._csv_buffer:
                key = item.get("pnk", "") or item.get("product_id", "")
                if key in path_map:
                    item["main_image_local_path"] = path_map[key]

        # 统计唯一商品数
        unique_ids = set()
        for item in self.exporters._csv_buffer:
            uid = item.get("pnk", "") or item.get("product_id", "")
            if uid:
                unique_ids.add(uid)

        total_records = self.exporters.get_product_count()
        unique_count = len(unique_ids)

        # 更新各类目的唯一商品数
        with self._stats_lock:
            for name, stats in self.stats.items():
                # 统计该类目的唯一商品
                cat_pnks = set()
                for item in self.exporters._csv_buffer:
                    if item.get("category_name") == name:
                        uid = item.get("pnk", "") or item.get("product_id", "")
                        if uid:
                            cat_pnks.add(uid)
                stats.unique_products = len(cat_pnks)
                stats.image_success = image_stats["success"]
                stats.image_failed = image_stats["failed"]

        # 导出文件
        self.exporters.finalize()

        # 汇总
        summary = {
            "version": "2.0.0",
            "start_time": datetime.fromtimestamp(self.start_time, tz=timezone.utc).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(total_elapsed, 2),
            "categories": [s.to_dict() for s in self.stats.values()],
            "totals": {
                "total_records": total_records,
                "unique_products": unique_count,
                "image_download_success": image_stats["success"],
                "image_download_failed": image_stats["failed"],
                "success_pages": sum(s.success_pages for s in self.stats.values()),
                "failed_pages": sum(s.failed_pages for s in self.stats.values()),
            },
        }

        # 写入 run_summary.json
        summary_path = os.path.join(self.output_dir, "run_summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        logger.info(f"汇总已写入: {summary_path}")

        return summary
